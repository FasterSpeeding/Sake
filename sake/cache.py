from __future__ import annotations

__all__: typing.Final[typing.Sequence[str]] = [
    "ResourceClient",
    "EmojiCache",
    "FullCache",
    "GuildCache",
    "MeCache",
    "RoleCache",
    "UserCache",
]

import abc
import asyncio
import enum
import logging
import typing

import aioredis
from hikari import guilds
from hikari import snowflakes
from hikari import users
from hikari.events import guild_events
from hikari.events import member_events
from hikari.events import message_events
from hikari.events import role_events
from hikari.events import shard_events
from hikari.events import user_events

from sake import conversion
from sake import errors
from sake import iterators
from sake import traits

if typing.TYPE_CHECKING:
    import ssl as ssl_
    import types

    import aioredis.abc
    from hikari import emojis as emojis_
    from hikari import messages
    from hikari import traits as hikari_traits


_LOGGER: typing.Final[logging.Logger] = logging.getLogger("hikari.sake")
"""Type-Hint The logger instance used by this sake implementation."""
ResourceT = typing.TypeVar("ResourceT", bound="ResourceClient")
"""Type-Hint A type hint used to represent a resource client instance."""
ValueT = typing.TypeVar("ValueT")


class ResourceIndex(enum.IntEnum):
    """An enum of the indexes used to map cache resources to their redis databases."""

    EMOJI = 0
    GUILD = 1
    GUILD_CHANNEL = 2
    INVITE = 3
    MEMBER = 4
    PRESENCE = 5
    ROLE = 6
    USER = 7
    VOICE_STATE = 8
    GUILD_REFERENCE = 9
    #  This is a special case database solely used for linking other entries to their relevant guilds.
    MESSAGE = 10


async def _close_client(client: aioredis.Redis) -> None:
    await client.close()


