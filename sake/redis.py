from __future__ import annotations

__all__: typing.Final[typing.Sequence[str]] = [
    "ResourceClient",
    "EmojiCache",
    "FullCache",
    "GuildCache",
    "GuildChannelCache",
    "InviteCache",
    "MeCache",
    "MemberCache",
    "MessageCache",
    "PresenceCache",
    "RoleCache",
    "UserCache",
]

import abc
import asyncio
import enum
import itertools
import logging
import typing

import aioredis
from hikari import channels, invites
from hikari import guilds
from hikari import presences
from hikari import snowflakes
from hikari import users
from hikari.events import channel_events
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
from sake.traits import CacheIterator

if typing.TYPE_CHECKING:
    import ssl as ssl_
    import types

    import aioredis.abc
    from hikari import emojis as emojis_
    from hikari import messages
    from hikari import traits as hikari_traits


_LOGGER: typing.Final[logging.Logger] = logging.getLogger("hikari.sake")
RedisValueT = typing.Union[bytearray, bytes, float, int, str]
"""A type variable of the value types accepted by aioredis."""
RedisMapT = typing.MutableMapping[str, RedisValueT]
"""A type variable of the mapping type accepted by aioredis"""
ResourceT = typing.TypeVar("ResourceT", bound="ResourceClient")
KeyT = typing.TypeVar("KeyT")
OtherKeyT = typing.TypeVar("OtherKeyT")
"""Type-Hint The logger instance used by this sake implementation."""
ValueT = typing.TypeVar("ValueT")
OtherValueT = typing.TypeVar("OtherValueT")
"""Type-Hint A type hint used to represent a resource client instance."""
WINDOW_SIZE: typing.Final[int] = 100


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


def chunk_values(
    values: typing.Iterable[ValueT], window_size: int = WINDOW_SIZE
) -> typing.Iterator[typing.Sequence[ValueT]]:
    if window_size <= 0:
        raise ValueError("Window size must be a positive integer")

    iterator = iter(values)
    while result := list(itertools.islice(iterator, window_size)):
        yield result


def cast_map_window(
    window: typing.Iterable[typing.Tuple[KeyT, ValueT]],
    key_cast: typing.Callable[[KeyT], OtherKeyT],
    value_cast: typing.Callable[[ValueT], OtherValueT],
) -> typing.Dict[OtherKeyT, OtherValueT]:
    return dict((key_cast(key), value_cast(value)) for key, value in window)


async def global_iter_get(
    client: aioredis.Redis, window_size: int
) -> typing.AsyncIterator[typing.MutableSequence[bytes]]:
    cursor = 0
    while True:
        cursor, results = await client.scan(cursor, count=window_size)

        if results:
            yield await client.mget(*results)

        if not cursor:
            break


async def iter_hget(
    client: aioredis.Redis, key: RedisValueT, window_size: int
) -> typing.AsyncIterator[typing.MutableSequence[bytes]]:
    cursor = 0
    while True:
        cursor, results = await client.hscan(key, cursor, count=window_size)

        if results:
            yield [result for _, result in results]

        if not cursor:
            break


