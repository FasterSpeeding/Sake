from __future__ import annotations

__all__: typing.Final[typing.Sequence[str]] = ["Resource", "EmojiCache"]

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
    from hikari import emojis
    from hikari import snowflakes


_LOGGER: typing.Final[logging.Logger] = logging.getLogger("hikari.sake")
ResourceT = typing.TypeVar("ResourceT", bound="Resource")


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


class Resource(traits.Resource, abc.ABC):
    __slots__: typing.Sequence[str] = (
        "_app",
        "_address",
        "_connection",
        "_database",
        "_password",
        "_ssl",
    )

    def __init__(
        self,
        app: traits.RESTAndDispatcherAware,
        *,
        address: typing.Union[str, typing.Tuple[str, typing.Union[str, int]]],
        database: typing.Optional[int] = None,
        password: typing.Optional[str] = None,
        ssl: typing.Union[ssl_.SSLContext, bool, None] = None,
    ) -> None:
        self._address = address
        self._app = app
        self._connection: typing.Optional[aioredis.ConnectionsPool] = None
        self._database = database
        self._password = password
        self._ssl = ssl

    @property
    def app(self) -> traits.RESTAndDispatcherAware:
        return self._app

    @abc.abstractmethod
    def subscribe_listener(self) -> None:
        return None

    @abc.abstractmethod
    def unsubscribe_listener(self) -> None:
        return None

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

    async def get_active(self) -> bool:
        return self._connection is not None and not self._connection.closed

    async def open(self) -> None:
        self._connection = await aioredis.create_pool(
            address=self._address,
            db=self._database,
            password=self._password,
            ssl=self._ssl,
            encoding="utf-8",
        )
        self.subscribe_listener()

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

        self.unsubscribe_listener()


class UserCache(Resource, traits.UserCache):
    __slots__: typing.Sequence[str] = ("_user_client",)

    def __init__(
        self,
        app: traits.RESTAndDispatcherAware,
        *,
        address: typing.Union[str, typing.Tuple[str, typing.Union[str, int]]],
        database: typing.Optional[int] = None,
        password: typing.Optional[str] = None,
        ssl: typing.Union[ssl_.SSLContext, bool, None] = None,
    ) -> None:
        super().__init__(app, address=address, database=database, password=password, ssl=ssl)
        self._user_client: typing.Optional[aioredis.Redis] = None

    async def open(self) -> None:
        await super().open()
        self._user_client = aioredis.Redis(self._connection)

    async def close(self) -> None:
        await super().close()
        self._user_client = None

    def subscribe_listener(self) -> None:
        # The users cache is a special case as it doesn't directly map to any events.
        super().subscribe_listener()

    def unsubscribe_listener(self) -> None:
        # The users cache is a special case as it doesn't directly map to any events.
        super().unsubscribe_listener()

    async def _delete_user(self, user_id: snowflakes.Snowflakeish) -> None:
        if self._user_client is None:
            raise RuntimeError("Cannot remove entities from an inactive cache") from None

        await self._user_client.delete(int(user_id))

    async def get_user(self, user_id: snowflakes.Snowflakeish) -> users.User:
        if self._user_client is None:
            raise RuntimeError("Cannot get entities from an inactive cache") from None

        data = await self._user_client.hgetall(int(user_id))
        return conversion.deserialize_user(data, app=self.app)

    async def get_user_view(self) -> views.CacheView[snowflakes.Snowflake, users.User]:
        raise NotImplementedError

    async def _set_user(self, user: users.User) -> None:
        if self._user_client is None:
            raise RuntimeError("Cannot set entities on an inactive cache") from None

        await self._user_client.hmset_dict(int(user.id), conversion.serialize_user(user))


