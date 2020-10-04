from __future__ import annotations

__all__: typing.Final[typing.Sequence[str]] = ["ResourceClient", "EmojiCache", "FullCache", "GuildCache", "UserCache"]

import abc
import asyncio
import enum
import logging
import typing

import aioredis
from hikari import guilds
from hikari import users
from hikari.events import guild_events
from hikari.events import member_events

from sake import conversion
from sake import traits
from sake import views

if typing.TYPE_CHECKING:
    import ssl as ssl_
    import types

    import aioredis.abc
    from hikari import emojis as emojis_
    from hikari import snowflakes
    from hikari import traits as hikari_traits


_LOGGER: typing.Final[logging.Logger] = logging.getLogger("hikari.sake")
ResourceT = typing.TypeVar("ResourceT", bound="ResourceClient")


class ResourceIndex(enum.IntEnum):
    EMOJI = 0
    GUILD = 1
    GUILD_CHANNEL = 2
    INVITE = 3
    ME = 4
    MEMBER = 5
    PRESENCE = 6
    ROLE = 7
    USER = 8
    VOICE_STATE = 9


class ResourceClient(traits.Resource, abc.ABC):
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

    @property  # As a note, this will only be set if this is actively hooked into event dispatchers
    def dispatcher(self) -> typing.Optional[hikari_traits.DispatcherAware]:
        return self._dispatch

    @property  # unlike here where this is 100% required for building models.
    def rest(self) -> hikari_traits.RESTAware:
        return self._rest

    async def destroy_connection(self, resource: ResourceIndex) -> None:
        if resource in self._clients:
            await self._clients.pop(resource).close()

    async def get_connection(self, resource: ResourceIndex) -> aioredis.Redis:
        if not self._started:
            raise TypeError("Cannot use an inactive client")

        try:
            return self._clients[resource]
        except KeyError:
            pool = await aioredis.create_redis_pool(
                address=self._address,
                db=int(resource),
                password=self._password,
                ssl=self._ssl,
                encoding="utf-8",
            )
            self._clients[resource] = pool
            return pool

    async def get_connection_status(self, resource: ResourceIndex) -> bool:
        return resource in self._clients and not self._clients[resource].closed

    async def open(self) -> None:
        if not self._started:
            self.subscribe_listeners()
            self._started = True

    async def close(self) -> None:
        # We want to ensure that we both only do anything here if the client was already started when the method was
        # originally called and also that the client is marked as "closed" before this starts severing connections.
        was_started = self._started
        self._started = False

        if was_started:
            self.unsubscribe_listeners()
            for key in tuple(self._clients.keys()):
                await self.destroy_connection(key)

    @abc.abstractmethod
    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        return None

    @abc.abstractmethod
    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        return None


class UserCache(ResourceClient, traits.UserCache):
    __slots__: typing.Sequence[str] = ()

    async def _delete_user(self, user_id: snowflakes.Snowflakeish) -> None:
        client = await self.get_connection(ResourceIndex.USER)
        await client.delete(int(user_id))

    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        # The users cache is a special case as it doesn't directly map to any events.
        super().subscribe_listeners()

    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        # The users cache is a special case as it doesn't directly map to any events.
        super().unsubscribe_listeners()

    async def get_user(self, user_id: snowflakes.Snowflakeish) -> users.User:
        client = await self.get_connection(ResourceIndex.USER)
        data = await client.hgetall(int(user_id))
        return conversion.deserialize_user(data, app=self.rest)

    async def get_user_view(self) -> views.CacheView[snowflakes.Snowflake, users.User]:
        raise NotImplementedError

    async def _set_user(self, user: users.User) -> None:
        client = await self.get_connection(ResourceIndex.USER)
        await client.hmset_dict(int(user.id), conversion.serialize_user(user))