async def reference_iter_get(
    resource_client: ResourceClient, index: ResourceIndex, key: RedisValueT, window_size: int
) -> typing.AsyncIterator[typing.MutableSequence[bytes]]:
    client = await resource_client.get_connection(index)
    reference_client = await resource_client.get_connection(ResourceIndex.GUILD_REFERENCE)
    cursor = 0

    while True:
        cursor, results = await reference_client.sscan(key, cursor, count=window_size)

        if results:
            yield await client.mget(*results)

        if not cursor:
            break


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
        "_converter",
        "_dispatch",
        "_metadata",
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
        metadata: typing.Optional[typing.MutableMapping[str, typing.Any]] = None,
    ) -> None:
        self._address = address
        self._dispatch = dispatch
        self._clients: typing.MutableMapping[ResourceIndex, aioredis.Redis] = {}
        self._converter = conversion.JSONHandler(rest)
        self._metadata = metadata or {}
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

    @property
    def metadata(self) -> typing.MutableMapping[str, typing.Any]:
        return self._metadata

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

    async def _optionally_bulk_set_users(self, users_: typing.Iterator[users.User]) -> None:
        if isinstance(self, UserCache):
            client = await self.get_connection(ResourceIndex.USER)
            windows = chunk_values(users_)
            setters = (
                client.mset(*((int(user.id), self._converter.serialize_user(user)) for user in window))
                for window in windows
            )
            await asyncio.gather(*setters)

    async def _optionally_set_user(self, user: users.User) -> None:
        if isinstance(self, UserCache):
            await self.set_user(user)

    async def _spawn_connection(self, resource: ResourceIndex) -> None:
        self._clients[resource] = await aioredis.create_redis_pool(
            address=self._address,
            db=int(resource),
            password=self._password,
            ssl=self._ssl,
            # encoding="utf-8",
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
    def _generate_reference_key(guild_id: snowflakes.Snowflakeish, resource: ResourceIndex) -> str:
        return f"{guild_id}:{int(resource)}"

    async def _add_ids(
        self, guild_id: snowflakes.Snowflakeish, resource: ResourceIndex, *identifiers: RedisValueT
    ) -> None:
        key = self._generate_reference_key(guild_id, resource)
        client = await self.get_connection(ResourceIndex.GUILD_REFERENCE)
        await client.sadd(key, *identifiers)

    async def _clear_ids(self, resource: ResourceIndex) -> None:
        raise NotImplementedError

    async def _clear_ids_for_guild(self, guild_id: snowflakes.Snowflakeish, resource: ResourceIndex) -> None:
        key = self._generate_reference_key(guild_id, resource)
        client = await self.get_connection(ResourceIndex.GUILD_REFERENCE)
        await client.delete(key)

    async def _delete_ids(
        self, guild_id: snowflakes.Snowflakeish, resource: ResourceIndex, *identifiers: RedisValueT
    ) -> None:
        key = self._generate_reference_key(guild_id, resource)
        client = await self.get_connection(ResourceIndex.GUILD_REFERENCE)
        await client.srem(key, *identifiers)  # TODO: do i need to explicitly delete this if len is 0?

    async def _get_ids(
        self,
        guild_id: snowflakes.Snowflakeish,
        resource: ResourceIndex,
        *,
        cast: typing.Callable[[bytes], ValueT],
    ) -> typing.Sequence[ValueT]:
        key = self._generate_reference_key(guild_id, resource)
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
        await client.flushdb()

    async def delete_user(self, user_id: snowflakes.Snowflakeish) -> None:
        # <<Inherited docstring from sake.traits.UserCache>>
        client = await self.get_connection(ResourceIndex.USER)
        await client.delete(int(user_id))

    async def get_user(self, user_id: snowflakes.Snowflakeish) -> users.User:
        # <<Inherited docstring from sake.traits.UserCache>>
        client = await self.get_connection(ResourceIndex.USER)
        data = await client.get(int(user_id))

        if not data:
            raise errors.EntryNotFound(f"User entry `{user_id}` not found")

        return self._converter.deserialize_user(data)

    def iter_users(
        self, *, window_size: int = WINDOW_SIZE
    ) -> traits.CacheIterator[users.User]:  # TODO: handle when an entity is removed mid-iteration
        # <<Inherited docstring from sake.traits.UserCache>>
        return iterators.RedisIterator(
            self, ResourceIndex.USER, self._converter.deserialize_user, window_size=window_size
        )

    async def set_user(self, user: users.User) -> None:
        # <<Inherited docstring from sake.traits.UserCache>>
        client = await self.get_connection(ResourceIndex.USER)
        await client.set(int(user.id), self._converter.serialize_user(user))


class EmojiCache(_GuildReference, traits.EmojiCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> ResourceIndex:
        # <<Inherited docstring from ResourceClient>>
        return ResourceIndex.EMOJI

    async def __bulk_add_emojis(self, emojis: typing.Iterable[emojis_.KnownCustomEmoji]) -> None:
        client = await self.get_connection(ResourceIndex.EMOJI)
        windows = chunk_values(emojis)
        setters = (
            client.mset({int(emoji.id): self._converter.serialize_emoji(emoji) for emoji in window})
            for window in windows
        )
        await asyncio.gather(*setters)

    async def __on_emojis_update(self, event: guild_events.EmojisUpdateEvent) -> None:
        await self.clear_emojis_for_guild(event.guild_id)
        await self.__bulk_add_emojis(event.emojis)

    async def __on_guild_visibility_event(self, event: guild_events.GuildVisibilityEvent) -> None:
        if isinstance(event, (guild_events.GuildAvailableEvent, guild_events.GuildUpdateEvent)):
            await self.clear_emojis_for_guild(event.guild_id)
            await self.__bulk_add_emojis(event.emojis.values())

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
        emoji_ids = await self._get_ids(guild_id, ResourceIndex.EMOJI, cast=int)
        if not emoji_ids:
            return

        await self._clear_ids_for_guild(guild_id, ResourceIndex.EMOJI)
        client = await self.get_connection(ResourceIndex.EMOJI)
        await asyncio.gather(*(client.delete(*window) for window in chunk_values(emoji_ids)))

    async def delete_emoji(self, emoji_id: snowflakes.Snowflakeish) -> None:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        client = await self.get_connection(ResourceIndex.EMOJI)
        data = await client.get(int(emoji_id))

        if not data:
            return

        emoji = self._converter.deserialize_emoji(data)  # TODO: can i avoid this?
        await self._delete_ids(int(emoji.guild_id), ResourceIndex.EMOJI, int(emoji.id))
        await client.delete(int(emoji_id))

    async def get_emoji(self, emoji_id: snowflakes.Snowflakeish) -> emojis_.KnownCustomEmoji:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        client = await self.get_connection(ResourceIndex.EMOJI)
        data = await client.get(int(emoji_id))

        if not data:
            raise errors.EntryNotFound(f"Emoji entry `{emoji_id}` not found")

        return self._converter.deserialize_emoji(data)

    def iter_emojis(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[emojis_.KnownCustomEmoji]:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        return iterators.RedisIterator(
            self, ResourceIndex.EMOJI, self._converter.deserialize_emoji, window_size=window_size
        )

    def iter_emojis_for_guild(
        self, guild_id: snowflakes.Snowflakeish, *, window_size: int = WINDOW_SIZE
    ) -> traits.CacheIterator[emojis_.KnownCustomEmoji]:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        key = self._generate_reference_key(guild_id, ResourceIndex.EMOJI).encode()
        return iterators.SpecificRedisIterator(
            self, key, ResourceIndex.EMOJI, self._converter.deserialize_emoji, window_size=window_size
        )

    async def set_emoji(self, emoji: emojis_.KnownCustomEmoji) -> None:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        client = await self.get_connection(ResourceIndex.EMOJI)
        data = self._converter.serialize_emoji(emoji)
        await self._add_ids(emoji.guild_id, ResourceIndex.EMOJI, int(emoji.id))
        await client.set(int(emoji.id), data)

        if emoji.user is not None:
            await self._optionally_set_user(emoji.user)


class GuildCache(ResourceClient, traits.GuildCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> ResourceIndex:
        # <<Inherited docstring from ResourceClient>>
        return ResourceIndex.GUILD

    async def __on_guild_visibility_event(self, event: guild_events.GuildVisibilityEvent) -> None:
        client = await self.get_connection(ResourceIndex.GUILD)
        if isinstance(event, guild_events.GuildAvailableEvent):
            data = self._converter.serialize_guild(event.guild)
            await client.set(int(event.guild_id), data)

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
        data = await client.get(int(guild_id))

        if not data:
            raise errors.EntryNotFound(f"Guild entry `{guild_id}` not found")

        return self._converter.deserialize_guild(data)

    def iter_guilds(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[guilds.GatewayGuild]:
        # <<Inherited docstring from sake.traits.GuildCache>>
        return iterators.RedisIterator(
            self, ResourceIndex.GUILD, self._converter.deserialize_guild, window_size=window_size
        )

    async def set_guild(self, guild: guilds.GatewayGuild) -> None:
        # <<Inherited docstring from sake.traits.GuildCache>>
        client = await self.get_connection(ResourceIndex.GUILD)
        data = self._converter.serialize_guild(guild)
        await client.set(int(guild.id), data)


class GuildChannelCache(_GuildReference, traits.GuildChannelCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> ResourceIndex:
        # <<Inherited docstring from sake.traits.Resource>>
        return ResourceIndex.GUILD_CHANNEL

    async def __on_guild_channel_event(self, event: channel_events.GuildChannelEvent) -> None:
        if isinstance(event, (channel_events.GuildChannelCreateEvent, channel_events.GuildChannelUpdateEvent)):
            await self.set_guild_channel(event.channel)
        elif isinstance(event, channel_events.GuildChannelDeleteEvent):
            await self.delete_guild_channel(event.channel_id)
        elif isinstance(event, channel_events.GuildPinsUpdateEvent):
            try:
                channel = await self.get_guild_channel(event.channel_id)
            except errors.EntryNotFound:
                pass
            else:
                assert isinstance(
                    channel, (channels.GuildNewsChannel, channels.GuildTextChannel)
                ), "unexpected channel type for a pin update"
                channel.last_pin_timestamp = event.last_pin_timestamp
                await self.set_guild_channel(channel)

    async def __on_guild_event(self, event: guild_events.GuildVisibilityEvent) -> None:
        if isinstance(event, guild_events.GuildAvailableEvent):
            client = await self.get_connection(ResourceIndex.GUILD_CHANNEL)
            windows = chunk_values(event.channels.items())
            setters = (
                client.mset(cast_map_window(window, int, self._converter.serialize_guild_channel)) for window in windows
            )
            id_setter = self._add_ids(event.guild_id, ResourceIndex.GUILD_CHANNEL, *map(int, event.channels.keys()))
            await asyncio.gather(*setters, id_setter)

        elif isinstance(event, guild_events.GuildLeaveEvent):
            await self.clear_guild_channels_for_guild(event.guild_id)

    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().subscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.subscribe(channel_events.GuildChannelEvent, self.__on_guild_channel_event)
            self.dispatch.dispatcher.subscribe(guild_events.GuildVisibilityEvent, self.__on_guild_event)

    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().unsubscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.unsubscribe(channel_events.GuildChannelEvent, self.__on_guild_channel_event)
            self.dispatch.dispatcher.unsubscribe(guild_events.GuildVisibilityEvent, self.__on_guild_event)

    async def clear_guild_channels(self) -> None:
        client = await self.get_connection(ResourceIndex.GUILD_CHANNEL)
        await client.flushdb()
        await self._clear_ids(ResourceIndex.GUILD_CHANNEL)

    async def clear_guild_channels_for_guild(self, guild_id: snowflakes.Snowflakeish) -> None:
        channel_ids = await self._get_ids(int(guild_id), ResourceIndex.GUILD_CHANNEL, cast=int)
        if not channel_ids:
            return

        await self._clear_ids_for_guild(int(guild_id), ResourceIndex.GUILD_CHANNEL)
        client = await self.get_connection(ResourceIndex.GUILD_CHANNEL)
        await asyncio.gather(*(client.delete(*window) for window in chunk_values(channel_ids)))

    async def delete_guild_channel(self, channel_id: snowflakes.Snowflakeish) -> None:
        client = await self.get_connection(ResourceIndex.GUILD_CHANNEL)
        await client.delete(int(channel_id))

    async def get_guild_channel(self, channel_id: snowflakes.Snowflakeish) -> channels.GuildChannel:
        client = await self.get_connection(ResourceIndex.GUILD_CHANNEL)
        data = await client.get(int(channel_id))

        if not data:
            raise errors.EntryNotFound(f"Guild channel entry `{channel_id}` not found")

        return self._converter.deserialize_guild_channel(data)

    def iter_guild_channels(self, *, window_size: int = WINDOW_SIZE) -> CacheIterator[channels.GuildChannel]:
        return iterators.RedisIterator(
            self, ResourceIndex.GUILD_CHANNEL, self._converter.deserialize_guild_channel, window_size=window_size
        )

    def iter_guild_channels_for_guild(
        self, guild_id: snowflakes.Snowflakeish, *, window_size: int = WINDOW_SIZE
    ) -> CacheIterator[channels.GuildChannel]:
        key = self._generate_reference_key(guild_id, ResourceIndex.GUILD_CHANNEL).encode()
        return iterators.SpecificRedisIterator(
            self, key, ResourceIndex.GUILD_CHANNEL, self._converter.deserialize_guild_channel, window_size=window_size
        )

    async def set_guild_channel(self, channel: channels.GuildChannel) -> None:
        client = await self.get_connection(ResourceIndex.GUILD_CHANNEL)
        data = self._converter.serialize_guild_channel(channel)
        await client.set(int(channel.id), data)
        await self._add_ids(int(channel.guild_id), ResourceIndex.GUILD_CHANNEL, int(channel.id))


class InviteCache(_GuildReference, traits.InviteCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> ResourceIndex:
        # <<Inherited docstring from sake.traits.Resource>>
        return ResourceIndex.INVITE

    async def __on_guild_channel_delete_event(self, event: channel_events.GuildChannelDeleteEvent) -> None:
        await self.clear_invites_for_channel(event.channel_id)

    # TODO: can we also use member remove for the same purpose?
    async def __on_guild_leave_event(self, event: guild_events.GuildLeaveEvent) -> None:
        await self.clear_invites_for_guild(event.guild_id)

    async def __on_invite_event(self, event: channel_events.InviteEvent) -> None:
        if isinstance(event, channel_events.InviteCreateEvent):
            await self.set_invite(event.invite)
        elif isinstance(event, channel_events.InviteDeleteEvent):
            await self.delete_invite(event.code)

    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().subscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.subscribe(
                channel_events.GuildChannelDeleteEvent, self.__on_guild_channel_delete_event
            )
            self.dispatch.dispatcher.subscribe(guild_events.GuildLeaveEvent, self.__on_guild_leave_event)
            self.dispatch.dispatcher.subscribe(channel_events.InviteEvent, self.__on_invite_event)

    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().unsubscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.unsubscribe(
                channel_events.GuildChannelDeleteEvent, self.__on_guild_channel_delete_event
            )
            self.dispatch.dispatcher.unsubscribe(guild_events.GuildLeaveEvent, self.__on_guild_leave_event)
            self.dispatch.dispatcher.unsubscribe(channel_events.InviteEvent, self.__on_invite_event)

    async def clear_invites(self) -> None:
        await self._clear_ids(ResourceIndex.INVITE)
        client = await self.get_connection(ResourceIndex.INVITE)
        await client.flushdb()

    async def clear_invites_for_channel(self, channel_id: snowflakes.Snowflakeish) -> None:
        raise NotImplementedError

    async def clear_invites_for_guild(self, guild_id: snowflakes.Snowflakeish) -> None:
        codes = await self._get_ids(int(guild_id), ResourceIndex.INVITE, cast=lambda key: key.decode("utf-8"))
        if not codes:
            return

        client = await self.get_connection(ResourceIndex.INVITE)
        await asyncio.gather(*(client.delete(*window) for window in chunk_values(codes)))

    async def delete_invite(self, invite_code: str) -> None:
        client = await self.get_connection(ResourceIndex.INVITE)
        # Aioredis treats keys and values as type invariant so we want to ensure this is a str and not a class which
        # subclasses str.
        await client.delete(str(invite_code))

    async def get_invite(self, invite_code: str) -> invites.InviteWithMetadata:
        client = await self.get_connection(ResourceIndex.INVITE)
        # Aioredis treats keys and values as type invariant so we want to ensure this is a str and not a class which
        # subclasses str.
        data = await client.get(str(invite_code))
        if not data:
            raise errors.EntryNotFound(f"Invite entry `{invite_code}` not found")

        return self._converter.deserialize_invite(data)

    def iter_invites(self, *, window_size: int = WINDOW_SIZE) -> CacheIterator[invites.InviteWithMetadata]:
        return iterators.RedisIterator(
            self, ResourceIndex.INVITE, self._converter.deserialize_invite, window_size=window_size
        )

    def iter_invites_for_channel(
        self, channel_id: snowflakes.Snowflakeish
    ) -> CacheIterator[invites.InviteWithMetadata]:
        raise NotImplementedError

    def iter_invites_for_guild(
        self, guild_id: snowflakes.Snowflakeish, *, window_size: int = WINDOW_SIZE
    ) -> CacheIterator[invites.InviteWithMetadata]:
        key = self._generate_reference_key(guild_id, ResourceIndex.INVITE).encode()
        return iterators.SpecificRedisIterator(
            self, key, ResourceIndex.INVITE, self._converter.deserialize_invite, window_size=window_size
        )

    async def set_invite(self, invite: invites.InviteWithMetadata) -> None:
        client = await self.get_connection(ResourceIndex.INVITE)
        data = self._converter.serialize_invite(invite)
        await client.set(str(invite.code), data)

        if invite.guild_id is not None:
            await self._add_ids(int(invite.guild_id), ResourceIndex.INVITE, str(invite.code))


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
        data = await client.get(self.__ME_KEY)

        if not data:
            raise errors.EntryNotFound("Me entry not found")

        return self._converter.deserialize_me(data)

    async def set_me(self, me: users.OwnUser) -> None:
        # <<Inherited docstring from sake.traits.MeCache>>
        data = self._converter.serialize_me(me)
        client = await self.get_connection(ResourceIndex.USER)
        await client.set(self.__ME_KEY, data)
        await self._optionally_set_user(me)


class MemberCache(ResourceClient, traits.MemberCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> ResourceIndex:
        # <<Inherited docstring from sake.traits.Resource>>
        return ResourceIndex.MEMBER

    async def __bulk_add_members(
        self, guild_id: snowflakes.Snowflakeish, members: typing.Mapping[snowflakes.Snowflake, guilds.Member]
    ) -> None:
        client = await self.get_connection(ResourceIndex.MEMBER)
        windows = chunk_values(members.items())
        setters = (
            client.hmset_dict(int(guild_id), cast_map_window(window, int, self._converter.serialize_member))
            for window in windows
        )
        await asyncio.gather(*setters, self._optionally_bulk_set_users(member.user for member in members.values()))

    async def __on_guild_availability(self, event: guild_events.GuildAvailableEvent) -> None:
        await self.__bulk_add_members(event.guild_id, event.members)

    async def __on_member_event(self, event: member_events.MemberEvent) -> None:
        if isinstance(event, (member_events.MemberCreateEvent, member_events.MemberUpdateEvent)):
            await self.set_member(event.member)
        elif isinstance(event, member_events.MemberDeleteEvent):
            if "own_id" not in self.metadata:
                #  TODO: this is racey
                user = await self.rest.rest.fetch_my_user()
                self.metadata["own_id"] = user.id

            own_id = self.metadata["own_id"]
            assert isinstance(own_id, snowflakes.Snowflake)
            if event.user_id == own_id:
                await self.clear_members_for_guild(event.guild_id)
            else:
                await self.delete_member(event.guild_id, event.user_id)

    async def __on_member_chunk_event(self, event: shard_events.MemberChunkEvent) -> None:
        await self.__bulk_add_members(event.guild_id, event.members)

    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().subscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.subscribe(guild_events.GuildAvailableEvent, self.__on_guild_availability)
            self.dispatch.dispatcher.subscribe(member_events.MemberEvent, self.__on_member_event)
            self.dispatch.dispatcher.subscribe(shard_events.MemberChunkEvent, self.__on_member_chunk_event)

    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().unsubscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.unsubscribe(guild_events.GuildAvailableEvent, self.__on_guild_availability)
            self.dispatch.dispatcher.unsubscribe(member_events.MemberEvent, self.__on_member_event)
            self.dispatch.dispatcher.unsubscribe(shard_events.MemberChunkEvent, self.__on_member_chunk_event)

    async def clear_members(self) -> None:
        # <<Inherited docstring from sake.traits.MemberCache>>
        client = await self.get_connection(ResourceIndex.MEMBER)
        await client.flushdb()

    async def clear_members_for_guild(self, guild_id: snowflakes.Snowflakeish) -> None:
        # <<Inherited docstring from sake.traits.MemberCache>>
        client = await self.get_connection(ResourceIndex.MEMBER)
        await client.delete(int(guild_id))

    async def delete_member(self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish) -> None:
        # <<Inherited docstring from sake.traits.MemberCache>>
        client = await self.get_connection(ResourceIndex.MEMBER)
        await client.hdel(int(guild_id), int(user_id))

    async def get_member(self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish) -> guilds.Member:
        # <<Inherited docstring from sake.traits.MemberCache>>
        client = await self.get_connection(ResourceIndex.MEMBER)
        data = await client.hget(int(guild_id), int(user_id))

        if not data:
            raise errors.EntryNotFound(f"Member entry `{user_id}` for guild `{guild_id}` not found")

        return self._converter.deserialize_member(data)

    def iter_members(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[guilds.Member]:
        # <<Inherited docstring from sake.traits.MemberCache>>
        return iterators.MultiMapIterator(
            self, ResourceIndex.MEMBER, self._converter.deserialize_member, window_size=window_size
        )

    def iter_members_for_guild(
        self, guild_id: snowflakes.Snowflakeish, *, window_size: int = WINDOW_SIZE
    ) -> traits.CacheIterator[guilds.Member]:
        # <<Inherited docstring from sake.traits.MemberCache>>
        return iterators.SpecificMapIterator(
            self,
            str(guild_id).encode(),
            ResourceIndex.MEMBER,
            self._converter.deserialize_member,
            window_size=window_size,
        )

    def iter_members_for_user(self, user_id: snowflakes.Snowflakeish) -> traits.CacheIterator[guilds.Member]:
        # <<Inherited docstring from sake.traits.MemberCache>>
        raise NotImplementedError

    async def set_member(self, member: guilds.Member) -> None:
        # <<Inherited docstring from sake.traits.MemberCache>>
        client = await self.get_connection(ResourceIndex.MEMBER)
        data = self._converter.serialize_member(member)
        await client.hset(int(member.guild_id), int(member.user.id), data)
        await self._optionally_set_user(member.user)


class MessageCache(_GuildReference, traits.MessageCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> ResourceIndex:
        # <<Inherited docstring from sake.traits.Resource>>
        return ResourceIndex.MESSAGE

    async def __bulk_delete_messages(self, message_ids: typing.Iterable[int]) -> None:
        client = await self.get_connection(ResourceIndex.MESSAGE)
        await asyncio.gather(*(client.delete(*window) for window in chunk_values(message_ids)))

    async def __on_channel_delete(self, event: channel_events.ChannelDeleteEvent) -> None:
        await self.clear_messages_for_channel(event.channel_id)

    async def __on_guild_leave(self, event: guild_events.GuildLeaveEvent) -> None:
        await self.clear_messages_for_guild(event.guild_id)

    async def __on_message_event(self, event: message_events.MessageEvent) -> None:
        if isinstance(event, message_events.MessageCreateEvent):
            await self.set_message(event.message)

        elif isinstance(event, message_events.MessageUpdateEvent):
            await self.update_message(event.message)

        elif isinstance(event, message_events.MessageDeleteEvent):
            await self.__bulk_delete_messages((int(message_id) for message_id in event.message_ids))

    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().subscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.subscribe(channel_events.ChannelDeleteEvent, self.__on_channel_delete)
            self.dispatch.dispatcher.subscribe(guild_events.GuildLeaveEvent, self.__on_guild_leave)
            self.dispatch.dispatcher.subscribe(message_events.MessageEvent, self.__on_message_event)

    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().unsubscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.unsubscribe(channel_events.ChannelDeleteEvent, self.__on_channel_delete)
            self.dispatch.dispatcher.unsubscribe(guild_events.GuildLeaveEvent, self.__on_guild_leave)
            self.dispatch.dispatcher.unsubscribe(message_events.MessageEvent, self.__on_message_event)

    async def clear_messages(self) -> None:
        client = await self.get_connection(ResourceIndex.INVITE)
        await self._clear_ids(ResourceIndex.INVITE)
        await client.flushdb()

    async def clear_messages_for_channel(self, channel_id: snowflakes.Snowflakeish) -> None:
        raise NotImplementedError

    async def clear_messages_for_guild(self, guild_id: snowflakes.Snowflakeish) -> None:
        message_ids = await self._get_ids(int(guild_id), ResourceIndex.MESSAGE, cast=int)
        if not message_ids:
            return

        await self._clear_ids_for_guild(int(guild_id), ResourceIndex.MESSAGE)
        await self.__bulk_delete_messages(message_ids)

    async def delete_message(self, message_id: snowflakes.Snowflakeish) -> None:
        client = await self.get_connection(ResourceIndex.MESSAGE)
        await client.delete(int(message_id))

    async def get_message(self, message_id: snowflakes.Snowflakeish) -> messages.Message:
        client = await self.get_connection(ResourceIndex.MESSAGE)
        data = await client.get(int(message_id))
        if not data:
            raise errors.EntryNotFound(f"Message entry `{message_id}` not found")

        return self._converter.deserialize_message(data)

    def iter_messages(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[messages.Message]:
        return iterators.RedisIterator(
            self, ResourceIndex.MESSAGE, self._converter.deserialize_message, window_size=window_size
        )

    def iter_message_for_channel(self, channel_id: snowflakes.Snowflakeish) -> traits.CacheIterator[messages.Message]:
        raise NotImplementedError

    def iter_messages_for_guild(
        self, guild_id: snowflakes.Snowflakeish, *, window_size: int = WINDOW_SIZE
    ) -> traits.CacheIterator[messages.Message]:
        key = self._generate_reference_key(guild_id, ResourceIndex.MESSAGE).encode()
        return iterators.SpecificRedisIterator(
            self, key, ResourceIndex.MESSAGE, self._converter.deserialize_message, window_size=window_size
        )

    async def set_message(self, message: messages.Message) -> None:
        data = self._converter.serialize_message(message)
        client = await self.get_connection(ResourceIndex.MESSAGE)
        await client.set(int(message.id), data)
        await self._optionally_set_user(message.author)

    async def update_message(self, message: messages.PartialMessage) -> bool:
        # This is a special case method for handling the partial message updates we get
        raise NotImplementedError


class PresenceCache(ResourceClient, traits.PresenceCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> ResourceIndex:
        # <<Inherited docstring from ResourceClient>>
        return ResourceIndex.PRESENCE

    async def __bulk_add_presences(
        self, guild_id: snowflakes.Snowflake, presence: typing.Mapping[snowflakes.Snowflake, presences.MemberPresence]
    ) -> None:
        client = await self.get_connection(ResourceIndex.PRESENCE)
        windows = chunk_values(presence.items())
        setters = (
            client.hmset_dict(int(guild_id), cast_map_window(window, int, self._converter.serialize_presence))
            for window in windows
        )
        await asyncio.gather(*setters)  # self._optionally_bulk_set_users

    async def __on_guild_visibility_event(self, event: guild_events.GuildVisibilityEvent) -> None:
        if isinstance(event, guild_events.GuildAvailableEvent):
            await self.__bulk_add_presences(event.guild_id, event.presences)

        elif isinstance(event, guild_events.GuildLeaveEvent):
            await self.clear_presences_for_guild(event.guild_id)

    async def __on_member_chunk(self, event: shard_events.MemberChunkEvent) -> None:
        await self.__bulk_add_presences(event.guild_id, event.presences)

    async def __on_presence_update_event(self, event: guild_events.PresenceUpdateEvent) -> None:
        if event.presence.visible_status is presences.Status.OFFLINE:
            await self.delete_presence(event.guild_id, event.user_id)

        else:
            await self.set_presence(event.presence)

    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().subscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.subscribe(guild_events.GuildVisibilityEvent, self.__on_guild_visibility_event)
            self.dispatch.dispatcher.subscribe(shard_events.MemberChunkEvent, self.__on_member_chunk)
            self.dispatch.dispatcher.subscribe(guild_events.PresenceUpdateEvent, self.__on_presence_update_event)

    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().unsubscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.unsubscribe(guild_events.GuildVisibilityEvent, self.__on_guild_visibility_event)
            self.dispatch.dispatcher.unsubscribe(shard_events.MemberChunkEvent, self.__on_member_chunk)
            self.dispatch.dispatcher.unsubscribe(guild_events.PresenceUpdateEvent, self.__on_presence_update_event)

    async def clear_presences(self) -> None:
        client = await self.get_connection(ResourceIndex.PRESENCE)
        await client.flushdb()

    async def clear_presences_for_guild(self, guild_id: snowflakes.Snowflakeish) -> None:
        client = await self.get_connection(ResourceIndex.PRESENCE)
        await client.delete(int(guild_id))

    async def delete_presence(self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish) -> None:
        client = await self.get_connection(ResourceIndex.PRESENCE)
        await client.hdel(int(guild_id), int(user_id))

    async def get_presence(
        self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish
    ) -> presences.MemberPresence:
        client = await self.get_connection(ResourceIndex.PRESENCE)
        data = await client.hget(int(guild_id), int(user_id))
        return self._converter.deserialize_presence(data)

    def iter_presences(self, *, window_size: int = WINDOW_SIZE) -> CacheIterator[presences.MemberPresence]:
        return iterators.MultiMapIterator(
            self, ResourceIndex.PRESENCE, self._converter.deserialize_presence, window_size=window_size
        )

    def iter_presences_for_user(self, user_id: snowflakes.Snowflakeish) -> CacheIterator[presences.MemberPresence]:
        raise NotImplementedError

    def iter_presences_for_guild(
        self, guild_id: snowflakes.Snowflakeish, *, window_size: int = WINDOW_SIZE
    ) -> CacheIterator[presences.MemberPresence]:
        return iterators.SpecificMapIterator(
            self,
            str(guild_id).encode(),
            ResourceIndex.PRESENCE,
            self._converter.deserialize_presence,
            window_size=window_size,
        )

    async def set_presence(self, presence: presences.MemberPresence) -> None:
        data = self._converter.serialize_presence(presence)
        client = await self.get_connection(ResourceIndex.PRESENCE)
        await client.hset(int(presence.guild_id), int(presence.user_id), data)


class RoleCache(_GuildReference, traits.RoleCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> ResourceIndex:
        # <<Inherited docstring from ResourceClient>>
        return ResourceIndex.ROLE

    async def __on_guild_visibility_event(self, event: guild_events.GuildVisibilityEvent) -> None:
        if isinstance(event, (guild_events.GuildAvailableEvent, guild_events.GuildUpdateEvent)):
            client = await self.get_connection(ResourceIndex.ROLE)
            windows = chunk_values(event.roles.items())
            setters = (client.mset(cast_map_window(window, int, self._converter.serialize_role)) for window in windows)
            id_setter = self._add_ids(event.guild_id, ResourceIndex.ROLE, *map(int, event.roles.keys()))
            await asyncio.gather(*setters, id_setter)

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
        role_ids = await self._get_ids(guild_id, ResourceIndex.ROLE, cast=int)
        if not role_ids:
            return

        await self._clear_ids_for_guild(guild_id, ResourceIndex.ROLE)
        client = await self.get_connection(ResourceIndex.ROLE)
        await asyncio.gather(*(client.delete(*window) for window in chunk_values(role_ids)))

    async def delete_role(self, role_id: snowflakes.Snowflakeish) -> None:
        # <<Inherited docstring from sake.traits.RoleCache>>
        client = await self.get_connection(ResourceIndex.ROLE)
        data = await client.get(int(role_id))

        if not data:
            return

        role = self._converter.deserialize_role(data)  # TODO: can i avoid this?
        await self._delete_ids(int(role.guild_id), ResourceIndex.ROLE, int(role.id))
        await client.delete(int(role_id))

    async def get_role(self, role_id: snowflakes.Snowflakeish) -> guilds.Role:
        # <<Inherited docstring from sake.traits.RoleCache>>
        client = await self.get_connection(ResourceIndex.ROLE)
        data = await client.get(int(role_id))

        if not data:
            raise errors.EntryNotFound(f"Role entry `{role_id}` not found")

        return self._converter.deserialize_role(data)

    def iter_roles(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[guilds.Role]:
        # <<Inherited docstring from sake.traits.RoleCache>>
        return iterators.RedisIterator(
            self, ResourceIndex.ROLE, self._converter.deserialize_role, window_size=window_size
        )

    def iter_roles_for_guild(
        self, guild_id: snowflakes.Snowflakeish, *, window_size: int = WINDOW_SIZE
    ) -> traits.CacheIterator[guilds.Role]:
        # <<Inherited docstring from sake.traits.RoleCache>>
        key = self._generate_reference_key(guild_id, ResourceIndex.ROLE).encode()
        return iterators.SpecificRedisIterator(
            self, key, ResourceIndex.ROLE, self._converter.deserialize_role, window_size=window_size
        )

    async def set_role(self, role: guilds.Role) -> None:
        # <<Inherited docstring from sake.traits.RoleCache>>
        client = await self.get_connection(ResourceIndex.ROLE)
        await client.set(int(role.id), self._converter.serialize_role(role))
        await self._add_ids(role.guild_id, ResourceIndex.ROLE, int(role.id))


class FullCache(
    GuildCache, EmojiCache, GuildChannelCache, InviteCache, MeCache, MemberCache, PresenceCache, RoleCache
):  # MessageCache
    """A class which implements all the defined cache resoruces."""

    __slots__: typing.Sequence[str] = ()