# TODO: may go back to approach where client logic and interface are separate classes
class ResourceClient(traits.Resource, abc.ABC):
    """A base client which all resources in this implementation will implement.

    !!! note
        This cannot be initialised by itself and is useless alone.

    Parameters
    ----------
    rest : hikari.traits.RESTAware
        The REST aware Hikari client to bind this resource client to.
    address : typing.Union[str, typing.Tuple[str, typing.Union[str, int]]
        The address to use to connect to the Redis backend server this
        resource is linked to. This may either be a string url in the form
        of `"redis://localhost:4242"` or a tuple of an address to a port
        in the form of `("localhost", 4242)`.

    Other Parameters
    ----------------
    dispatch : typing.Optional[hikari.traits.DispatchAware]
        The dispatcher aware Hikari client to bind this resource client to.
        This can be left as `builtins.None` to avoid this client from
        automatically registering any event listeners.
    password : typing.Optional[str]
        The password to optionally use to connect ot the backend Redis
        server.
    ssl : typing.Union[ssl.SSLContext, builtins.bool, builtins.None]
        The SSL context to use when connecting to the Redis backend server,
        this may be a context object, bool value or None to leave default
        behaviour (which will likely be no SSL).
    """

    __slots__: typing.Sequence[str] = (
        "_address",
        "_clients",
        "_dispatch",
        "_password",
        "_rest",
        "_ssl",
        "_started",
    )

    def __init__(
        self,
        rest: hikari_traits.RESTAware,
        dispatch: typing.Optional[hikari_traits.DispatcherAware] = None,
        *,
        address: typing.Union[str, typing.Tuple[str, typing.Union[str, int]]],
        password: typing.Optional[str] = None,
        ssl: typing.Union[ssl_.SSLContext, bool, None] = None,
    ) -> None:
        self._address = address
        self._dispatch = dispatch
        self._clients: typing.MutableMapping[ResourceIndex, aioredis.Redis] = {}
        self._password = password
        self._rest = rest
        self._ssl = ssl
        self._started = False

    async def __aenter__(self: ResourceT) -> ResourceT:
        await self.open()
        return self

    async def __aexit__(
        self, exc_type: typing.Type[Exception], exc_val: Exception, exc_tb: types.TracebackType
    ) -> None:
        await self.close()

    def __enter__(self) -> typing.NoReturn:
        # This is async only.
        cls = type(self)
        raise TypeError(f"{cls.__module__}.{cls.__qualname__} is async-only, did you mean 'async with'?") from None

    def __exit__(self, exc_type: typing.Type[Exception], exc_val: Exception, exc_tb: types.TracebackType) -> None:
        return None

    @classmethod
    @abc.abstractmethod  # TODO: should this return a sequence?
    def index(cls) -> ResourceIndex:
        """The index for the resource which this class is linked to.

        !!! note
            This should be called on specific base classes and will not be
            accurate after inheritance.

        Returns
        -------
        ResourceIndex
            The index of the resource this class is linked to.
        """
        raise NotImplementedError

    @property  # As a note, this will only be set if this is actively hooked into event dispatchers
    def dispatch(self) -> typing.Optional[hikari_traits.DispatcherAware]:
        """The dispatcher aware client this resource client is tied to, if set.

        !!! note
            If this is set then event listeners will be (de)registered
            when this resource is opened/closed.

        Returns
        -------
        typing.Optional[hikari.traits.DispatcherAware]
            The dispatcher aware client this resource is tied to if set,
            else `builtins.None`.
        """
        return self._dispatch

    @property  # unlike here where this is 100% required for building models.
    def rest(self) -> hikari_traits.RESTAware:
        """The REST aware client this resource client is tied to.

        This is used to build models with a `app` attribute.

        Returns
        -------
        hikari.traits.RESTAware
            The REST aware client this resource is tied to.
        """
        return self._rest

    async def get_connection(self, resource: ResourceIndex) -> aioredis.Redis:
        """Get the connection for a specific resource.

        Parameters
        ----------
        resource : ResourceIndex
            The index of the resource to get a connection for.

        Returns
        -------
        aioredis.Redis
            The connection instance for the specified resource.

        Raises
        ------
        TypeError
            When this method is called on a closed client.
        ValueError
            When you pass an invalid resource for the client.
        """
        if not self._started:
            raise TypeError("Cannot use an inactive client")

        try:
            return self._clients[resource]
        except KeyError:
            raise ValueError(f"Resource index `{resource}` is invalid for this client") from None

    def _get_indexes(self) -> typing.MutableSet[ResourceIndex]:
        results = set()
        for cls in type(self).mro():
            if not issubclass(cls, ResourceClient) or cls is ResourceClient:
                continue

            results.add(cls.index())

        return results

    async def get_connection_status(self, resource: ResourceIndex) -> bool:
        """Get the status of the internal connection for a specific resource.

        Parameters
        ----------
        resource : ResourceIndex
            The index of the resource to get the status for.

        Returns
        -------
        bool
            Whether the client has an active connection for the specified resource.
        """
        return resource in self._clients and not self._clients[resource].closed

    async def _spawn_connection(self, resource: ResourceIndex) -> None:
        self._clients[resource] = await aioredis.create_redis_pool(
            address=self._address,
            db=int(resource),
            password=self._password,
            ssl=self._ssl,
            encoding="utf-8",
        )

    async def open(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        if self._started:
            return

        await asyncio.gather(*map(self._spawn_connection, self._get_indexes()))
        self.subscribe_listeners()
        self._started = True

    async def close(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        # We want to ensure that we both only do anything here if the client was already started when the method was
        # originally called and also that the client is marked as "closed" before this starts severing connections.
        was_started = self._started
        self._started = False

        if not was_started:
            return

        self.unsubscribe_listeners()
        clients = self._clients
        self._clients = {}
        await asyncio.gather(*map(_close_client, clients.values()))

    @abc.abstractmethod
    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        return None

    @abc.abstractmethod
    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        return None


class _GuildReference(ResourceClient):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> ResourceIndex:
        # <<Inherited docstring from ResourceClient>>
        return ResourceIndex.GUILD_REFERENCE

    @staticmethod
    def _generate_key(guild_id: snowflakes.Snowflakeish, resource: ResourceIndex) -> str:
        return f"{guild_id}:{int(resource)}"

    async def _add_ids(
        self, guild_id: snowflakes.Snowflakeish, resource: ResourceIndex, *identifiers: conversion.RedisValueT
    ) -> None:
        key = self._generate_key(guild_id, resource)
        client = await self.get_connection(ResourceIndex.GUILD_REFERENCE)
        await client.sadd(key, *identifiers)

    async def _clear_ids(self, resource: ResourceIndex) -> None:
        raise NotImplementedError  # TODO: this

    async def _clear_ids_for_guild(self, guild_id: snowflakes.Snowflakeish, resource: ResourceIndex) -> None:
        key = self._generate_key(guild_id, resource)
        client = await self.get_connection(ResourceIndex.GUILD_REFERENCE)
        await client.delete(key)

    async def _delete_ids(
        self, guild_id: snowflakes.Snowflakeish, resource: ResourceIndex, *identifiers: conversion.RedisValueT
    ) -> None:
        key = self._generate_key(guild_id, resource)
        client = await self.get_connection(ResourceIndex.GUILD_REFERENCE)
        await client.srem(key, *identifiers)

    async def _get_ids(
        self,
        guild_id: snowflakes.Snowflakeish,
        resource: ResourceIndex,
        *,
        cast: typing.Callable[[conversion.RedisValueT], ValueT],
    ) -> typing.Sequence[ValueT]:
        key = self._generate_key(guild_id, resource)
        client = await self.get_connection(ResourceIndex.GUILD_REFERENCE)
        return (*map(cast, await client.smembers(key)),)


class UserCache(ResourceClient, traits.UserCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> ResourceIndex:
        # <<Inherited docstring from ResourceClient>>
        return ResourceIndex.USER

    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        # The users cache is a special case as it doesn't directly map to any events.
        super().subscribe_listeners()

    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        # The users cache is a special case as it doesn't directly map to any events.
        super().unsubscribe_listeners()

    async def clear_users(self) -> None:
        client = await self.get_connection(ResourceIndex.USER)
        await client.flushdb()  # TODO: ref counting

    async def delete_user(self, user_id: snowflakes.Snowflakeish) -> None:
        # <<Inherited docstring from sake.traits.UserCache>>
        client = await self.get_connection(ResourceIndex.USER)
        await client.delete(int(user_id))

    async def get_user(self, user_id: snowflakes.Snowflakeish) -> users.User:
        # <<Inherited docstring from sake.traits.UserCache>>
        client = await self.get_connection(ResourceIndex.USER)
        data = await client.hgetall(int(user_id))

        if not data:
            raise errors.EntryNotFound(f"User entry `{user_id}` not found")

        return conversion.deserialize_user(data, app=self.rest)

    def iter_users(self) -> traits.CacheIterator[users.User]:  # TODO: handle when an entity is removed mid-iteration
        # <<Inherited docstring from sake.traits.UserCache>>
        return iterators.RedisIterator(self, ResourceIndex.USER, lambda id_: self.get_user(snowflakes.Snowflake(id_)))

    async def set_user(self, user: users.User) -> None:
        # <<Inherited docstring from sake.traits.UserCache>>
        client = await self.get_connection(ResourceIndex.USER)
        await client.hmset_dict(int(user.id), conversion.serialize_user(user))


class EmojiCache(UserCache, _GuildReference, traits.EmojiCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> ResourceIndex:
        # <<Inherited docstring from ResourceClient>>
        return ResourceIndex.EMOJI

    async def _bulk_add_emojis(self, emojis: typing.Iterable[emojis_.KnownCustomEmoji]) -> None:
        #  This is generally quicker and less blocking than buffering requests.
        await asyncio.gather(*map(self.set_emoji, emojis))

    async def __on_emojis_update(self, event: guild_events.EmojisUpdateEvent) -> None:
        await self.clear_emojis_for_guild(event.guild_id)
        await self._bulk_add_emojis(event.emojis)

    async def __on_guild_visibility_event(self, event: guild_events.GuildVisibilityEvent) -> None:
        if isinstance(event, (guild_events.GuildAvailableEvent, guild_events.GuildUpdateEvent)):
            await self.clear_emojis_for_guild(event.guild_id)
            await self._bulk_add_emojis(event.emojis.values())

        elif isinstance(event, guild_events.GuildLeaveEvent):
            await self.clear_emojis_for_guild(event.guild_id)

    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().subscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.subscribe(guild_events.EmojisUpdateEvent, self.__on_emojis_update)
            self.dispatch.dispatcher.subscribe(guild_events.GuildVisibilityEvent, self.__on_guild_visibility_event)
            #  TODO: can we also listen for member delete to manage this?

    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().unsubscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.unsubscribe(guild_events.EmojisUpdateEvent, self.__on_emojis_update)
            self.dispatch.dispatcher.unsubscribe(guild_events.GuildVisibilityEvent, self.__on_guild_visibility_event)
            #  TODO: can we also listen for member delete to manage this?

    async def clear_emojis(self) -> None:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        await self._clear_ids(ResourceIndex.EMOJI)
        client = await self.get_connection(ResourceIndex.EMOJI)
        await client.flushdb()

    async def clear_emojis_for_guild(self, guild_id: snowflakes.Snowflakeish) -> None:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        emoji_ids = await self._get_ids(guild_id, ResourceIndex.EMOJI, cast=snowflakes.Snowflake)
        if not emoji_ids:
            return

        await self._clear_ids_for_guild(guild_id, ResourceIndex.EMOJI)
        await asyncio.gather(*map(self.delete_emoji, emoji_ids))

    async def delete_emoji(self, emoji_id: snowflakes.Snowflakeish) -> None:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        client = await self.get_connection(ResourceIndex.EMOJI)
        data = await client.hgetall(int(emoji_id))

        if not data:
            return

        await self._delete_ids(data["guild_id"], ResourceIndex.EMOJI, data["id"])
        await client.delete(int(emoji_id))

    async def get_emoji(self, emoji_id: snowflakes.Snowflakeish) -> emojis_.KnownCustomEmoji:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        client = await self.get_connection(ResourceIndex.EMOJI)
        data = await client.hgetall(int(emoji_id))

        if not data:
            raise errors.EntryNotFound(f"Emoji entry `{emoji_id}` not found")

        user = await self.get_user(int(data["user_id"])) if "user_id" in data else None
        return conversion.deserialize_emoji(data, app=self.rest, user=user)

    def iter_emojis(self) -> traits.CacheIterator[emojis_.KnownCustomEmoji]:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        return iterators.RedisIterator(self, ResourceIndex.EMOJI, lambda id_: self.get_emoji(snowflakes.Snowflake(id_)))

    def iter_emojis_for_guild(
        self, guild_id: snowflakes.Snowflakeish
    ) -> traits.CacheIterator[emojis_.KnownCustomEmoji]:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        return iterators.SpecificRedisIterator(
            lambda: self._get_ids(guild_id, ResourceIndex.EMOJI, cast=snowflakes.Snowflake), self.get_emoji
        )

    async def set_emoji(self, emoji: emojis_.KnownCustomEmoji) -> None:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        client = await self.get_connection(ResourceIndex.EMOJI)
        data = conversion.serialize_emoji(emoji)

        if emoji.user is not None:
            await self.set_user(emoji.user)

        await self._add_ids(emoji.guild_id, ResourceIndex.EMOJI, int(emoji.id))
        await client.hmset_dict(int(emoji.id), data)


class GuildCache(ResourceClient, traits.GuildCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> ResourceIndex:
        # <<Inherited docstring from ResourceClient>>
        return ResourceIndex.GUILD

    async def __on_guild_visibility_event(self, event: guild_events.GuildVisibilityEvent) -> None:
        client = await self.get_connection(ResourceIndex.GUILD)
        if isinstance(event, guild_events.GuildAvailableEvent):
            data = conversion.serialize_guild(event.guild)
            await client.hmset_dict(int(event.guild_id), data)

        elif isinstance(event, guild_events.GuildLeaveEvent):
            await client.delete(int(event.guild_id))

    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        #  TODO: on member chunk
        super().subscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.subscribe(guild_events.GuildVisibilityEvent, self.__on_guild_visibility_event)

    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().unsubscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.unsubscribe(guild_events.GuildVisibilityEvent, self.__on_guild_visibility_event)

    async def clear_guilds(self) -> None:
        client = await self.get_connection(ResourceIndex.GUILD)
        await client.flushdb()

    async def delete_guild(self, guild_id: snowflakes.Snowflakeish) -> None:
        # <<Inherited docstring from sake.traits.GuildCache>>
        client = await self.get_connection(ResourceIndex.GUILD)
        await client.delete(int(guild_id))

    async def get_guild(self, guild_id: snowflakes.Snowflakeish) -> guilds.GatewayGuild:
        # <<Inherited docstring from sake.traits.GuildCache>>
        client = await self.get_connection(ResourceIndex.GUILD)
        data = await client.hgetall(int(guild_id))

        if not data:
            raise errors.EntryNotFound(f"Guild entry `{guild_id}` not found")

        return conversion.deserialize_guild(data, app=self.rest)

    def iter_guilds(self) -> traits.CacheIterator[guilds.GatewayGuild]:
        # <<Inherited docstring from sake.traits.GuildCache>>
        return iterators.RedisIterator(self, ResourceIndex.GUILD, lambda id_: self.get_guild(snowflakes.Snowflake(id_)))

    async def set_guild(self, guild: guilds.GatewayGuild) -> None:
        # <<Inherited docstring from sake.traits.GuildCache>>
        client = await self.get_connection(ResourceIndex.GUILD)
        data = conversion.serialize_guild(guild)
        await client.hmset_dict(int(guild.id), data)


class MeCache(ResourceClient, traits.MeCache):
    __slots__: typing.Sequence[str] = ()

    __ME_KEY: typing.Final[str] = "ME"

    @classmethod
    def index(cls) -> ResourceIndex:
        # <<Inherited docstring from ResourceClient>>
        return ResourceIndex.USER

    async def __on_own_user_update(self, event: user_events.OwnUserUpdateEvent) -> None:
        await self.set_me(event.user)

    async def __on_shard_ready(self, event: shard_events.ShardReadyEvent) -> None:
        await self.set_me(event.my_user)

    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().subscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.subscribe(user_events.OwnUserUpdateEvent, self.__on_own_user_update)
            self.dispatch.dispatcher.subscribe(shard_events.ShardReadyEvent, self.__on_shard_ready)

    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().unsubscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.unsubscribe(user_events.OwnUserUpdateEvent, self.__on_own_user_update)
            self.dispatch.dispatcher.unsubscribe(shard_events.ShardReadyEvent, self.__on_shard_ready)

    async def delete_me(self) -> None:
        # <<Inherited docstring from sake.traits.MeCache>>
        client = await self.get_connection(ResourceIndex.USER)
        await client.delete(self.__ME_KEY)

    async def get_me(self) -> users.OwnUser:
        # <<Inherited docstring from sake.traits.MeCache>>
        client = await self.get_connection(ResourceIndex.USER)
        data = await client.hgetall(self.__ME_KEY)

        if not data:
            raise errors.EntryNotFound("Me entry not found")

        return conversion.deserialize_me(data, app=self.rest)

    async def set_me(self, me: users.OwnUser) -> None:
        # <<Inherited docstring from sake.traits.MeCache>>
        data = conversion.serialize_me(me)
        client = await self.get_connection(ResourceIndex.USER)
        await client.hmset_dict(self.__ME_KEY, data)


class _InternalMemberCache(UserCache):
    async def _delete_member(self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish) -> None:
        raise NotImplementedError

    async def _get_member(self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish) -> guilds.Member:
        raise NotImplementedError

    async def _set_member(self, member: guilds.Member) -> None:
        client = await self.get_connection(ResourceIndex.MEMBER)
        data = conversion.serialize_member(member)
        await self.set_user(member.user)
        await client.hmset_dict(int(member.id), data)


class MessageCache(_GuildReference, _InternalMemberCache, traits.MessageCache):
    @classmethod
    def index(cls) -> ResourceIndex:
        return ResourceIndex.MESSAGE

    async def __on_message_event(self, event: message_events.MessageEvent) -> None:
        if isinstance(event, message_events.MessageCreateEvent):
            await self.set_message(event.message)
        elif isinstance(event, message_events.MessageUpdateEvent):
            await self.update_message(event.message)
        elif isinstance(event, message_events.MessageDeleteEvent):
            await self.delete_message(event.message.id)

    def subscribe_listeners(self) -> None:
        super().subscribe_listeners()
        if self.dispatch is not None:
            return  # TODO: remove when mature
            self.dispatch.dispatcher.subscribe(message_events.MessageEvent, self.__on_message_event)

    def unsubscribe_listeners(self) -> None:
        super().unsubscribe_listeners()
        if self.dispatch is not None:
            return  # TODO: remove when mature
            self.dispatch.dispatcher.unsubscribe(message_events.MessageEvent, self.__on_message_event)

    async def clear_messages(self) -> None:
        raise NotImplementedError

    async def clear_messages_for_channel(self, channel_id: snowflakes.Snowflakeish) -> None:
        raise NotImplementedError

    async def clear_messages_for_guild(self, guild_id: snowflakes.Snowflakeish) -> None:
        raise NotImplementedError

    async def delete_message(self, message_id: snowflakes.Snowflakeish):
        raise NotImplementedError

    async def get_message(self, message_id: snowflakes.Snowflakeish) -> messages.Message:
        client = await self.get_connection(ResourceIndex.MESSAGE)

    def iter_messages(self) -> traits.CacheIterator[messages.Message]:
        raise NotImplementedError

    def iter_message_for_channel(self, channel_id: snowflakes.Snowflakeish) -> traits.CacheIterator[messages.Message]:
        raise NotImplementedError

    def iter_messages_for_guild(self, guild_id: snowflakes.Snowflakeish) -> traits.CacheIterator[messages.Message]:
        raise NotImplementedError

    async def set_message(self, message: messages.Message) -> None:
        data = conversion.serialize_message(message)
        await self._set_member(message.member)
        client = await self.get_connection(ResourceIndex.MESSAGE)
        await client.hmset_dict(int(message.id), data)

    async def update_message(self, message: messages.PartialMessage) -> bool:
        # This is a special case method for handling the partial message updates we get
        raise NotImplementedError


class RoleCache(_GuildReference, traits.RoleCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> ResourceIndex:
        # <<Inherited docstring from ResourceClient>>
        return ResourceIndex.ROLE

    async def __on_guild_visibility_event(self, event: guild_events.GuildVisibilityEvent) -> None:
        if isinstance(event, (guild_events.GuildAvailableEvent, guild_events.GuildUpdateEvent)):
            await asyncio.gather(*map(self.set_role, event.roles.values()))

        elif isinstance(event, guild_events.GuildLeaveEvent):
            await self.clear_roles_for_guild(event.guild_id)

    async def __on_role_update(self, event: role_events.RoleEvent) -> None:
        if isinstance(event, (role_events.RoleCreateEvent, role_events.RoleUpdateEvent)):
            await self.set_role(event.role)

        elif isinstance(event, role_events.RoleDeleteEvent):
            await self.delete_role(event.role_id)

    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().subscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.subscribe(guild_events.GuildVisibilityEvent, self.__on_guild_visibility_event)
            self.dispatch.dispatcher.subscribe(role_events.RoleEvent, self.__on_role_update)

    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().unsubscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.unsubscribe(guild_events.GuildVisibilityEvent, self.__on_guild_visibility_event)
            self.dispatch.dispatcher.unsubscribe(role_events.RoleEvent, self.__on_role_update)

    async def clear_roles(self) -> None:
        # <<Inherited docstring from sake.traits.RoleCache>>
        await self._clear_ids(ResourceIndex.ROLE)
        client = await self.get_connection(ResourceIndex.ROLE)
        await client.flushdb()

    async def clear_roles_for_guild(self, guild_id: snowflakes.Snowflakeish) -> None:
        # <<Inherited docstring from sake.traits.RoleCache>>
        role_ids = await self._get_ids(guild_id, ResourceIndex.ROLE, cast=snowflakes.Snowflake)
        if not role_ids:
            return

        await self._clear_ids_for_guild(guild_id, ResourceIndex.ROLE)
        await asyncio.gather(*map(self.delete_role, role_ids))

    async def delete_role(self, role_id: snowflakes.Snowflakeish) -> None:
        # <<Inherited docstring from sake.traits.RoleCache>>
        client = await self.get_connection(ResourceIndex.ROLE)
        data = await client.hgetall(int(role_id))

        if not data:
            return

        await self._delete_ids(data["guild_id"], ResourceIndex.ROLE, data["id"])
        await client.delete(int(role_id))

    async def get_role(self, role_id: snowflakes.Snowflakeish) -> guilds.Role:
        # <<Inherited docstring from sake.traits.RoleCache>>
        client = await self.get_connection(ResourceIndex.ROLE)
        data = await client.hgetall(int(role_id))

        if not data:
            raise errors.EntryNotFound(f"Role entry `{role_id}` not found")

        return conversion.deserialize_role(data, app=self.rest)

    def iter_roles(self) -> traits.CacheIterator[guilds.Role]:
        # <<Inherited docstring from sake.traits.RoleCache>>
        return iterators.RedisIterator(self, ResourceIndex.ROLE, lambda id_: self.get_role(snowflakes.Snowflake(id_)))

    def iter_roles_for_guild(self, guild_id: snowflakes.Snowflakeish) -> traits.CacheIterator[guilds.Role]:
        # <<Inherited docstring from sake.traits.RoleCache>>
        return iterators.SpecificRedisIterator(
            lambda: self._get_ids(guild_id, ResourceIndex.ROLE, cast=snowflakes.Snowflake), self.get_role
        )

    async def set_role(self, role: guilds.Role) -> None:
        # <<Inherited docstring from sake.traits.RoleCache>>
        client = await self.get_connection(ResourceIndex.ROLE)
        await client.hmset_dict(int(role.id), conversion.serialize_role(role))
        await self._add_ids(role.guild_id, ResourceIndex.ROLE, int(role.id))


class FullCache(GuildCache, EmojiCache, MeCache, RoleCache):
    """A class which implements all the defined cache resoruces."""

    __slots__: typing.Sequence[str] = ()