class EmojiCache(UserCache, traits.EmojiCache):
    __slots__: typing.Sequence[str] = ()

    async def _bulk_add_emojis(self, emojis: typing.Iterable[emojis_.KnownCustomEmoji]) -> None:
        client = await self.get_connection(ResourceIndex.EMOJI)
        transaction = client.multi_exec()
        user_tasks = []

        for emoji in emojis:
            transaction.hmset_dict(int(emoji), conversion.serialize_emoji(emoji))

            if emoji.user:
                user_tasks.append(self._set_user(emoji.user))

        await asyncio.gather(transaction.execute(), *user_tasks)

    async def _on_emojis_update(self, event: guild_events.EmojisUpdateEvent) -> None:
        await self.clear_emojis_for_guild(event.guild_id)
        await self._bulk_add_emojis(event.emojis)

    async def _on_guild_available_and_update(
        self, event: typing.Union[guild_events.GuildAvailableEvent, guild_events.GuildUpdateEvent]
    ) -> None:
        await self.clear_emojis_for_guild(event.guild_id)
        await self._bulk_add_emojis(event.emojis.values())

    async def _on_guild_leave(self, event: guild_events.GuildLeaveEvent) -> None:
        await self.clear_emojis_for_guild(event.guild_id)

    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().subscribe_listeners()
        if self.dispatcher is not None:
            self.dispatcher.dispatcher.subscribe(guild_events.EmojisUpdateEvent, self._on_emojis_update)
            self.dispatcher.dispatcher.subscribe(guild_events.GuildAvailableEvent, self._on_guild_available_and_update)
            self.dispatcher.dispatcher.subscribe(guild_events.GuildUpdateEvent, self._on_guild_available_and_update)
            self.dispatcher.dispatcher.subscribe(guild_events.GuildLeaveEvent, self._on_guild_leave)
            #  TODO: can we also listen for member delete to manage this?

    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().unsubscribe_listeners()
        if self.dispatcher is not None:
            self.dispatcher.dispatcher.unsubscribe(guild_events.EmojisUpdateEvent, self._on_emojis_update)
            self.dispatcher.dispatcher.unsubscribe(
                guild_events.GuildAvailableEvent, self._on_guild_available_and_update
            )
            self.dispatcher.dispatcher.unsubscribe(guild_events.GuildUpdateEvent, self._on_guild_available_and_update)
            self.dispatcher.dispatcher.unsubscribe(guild_events.GuildLeaveEvent, self._on_guild_leave)
            #  TODO: can we also listen for member delete to manage this?

    async def clear_emojis(self) -> None:  # TODO: clear methods?
        client = await self.get_connection(ResourceIndex.EMOJI)
        await client.flushdb()

    async def clear_emojis_for_guild(self, guild_id: snowflakes.Snowflakeish) -> None:
        raise NotImplementedError

    async def delete_emoji(self, emoji_id: snowflakes.Snowflakeish) -> None:
        client = await self.get_connection(ResourceIndex.EMOJI)
        await client.delete(int(emoji_id))

    async def get_emoji(self, emoji_id: snowflakes.Snowflakeish) -> emojis_.KnownCustomEmoji:
        client = await self.get_connection(ResourceIndex.EMOJI)
        data = await client.hgetall(int(emoji_id))
        user = await self.get_user(int(data["user_id"])) if "user_id" in data else None
        return conversion.deserialize_emoji(data, app=self.rest, user=user)

    async def get_emoji_view(self) -> views.CacheView[snowflakes.Snowflake, emojis_.KnownCustomEmoji]:
        raise NotImplementedError

    async def get_emoji_view_for_guild(
        self, guild_id: snowflakes.Snowflakeish
    ) -> views.CacheView[snowflakes.Snowflake, emojis_.KnownCustomEmoji]:
        raise NotImplementedError

    async def set_emoji(self, emoji: emojis_.KnownCustomEmoji) -> None:
        client = await self.get_connection(ResourceIndex.EMOJI)
        data = conversion.serialize_emoji(emoji)

        if emoji.user is not None:
            await self._set_user(emoji.user)

        await client.hmset_dict(int(emoji.id), data)


class GuildCache(ResourceClient, traits.GuildCache):
    __slots__: typing.Sequence[str] = ()

    async def _on_guild_visibility_event(self, event: guild_events.GuildVisibilityEvent) -> None:
        client = await self.get_connection(ResourceIndex.GUILD)
        if isinstance(event, guild_events.GuildAvailableEvent):
            data = conversion.serialize_guild(event.guild)
            await client.hmset_dict(int(event.guild_id), data)

        elif isinstance(event, guild_events.GuildLeaveEvent):
            await client.delete(int(event.guild_id))

    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().subscribe_listeners()
        if self.dispatcher is not None:
            self.dispatcher.dispatcher.subscribe(guild_events.GuildVisibilityEvent, self._on_guild_visibility_event)

    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().unsubscribe_listeners()
        if self.dispatcher is not None:
            self.dispatcher.dispatcher.subscribe(guild_events.GuildVisibilityEvent, self._on_guild_visibility_event)

    async def delete_guild(self, guild_id: snowflakes.Snowflakeish) -> None:
        client = await self.get_connection(ResourceIndex.GUILD)
        await client.delete(int(guild_id))

    async def get_guild(self, guild_id: snowflakes.Snowflakeish) -> guilds.GatewayGuild:
        client = await self.get_connection(ResourceIndex.GUILD)
        data = await client.hgetall(int(guild_id))
        return conversion.deserialize_guild(data, app=self.rest)

    async def get_guild_view(self) -> views.CacheView[snowflakes.Snowflake, guilds.GatewayGuild]:
        raise NotImplementedError

    async def set_guild(self, guild: guilds.GatewayGuild) -> None:
        client = await self.get_connection(ResourceIndex.GUILD)
        data = conversion.serialize_guild(guild)
        await client.hmset_dict(int(guild.id), data)


class FullCache(GuildCache, EmojiCache):
    __slots__: typing.Sequence[str] = ()
