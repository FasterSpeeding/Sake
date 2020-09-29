import typing

__all__: typing.Final[typing.Sequence[str]] = ["Resource", "EmojiCache"]

import abc
import asyncio

import aioredis
import aioredis.abc
from hikari import guilds
from hikari import users
from hikari.events import guild_events
from hikari.events import member_events

from sake import traits
from sake import views

if typing.TYPE_CHECKING:
    import types

    from hikari import emojis
    from hikari import snowflakes


ResourceT = typing.TypeVar("ResourceT", bound="Resource")


class Resource(traits.Resource, abc.ABC):
    __slots__: typing.Sequence[str] = (
        "_app",
        "_address",
        "_connection",
        "_database",
        "_password",
        "_ssl",
        "_encoding",
        "_connection_cls",
    )

    def __init__(
        self,
        app: traits.RESTAndDispatcherAware,
        *,
        address: str,
        database: typing.Optional[int] = None,
        password: typing.Optional[str] = None,
        ssl: bool = True,
        encoding: typing.Optional[str] = None,
        connection_cls: typing.Optional[aioredis.abc.AbcConnection] = None,
    ) -> None:
        self._address = address
        self._app = app
        self._connection: typing.Optional[aioredis.ConnectionsPool] = None
        self._database = database
        self._password = password
        self._ssl = ssl
        self._encoding = encoding
        self._connection_cls = connection_cls

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
            encoding=self._encoding,
            # minsize
            # maxsize
            # parser
            # loop
            # create_connection_timeout
            # pool_cls=
            connection_cls=self._connection_cls,
        )
        self.subscribe_listener()

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()

        self.unsubscribe_listener()


class _PrivateUserCache(Resource):
    def subscribe_listener(self) -> None:
        super().subscribe_listener()

    def unsubscribe_listener(self) -> None:
        super().unsubscribe_listener()

    async def _delete_user(self) -> None:
        raise NotImplementedError

    async def _get_user(self) -> users.User:
        raise NotImplementedError

    async def _set_user(self) -> None:
        raise NotImplementedError


class _PrivateMemberCache(_PrivateUserCache):
    async def _delete_member(self) -> None:
        raise NotImplementedError

    async def _get_member(self) -> guilds.Member:
        raise NotImplementedError

    async def _set_member(self) -> None:
        raise NotImplementedError


class EmojiCache(Resource, traits.EmojiCache):
    def subscribe_listener(self) -> None:
        super().subscribe_listener()
        self.app.dispatcher.subscribe(guild_events.EmojisUpdateEvent, self._on_emojis_update)
        self.app.dispatcher.subscribe(guild_events.GuildLeaveEvent, self._on_guild_leave)
        self.app.dispatcher.subscribe(member_events.MemberDeleteEvent, self._on_member_remove)

    def unsubscribe_listener(self) -> None:
        super().unsubscribe_listener()
        self.app.dispatcher.unsubscribe(guild_events.EmojisUpdateEvent, self._on_emojis_update)
        self.app.dispatcher.unsubscribe(guild_events.GuildLeaveEvent, self._on_guild_leave)
        self.app.dispatcher.unsubscribe(member_events.MemberDeleteEvent, self._on_member_remove)

    async def _on_emojis_update(self, event: guild_events.EmojisUpdateEvent) -> None:
        await self.clear_emojis_for_guild(event.guild_id)

        await asyncio.gather(*(map(self.set_emoji, event.emojis)))

    async def _on_guild_leave(self, event: guild_events.GuildLeaveEvent) -> None:
        await self.clear_emojis_for_guild(event.guild_id)

    async def _on_member_remove(self, event: member_events.MemberDeleteEvent) -> None:
        if event.user_id == self.app.me:  # TODO: is this sane?
            await self.clear_emojis_for_guild(event.guild_id)

    async def clear_emojis(self) -> None:
        raise NotImplementedError

    async def clear_emojis_for_guild(self, guild_id: snowflakes.Snowflakeish) -> None:
        raise NotImplementedError

    async def delete_emoji(self, emoji_id: snowflakes.Snowflakeish) -> None:
        raise NotImplementedError

    async def get_emoji(self, emoji_id: snowflakes.Snowflakeish) -> emojis.KnownCustomEmoji:
        raise NotImplementedError

    async def get_emoji_view(self) -> views.CacheView[snowflakes.Snowflake, emojis.KnownCustomEmoji]:
        raise NotImplementedError

    async def get_emoji_view_for_guild(
        self, guild_id: snowflakes.Snowflakeish
    ) -> views.CacheView[snowflakes.Snowflake, emojis.KnownCustomEmoji]:
        raise NotImplementedError

    async def set_emoji(self, emoji: emojis.KnownCustomEmoji) -> None:
        raise NotImplementedError