class EmojiCache(UserCache, traits.EmojiCache):
    __slots__: typing.Sequence[str] = ("_emoji_client",)

    def __init__(
        self,
        app: traits.RESTAndDispatcherAware,
        *,
        address: typing.Union[str, typing.Tuple[str, typing.Union[str, int]]],
        database: typing.Optional[int] = None,
        password: typing.Optional[str] = None,
        ssl: typing.Union[ssl_.SSLContext, bool, None] = None,
    ) -> None:
        super().__init__(app, address=address, database=database, password=password, ssl=ssl)
        self._emoji_client: typing.Optional[aioredis.Redis] = None

    async def open(self) -> None:
        await super().open()
        self._emoji_client = aioredis.Redis(self._connection)

    async def close(self) -> None:
        await super().close()
        self._emoji_client = None

    def subscribe_listener(self) -> None:
        super().subscribe_listener()
        self.app.dispatcher.subscribe(guild_events.EmojisUpdateEvent, self._on_emojis_update)
        self.app.dispatcher.subscribe(guild_events.GuildLeaveEvent, self._on_guild_leave)
        self.app.dispatcher.subscribe(member_events.MemberDeleteEvent, self._on_member_delete)

    def unsubscribe_listener(self) -> None:
        super().unsubscribe_listener()
        self.app.dispatcher.unsubscribe(guild_events.EmojisUpdateEvent, self._on_emojis_update)
        self.app.dispatcher.unsubscribe(guild_events.GuildLeaveEvent, self._on_guild_leave)
        self.app.dispatcher.unsubscribe(member_events.MemberDeleteEvent, self._on_member_delete)

    async def _on_emojis_update(self, event: guild_events.EmojisUpdateEvent) -> None:
        if self._emoji_client is None:
            return _LOGGER.warning("A closed async cache received an emojis update event, this shouldn't happen")

        await self.clear_emojis_for_guild(event.guild_id)

        await asyncio.gather(*(map(self.set_emoji, event.emojis)))  # TODO: chained stuff in aioredis

    async def _on_guild_leave(self, event: guild_events.GuildLeaveEvent) -> None:
        if self._emoji_client is None:
            return _LOGGER.warning("A closed async cache received a guild leave event, this shouldn't happen")

        await self.clear_emojis_for_guild(event.guild_id)

    async def _on_member_delete(self, event: member_events.MemberDeleteEvent) -> None:
        if self._emoji_client is None:
            return _LOGGER.warning("A closed async cache received a member delete event, this shouldn't happen")

        if event.user_id == self.app.me:  # TODO: is this sane?
            await self.clear_emojis_for_guild(event.guild_id)

    async def clear_emojis(self) -> None:  # TODO: clear methods?
        raise NotImplementedError

    async def clear_emojis_for_guild(self, guild_id: snowflakes.Snowflakeish) -> None:
        raise NotImplementedError

    async def delete_emoji(self, emoji_id: snowflakes.Snowflakeish) -> None:
        if self._emoji_client is None:
            raise RuntimeError("Cannot remove entities from an inactive cache") from None

        await self._emoji_client.delete(int(emoji_id))

    async def get_emoji(self, emoji_id: snowflakes.Snowflakeish) -> emojis.KnownCustomEmoji:
        if self._emoji_client is None:
            raise RuntimeError("Cannot get entities from an inactive cache") from None

        data = await self._emoji_client.hgetall(int(emoji_id))
        user = await self.get_user(int(data["user_id"])) if "user_id" in data else None
        return conversion.deserialize_emoji(data, app=self.app, user=user)

    async def get_emoji_view(self) -> views.CacheView[snowflakes.Snowflake, emojis.KnownCustomEmoji]:
        raise NotImplementedError

    async def get_emoji_view_for_guild(
        self, guild_id: snowflakes.Snowflakeish
    ) -> views.CacheView[snowflakes.Snowflake, emojis.KnownCustomEmoji]:
        raise NotImplementedError

    async def set_emoji(self, emoji: emojis.KnownCustomEmoji) -> None:
        if self._emoji_client is None:
            raise RuntimeError("Cannot set entities in an inactive cache") from None

        data = conversion.serialize_emoji(emoji)

        if emoji.user is not None:
            await self._set_user(emoji.user)

        await self._emoji_client.hmset_dict(int(emoji.id), data)


class GuildCache(Resource, traits.GuildCache):
    __slots__: typing.Sequence[str] = ("_guild_client",)

    def __init__(
        self,
        app: traits.RESTAndDispatcherAware,
        *,
        address: typing.Union[str, typing.Tuple[str, typing.Union[str, int]]],
        database: typing.Optional[int] = None,
        password: typing.Optional[str] = None,
        ssl: typing.Union[ssl_.SSLContext, bool, None] = None,
    ) -> None:
        super().__init__(app, address=address, database=database, password=password, ssl=ssl)
        self._guild_client: typing.Optional[aioredis.Redis] = None

    async def open(self) -> None:
        await super().open()
        self._guild_client = aioredis.Redis(self._connection)

    async def close(self) -> None:
        await super().close()
        self._guild_client = None

    def subscribe_listener(self) -> None:
        self.app.dispatcher.subscribe(guild_events.GuildVisibilityEvent, self._on_guild_visibility_event)

    def unsubscribe_listener(self) -> None:
        self.app.dispatcher.subscribe(guild_events.GuildVisibilityEvent, self._on_guild_visibility_event)

    async def _on_guild_visibility_event(self, event: guild_events.GuildVisibilityEvent) -> None:
        if self._guild_client is None:
            return _LOGGER.warning("A closed async cache received a guild visibility event, this shouldn't happen")

        if isinstance(event, guild_events.GuildAvailableEvent):
            data = conversion.serialize_guild(event.guild)
            await self._guild_client.hmset_dict(int(event.guild_id), data)

        elif isinstance(event, guild_events.GuildLeaveEvent):
            await self._guild_client.delete(int(event.guild_id))

    async def delete_guild(self, guild_id: snowflakes.Snowflakeish) -> None:
        if self._guild_client is None:
            raise RuntimeError("Cannot remove entities from an inactive cache") from None

        await self._guild_client.delete(int(guild_id))

    async def get_guild(self, guild_id: snowflakes.Snowflakeish) -> guilds.GatewayGuild:
        if self._guild_client is None:
            raise RuntimeError("Cannot get entities from an inactive cache") from None

        data = await self._guild_client.hgetall(int(guild_id))
        return conversion.deserialize_guild(data, app=self.app)

    async def get_guild_view(self) -> views.CacheView[snowflakes.Snowflake, guilds.GatewayGuild]:
        raise NotImplementedError

    async def set_guild(self, guild: guilds.GatewayGuild) -> None:
        if self._guild_client is None:
            raise RuntimeError("Cannot set entities in an inactive cache") from None

        data = conversion.serialize_guild(guild)
        await self._guild_client.hmset_dict(int(guild.id), data)
