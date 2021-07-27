# -*- coding: utf-8 -*-
# cython: language_level=3
# BSD 3-Clause License
#
# Copyright (c) 2020, Faster Speeding
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
from __future__ import annotations

__all__: typing.Final[typing.Sequence[str]] = [
    "ResourceClient",
    "PrefixCache",
    "EmojiCache",
    "GuildCache",
    "GuildChannelCache",
    "IntegrationCache",
    "InviteCache",
    "MemberCache",
    "MessageCache",
    "PresenceCache",
    "RedisCache",
    "RoleCache",
    "UserCache",
    "VoiceStateCache",
]

import abc
import asyncio
import datetime
import enum
import itertools
import logging
import math
import typing

import aioredis
from hikari import channels
from hikari import errors as hikari_errors
from hikari import guilds
from hikari import invites as invites_
from hikari import presences as presences_
from hikari import snowflakes
from hikari import undefined
from hikari import users
from hikari import voices
from hikari.events import channel_events
from hikari.events import guild_events
from hikari.events import member_events
from hikari.events import message_events
from hikari.events import role_events
from hikari.events import shard_events
from hikari.events import user_events
from hikari.events import voice_events
from hikari.internal import time
from yuyo import backoff

from sake import errors
from sake import marshalling
from sake import redis_iterators
from sake import traits

if typing.TYPE_CHECKING:
    import ssl as ssl_
    import types

    import aioredis.abc
    from hikari import emojis as emojis_
    from hikari import messages
    from hikari import traits as hikari_traits

_KeyT = typing.TypeVar("_KeyT")
_OtherKeyT = typing.TypeVar("_OtherKeyT")
_ValueT = typing.TypeVar("_ValueT")
_OtherValueT = typing.TypeVar("_OtherValueT")
_LOGGER: typing.Final[logging.Logger] = logging.getLogger("hikari.sake.redis")
"""Type-Hint The logger instance used by this Sake implementation."""
RedisValueT = typing.Union[bytearray, bytes, float, int, str]
"""A type variable of the value types accepted by aioredis."""
RedisMapT = typing.MutableMapping[str, RedisValueT]
"""A type variable of the mapping type accepted by aioredis"""
ResourceT = typing.TypeVar("ResourceT", bound="ResourceClient")
"""Type-Hint A type hint used to represent a resource client instance."""
WINDOW_SIZE: typing.Final[int] = 1_000
"""The default size used for "windowed" chunking in this client."""
DEFAULT_EXPIRE: typing.Final[int] = 3_600_000
"""The default expire time (in milliseconds) used for expiring resources of 60 minutes."""
DEFAULT_FAST_EXPIRE: typing.Final[int] = 300_000
"""The default expire time (in milliseconds) used for expiring resources quickly of 5 minutes."""
DEFAULT_SLOW_EXPIRE: typing.Final[int] = 604_800
"""The default expire time (in milliseconds) used for gateway-event deleted resources (1 week)."""
DEFAULT_INVITE_EXPIRE: typing.Final[int] = 2_592_000_000
"""A special case month long default expire time for invite entries without a set "expire_at"."""
ExpireT = typing.Union[datetime.timedelta, int, float, None]
"""A type hint used to represent expire times.

These may either be the number of seconds as an int or float (where millisecond
precision is supported) or a timedelta. `builtins.None`, float("nan") and
float("inf") all represent no expire.
"""


class ResourceIndex(enum.IntEnum):
    """An enum of the indexes used to map cache resources to their redis databases."""

    EMOJI = 0
    GUILD = 1
    CHANNEL = 2
    INVITE = 3
    MEMBER = 4
    PRESENCE = 5
    ROLE = 6
    USER = 7
    VOICE_STATE = 8
    REFERENCE = 9
    #  REFERENCE is a special case database solely used for linking other entries to other master entities.
    MESSAGE = 10
    INTEGRATION = 11
    PREFIX = 12


def _cast_map_window(
    window: typing.Iterable[typing.Tuple[_KeyT, _ValueT]],
    key_cast: typing.Callable[[_KeyT], _OtherKeyT],
    value_cast: typing.Callable[[_ValueT], _OtherValueT],
) -> typing.Dict[_OtherKeyT, _OtherValueT]:
    return dict((key_cast(key), value_cast(value)) for key, value in window)


async def _close_client(client: aioredis.Redis) -> None:
    # TODO: will we need to catch errors here?
    client.close()


def _convert_expire_time(expire: ExpireT) -> typing.Optional[int]:
    """Convert a timedelta, int or float expire time representation to an integer."""
    if expire is None:
        return None

    if isinstance(expire, datetime.timedelta):
        return round(expire.total_seconds() * 1000)

    if isinstance(expire, int):
        return expire * 1000

    if isinstance(expire, float):
        if math.isnan(expire) or math.isinf(expire):
            return None

        return round(expire * 1000)

    raise ValueError(f"Invalid expire time passed; expected a float, int or timedelta but got a {type(expire)!r}")


# TODO: document that it isn't guaranteed that deletion will be finished before clear command coroutines finish.
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
        "__address",
        "__clients",
        "__default_expire",
        "__marshaller",
        "__dispatch",
        "__metadata",
        "__password",
        "__rest",
        "__ssl",
        "__started",
    )

    def __init__(
        self,
        rest: hikari_traits.RESTAware,
        dispatch: typing.Optional[hikari_traits.DispatcherAware] = None,
        /,
        *,
        address: typing.Union[str, typing.Tuple[str, typing.Union[str, int]]],
        default_expire: ExpireT = DEFAULT_SLOW_EXPIRE,
        password: typing.Optional[str] = None,
        ssl: typing.Union[ssl_.SSLContext, bool, None] = None,
        metadata: typing.Optional[typing.MutableMapping[str, typing.Any]] = None,
        object_marshaller: typing.Optional[marshalling.ObjectMarshaller[bytes]] = None,
    ) -> None:
        self.__address = address
        self.__clients: typing.Dict[int, aioredis.Redis] = {}
        self.__default_expire = _convert_expire_time(default_expire)
        self.__dispatch = dispatch
        self.__index_overrides: typing.Dict[ResourceIndex, int] = {}
        self.__marshaller = object_marshaller or marshalling.JSONMarshaller(rest)
        self.__metadata = metadata or {}
        self.__password = password
        self.__rest = rest
        self.__ssl = ssl
        self.__started = False

    async def __aenter__(self: ResourceT) -> ResourceT:
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: typing.Type[typing.Type[BaseException]],
        exc_val: typing.Type[BaseException],
        exc_tb: typing.Type[types.TracebackType],
    ) -> None:
        await self.close()

    def __enter__(self) -> typing.NoReturn:
        # This is async only.
        cls = type(self)
        raise TypeError(f"{cls.__module__}.{cls.__qualname__} is async-only, did you mean 'async with'?") from None

    def __exit__(
        self,
        exc_type: typing.Optional[typing.Type[BaseException]],
        exc_val: typing.Optional[BaseException],
        exc_tb: typing.Optional[types.TracebackType],
    ) -> None:
        return None

    @property
    def default_expire(self) -> typing.Optional[int]:
        return self.__default_expire  # TODO: , pexpire=self.default_expire

    @classmethod
    @abc.abstractmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        """The index for the resource which this class is linked to.

        !!! note
            This should be called on specific base classes and will not be
            accurate after inheritance.

        !!! warning
            This doesn't account for overrides.

        Returns
        -------
        ResourceIndex
            The index of the resource this class is linked to.
        """
        return ()

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
        return self.__dispatch

    @property
    def marshaller(self) -> marshalling.ObjectMarshaller[bytes]:
        return self.__marshaller

    @property
    def metadata(self) -> typing.MutableMapping[str, typing.Any]:
        return self.__metadata

    @property  # unlike here where this is 100% required for building models.
    def rest(self) -> hikari_traits.RESTAware:
        """The REST aware client this resource client is tied to.

        This is used to build models with a `app` attribute.

        Returns
        -------
        hikari.traits.RESTAware
            The REST aware client this resource is tied to.
        """
        return self.__rest

    def get_index_override(self, index: ResourceIndex, /) -> typing.Optional[int]:
        return self.__index_overrides.get(index)

    def with_index_override(
        self: ResourceT, index: ResourceIndex, override: typing.Optional[int] = None, /
    ) -> ResourceT:
        if self.__started:
            raise ValueError("Cannot set an index override while the client is active")

        if override is not None:
            self.__index_overrides[index] = override

        else:
            self.__index_overrides.pop(index, None)

        return self

    async def get_connection(self, resource: ResourceIndex, /) -> aioredis.Redis:
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
        if not self.__started:
            raise TypeError("Cannot use an inactive client")

        try:
            return self.__clients[self.__index_overrides.get(resource, resource)]
        except KeyError:
            raise ValueError(f"Resource index `{resource}` isn't invalid for this client") from None

    def __get_indexes(self) -> typing.MutableSet[int]:
        results: typing.Set[int] = set()
        for sub_class in type(self).mro():
            if issubclass(sub_class, ResourceClient):
                results.update((self.__index_overrides.get(index, index) for index in sub_class.index()))

        return results

    async def get_connection_status(self, resource: ResourceIndex, /) -> bool:
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
        resource = self.__index_overrides.get(resource, resource)
        return resource in self.__clients and not self.__clients[resource].closed

    async def _optionally_bulk_set_users(self, users_: typing.Iterator[users.User]) -> None:
        try:
            client = await self.get_connection(ResourceIndex.USER)
        except ValueError:
            pass
        else:
            expire_time = int(self.metadata.get("expire_user", DEFAULT_EXPIRE))
            # user_setters = []
            # expire_setters: typing.MutableSequence[typing.Coroutine[typing.Any, typing.Any, None]] = []

            for window in redis_iterators.chunk_values(users_):
                processed_window = {int(user.id): self.marshaller.serialize_user(user) for user in window}
                # transaction = client.multi_exec()
                #
                # for user_id in processed_window.keys():
                #     transaction.pexpire(user_id, expire_time)
                #
                # expire_setters.append(transaction.execute())
                await client.mset(processed_window)
                asyncio.gather(*(client.pexpire(user_id, expire_time) for user_id in processed_window.keys()))
                #  TODO: benchmark bulk setting expire with transaction vs this
                # user_setters.append(client.mset(processed_window))
                # expire_setters.extend((client.pexpire(user_id, expire_time) for user_id in processed_window.keys()))

            # asyncio.gather(*user_setters)
            # asyncio.gather(*expire_setters)

    async def _optionally_set_user(self, user: users.User) -> None:
        try:
            client = await self.get_connection(ResourceIndex.USER)
        except ValueError:
            pass
        else:
            data = self.marshaller.serialize_user(user)
            expire_time = int(self.metadata.get("expire_user", DEFAULT_EXPIRE))
            await client.set(int(user.id), data, pexpire=expire_time)

    async def _spawn_connection(self, resource: int) -> None:
        self.__clients[resource] = await aioredis.create_redis_pool(
            address=self.__address,
            db=int(resource),
            password=self.__password,
            ssl=self.__ssl,
            # encoding="utf-8",
        )

    async def open(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        if self.__started:
            return

        try:
            # Gather is awaited here so we can assure all clients are started before this returns.
            await asyncio.gather(*map(self._spawn_connection, self.__get_indexes()))
        except aioredis.RedisError:
            # Ensure no dangling clients are left if this fails to start.
            clients = self.__clients
            self.__clients = {}
            await asyncio.gather(*map(_close_client, clients.values()))
            raise

        self.subscribe_listeners()
        self.__started = True

    async def close(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        # We want to ensure that we both only do anything here if the client was already started when the method was
        # originally called and also that the client is marked as "closed" before this starts severing connections.
        was_started = self.__started
        self.__started = False

        if not was_started:
            return

        self.unsubscribe_listeners()
        clients = self.__clients
        self.__clients = {}
        # Gather is awaited here so we can assure all clients are closed before this returns.
        await asyncio.gather(*map(_close_client, clients.values()))

    @abc.abstractmethod
    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        return None

    @abc.abstractmethod
    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        return None


class _Reference(ResourceClient, abc.ABC):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        return (ResourceIndex.REFERENCE,)

    @staticmethod
    def _generate_reference_key(master: ResourceIndex, master_id: snowflakes.Snowflakeish, slave: ResourceIndex) -> str:
        return f"{int(master)}:{master_id}:{int(slave)}"

    # To ensure at least 1 ID is always provided we have a required arg directly before the variable-length argument.
    async def _add_ids(
        self,
        master: ResourceIndex,
        master_id: snowflakes.Snowflakeish,
        slave: ResourceIndex,
        identifier: RedisValueT,
        *identifiers: RedisValueT,
    ) -> None:
        key = self._generate_reference_key(master, master_id, slave)
        client = await self.get_connection(ResourceIndex.REFERENCE)
        await client.sadd(key, identifier, *identifiers)

    # To ensure at least 1 ID is always provided we have a required arg directly before the variable-length argument.
    async def _delete_ids(
        self,
        master: ResourceIndex,
        master_id: snowflakes.Snowflakeish,
        slave: ResourceIndex,
        identifier: RedisValueT,
        *identifiers: RedisValueT,
        reference_key: bool = False,
    ) -> None:
        key = self._generate_reference_key(master, master_id, slave)
        client = await self.get_connection(ResourceIndex.REFERENCE)
        await client.srem(key, identifier, *identifiers)  # TODO: do i need to explicitly delete this if len is 0?

        if reference_key and await client.scard(key) == 1:
            await client.delete(key)

    async def _dump_relationship(
        self, master: ResourceIndex, slave: ResourceIndex
    ) -> typing.MutableMapping[bytes, typing.MutableSequence[bytes]]:
        client = await self.get_connection(ResourceIndex.REFERENCE)
        keys = await client.keys(pattern=f"{master}:*:{slave}")
        values = await asyncio.gather(*map(client.smembers, keys))
        references = {keys[index]: value for index, value in enumerate(values)}
        asyncio.gather(*(client.srem(key, *members) for key, members in references.items() if members))
        return references

    async def _get_ids(
        self,
        master: ResourceIndex,
        master_id: snowflakes.Snowflakeish,
        slave: ResourceIndex,
        *,
        cast: typing.Callable[[bytes], _ValueT],
    ) -> typing.MutableSequence[_ValueT]:
        key = self._generate_reference_key(master, master_id, slave)
        client = await self.get_connection(ResourceIndex.REFERENCE)
        return [*map(cast, await client.smembers(key))]


# To avoid breaking Mro conflicts this is kept as a separate class despite only being exposed through the UserCache.
class _MeCache(ResourceClient, traits.MeCache):
    __slots__: typing.Sequence[str] = ()

    __ME_KEY: typing.Final[str] = "ME"

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        # This isn't a full MeCache implementation in itself as it's reliant on the UserCache implementation.
        return ()

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
            self.dispatch.dispatcher.subscribe(user_events.OwnUserUpdateEvent, self.__on_own_user_update)
            self.dispatch.dispatcher.subscribe(shard_events.ShardReadyEvent, self.__on_shard_ready)

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

        return self.marshaller.deserialize_own_user(data)

    async def set_me(self, me: users.OwnUser, /) -> None:
        # <<Inherited docstring from sake.traits.MeCache>>
        data = self.marshaller.serialize_own_user(me)
        client = await self.get_connection(ResourceIndex.USER)
        await client.set(self.__ME_KEY, data)
        await self._optionally_set_user(me)


class UserCache(_MeCache, traits.UserCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        return (ResourceIndex.USER,)

    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        # The users cache is a special case as it doesn't directly map to any events for most user entries.
        super().subscribe_listeners()

    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        # The users cache is a special case as it doesn't directly map to any events for most user entries.
        super().unsubscribe_listeners()

    def with_user_expire(self: ResourceT, expire: typing.Optional[ExpireT], /) -> ResourceT:
        """Set the default expire time for user entries added with this client.

        Parameters
        ----------
        expire : typing.Union[datetime.timedelta, builtins.int, builtins.float]
            The default expire time to add for users in this cache or `builtins.None`
            to set back to the default behaviour.
            This may either be the number of seconds as an int or float (where
            millisecond precision is supported) or a timedelta.

        Returns
        -------
        ResourceT
            The client this is being called on to enable chained calls.
        """
        if expire is not None:
            self.metadata["expire_user"] = _convert_expire_time(expire)

        elif "expire_user" in self.metadata:
            del self.metadata["expire_user"]

        return self

    async def clear_users(self) -> None:
        # <<Inherited docstring from sake.traits.UserCache>>
        client = await self.get_connection(ResourceIndex.USER)
        await client.flushdb()

    async def delete_user(self, user_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.UserCache>>
        client = await self.get_connection(ResourceIndex.USER)
        await client.delete(int(user_id))

    async def get_user(self, user_id: snowflakes.Snowflakeish, /) -> users.User:
        # <<Inherited docstring from sake.traits.UserCache>>
        user_id = int(user_id)
        client = await self.get_connection(ResourceIndex.USER)
        data = await client.get(user_id)

        if not data:
            raise errors.EntryNotFound(f"User entry `{user_id}` not found")

        return self.marshaller.deserialize_user(data)

    def iter_users(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[users.User]:
        # <<Inherited docstring from sake.traits.UserCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.USER, self.marshaller.deserialize_user, window_size=window_size
        )

    async def set_user(self, user: users.User, /, *, expire_time: typing.Optional[ExpireT] = None) -> None:
        # <<Inherited docstring from sake.traits.UserCache>>
        client = await self.get_connection(ResourceIndex.USER)

        if expire_time is None:
            expire_time = int(self.metadata.get("expire_user", DEFAULT_EXPIRE))

        else:
            expire_time = _convert_expire_time(expire_time)

        await client.set(int(user.id), self.marshaller.serialize_user(user), pexpire=expire_time)


class PrefixCache(ResourceClient, traits.PrefixCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        return (ResourceIndex.PREFIX,)

    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().subscribe_listeners()

    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().unsubscribe_listeners()

    async def clear_prefixes(self) -> None:
        # <<Inherited docstring from sake.traits.PrefixCache>>
        client = await self.get_connection(ResourceIndex.PREFIX)
        await client.flushdb()

    async def clear_prefixes_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.PrefixCache>>
        client = await self.get_connection(ResourceIndex.PREFIX)
        await client.delete(int(guild_id))
    
    async def delete_prefixes(self, guild_id: snowflakes.Snowflakeish, prefix: str, /, *prefixes: str) -> None:
        # <<Inherited docstring from sake.traits.PrefixCache>>
        client = await self.get_connection(ResourceIndex.PREFIX)
        await client.srem(int(guild_id), prefix, *prefixes)

    async def get_prefixes(self, guild_id: snowflakes.Snowflakeish, /) -> typing.List[str]:
        # <<Inherited docstring from sake.traits.PrefixCache>>
        guild_id = int(guild_id)
        client = await self.get_connection(ResourceIndex.PREFIX)
        data = await client.smembers(int(guild_id))
        if not data:
            raise errors.EntryNotFound(f"Prefix entry `{guild_id}` not found")

        return self.marshaller.deserialize_prefixes(data)

    def iter_prefixes(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[typing.List[str]]:
        # <<Inherited docstring from sake.traits.PrefixCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.PREFIX, self.marshaller.deserialize_prefixes, window_size=window_size
        )
        
    async def add_prefixes(self, guild_id: snowflakes.Snowflakeish, prefix: str, /, *prefixes: str) -> None:
        # <<Inherited docstring from sake.traits.PrefixCache>>
        client = await self.get_connection(ResourceIndex.PREFIX)
        await client.sadd(int(guild_id), prefix, *prefixes)

    async def set_prefixes(self, guild_id: snowflakes.Snowflakeish, prefixes: typing.Iterable[str], /) -> None:
        # <<Inherited docstring from sake.traits.PrefixCache>>
        await self.clear_prefixes_for_guild(guild_id)
        await self.add_prefixes(guild_id, *prefixes)


class EmojiCache(_Reference, traits.RefEmojiCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        return (ResourceIndex.EMOJI,)

    async def __bulk_add_emojis(
        self, emojis: typing.Iterable[emojis_.KnownCustomEmoji], guild_id: snowflakes.Snowflake
    ) -> None:
        client = await self.get_connection(ResourceIndex.EMOJI)
        windows = redis_iterators.chunk_values(emojis)
        setters = (
            client.mset({int(emoji.id): self.marshaller.serialize_emoji(emoji) for emoji in window})
            for window in windows
        )
        user_setter = self._optionally_bulk_set_users(emoji.user for emoji in emojis if emoji.user is not None)
        reference_setter = self._add_ids(
            ResourceIndex.GUILD, guild_id, ResourceIndex.EMOJI, *(int(emoji.id) for emoji in emojis)
        )
        asyncio.gather(*setters, user_setter, reference_setter)

    async def __on_emojis_update(self, event: guild_events.EmojisUpdateEvent) -> None:
        await self.clear_emojis_for_guild(event.guild_id)

        if event.emojis:
            await self.__bulk_add_emojis(event.emojis, event.guild_id)

    async def __on_guild_visibility_event(self, event: guild_events.GuildVisibilityEvent) -> None:
        # A guild going unavailable is as far as I'm concerned completely irrelevant.
        # Especially since we clear relevant entries on available and other entries are set to expire.
        if isinstance(event, guild_events.GuildUnavailableEvent):
            return

        await self.clear_emojis_for_guild(event.guild_id)
        if isinstance(event, (guild_events.GuildAvailableEvent, guild_events.GuildUpdateEvent)) and event.emojis:
            await self.__bulk_add_emojis(event.emojis.values(), event.guild_id)

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
        emoji_ids = await self._dump_relationship(ResourceIndex.GUILD, ResourceIndex.EMOJI)
        client = await self.get_connection(ResourceIndex.EMOJI)
        asyncio.gather(*itertools.starmap(client.delete, redis_iterators.chunk_values(itertools.chain(emoji_ids))))

    async def clear_emojis_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        emoji_ids = await self._get_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.EMOJI, cast=bytes)
        if not emoji_ids:
            return

        await self._delete_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.EMOJI, *emoji_ids)
        client = await self.get_connection(ResourceIndex.EMOJI)
        # Gather is awaited here to ensure that when internally bulk setting entries after triggering a bulk deletion
        # (based on events) we don't risk deleting entries after we've re-added them.
        await asyncio.gather(*itertools.starmap(client.delete, redis_iterators.chunk_values(emoji_ids)))

    async def delete_emoji(
        self, emoji_id: snowflakes.Snowflakeish, /, *, guild_id: typing.Optional[snowflakes.Snowflakeish] = None
    ) -> None:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        emoji_id = int(emoji_id)
        if guild_id is None:
            try:
                guild_id = (await self.get_emoji(emoji_id)).guild_id
            except errors.EntryNotFound:
                return

        client = await self.get_connection(ResourceIndex.EMOJI)
        await self._delete_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.EMOJI, emoji_id)
        await client.delete(emoji_id)

    async def get_emoji(self, emoji_id: snowflakes.Snowflakeish, /) -> emojis_.KnownCustomEmoji:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        emoji_id = int(emoji_id)
        client = await self.get_connection(ResourceIndex.EMOJI)
        data = await client.get(emoji_id)

        if not data:
            raise errors.EntryNotFound(f"Emoji entry `{emoji_id}` not found")

        return self.marshaller.deserialize_emoji(data)

    def iter_emojis(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[emojis_.KnownCustomEmoji]:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.EMOJI, self.marshaller.deserialize_emoji, window_size=window_size
        )

    def iter_emojis_for_guild(
        self, guild_id: snowflakes.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> traits.CacheIterator[emojis_.KnownCustomEmoji]:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        key = self._generate_reference_key(ResourceIndex.GUILD, guild_id, ResourceIndex.EMOJI)
        return redis_iterators.ReferenceIterator(
            self, key, ResourceIndex.EMOJI, self.marshaller.deserialize_emoji, window_size=window_size
        )

    async def set_emoji(self, emoji: emojis_.KnownCustomEmoji, /) -> None:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        client = await self.get_connection(ResourceIndex.EMOJI)
        data = self.marshaller.serialize_emoji(emoji)
        await self._add_ids(ResourceIndex.GUILD, emoji.guild_id, ResourceIndex.EMOJI, int(emoji.id))
        await client.set(int(emoji.id), data)

        if emoji.user is not None:
            await self._optionally_set_user(emoji.user)


class GuildCache(ResourceClient, traits.GuildCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        return (ResourceIndex.GUILD,)

    async def __on_guild_visibility_event(self, event: guild_events.GuildVisibilityEvent) -> None:
        client = await self.get_connection(ResourceIndex.GUILD)
        if isinstance(event, guild_events.GuildAvailableEvent):
            data = self.marshaller.serialize_guild(event.guild)
            await client.set(int(event.guild_id), data)

        elif isinstance(event, guild_events.GuildLeaveEvent):
            await client.delete(int(event.guild_id))

    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().subscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.subscribe(guild_events.GuildVisibilityEvent, self.__on_guild_visibility_event)

    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().unsubscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.unsubscribe(guild_events.GuildVisibilityEvent, self.__on_guild_visibility_event)

    async def clear_guilds(self) -> None:
        # <<Inherited docstring from sake.traits.GuildCache>>
        client = await self.get_connection(ResourceIndex.GUILD)
        await client.flushdb()

    async def delete_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.GuildCache>>
        client = await self.get_connection(ResourceIndex.GUILD)
        await client.delete(int(guild_id))

    async def get_guild(self, guild_id: snowflakes.Snowflakeish, /) -> guilds.GatewayGuild:
        # <<Inherited docstring from sake.traits.GuildCache>>
        guild_id = int(guild_id)
        client = await self.get_connection(ResourceIndex.GUILD)
        data = await client.get(guild_id)

        if not data:
            raise errors.EntryNotFound(f"Guild entry `{guild_id}` not found")

        return self.marshaller.deserialize_guild(data)

    def iter_guilds(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[guilds.GatewayGuild]:
        # <<Inherited docstring from sake.traits.GuildCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.GUILD, self.marshaller.deserialize_guild, window_size=window_size
        )

    async def set_guild(self, guild: guilds.GatewayGuild, /) -> None:
        # <<Inherited docstring from sake.traits.GuildCache>>
        client = await self.get_connection(ResourceIndex.GUILD)
        data = self.marshaller.serialize_guild(guild)
        await client.set(int(guild.id), data)


class GuildChannelCache(_Reference, traits.RefGuildChannelCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from sake.traits.Resource>>
        return (ResourceIndex.CHANNEL,)

    async def __on_guild_channel_event(self, event: channel_events.GuildChannelEvent) -> None:
        if isinstance(event, (channel_events.GuildChannelCreateEvent, channel_events.GuildChannelUpdateEvent)):
            await self.set_guild_channel(event.channel)

        elif isinstance(event, channel_events.GuildChannelDeleteEvent):
            await self.delete_guild_channel(event.channel_id, guild_id=event.guild_id)

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

    async def __on_guild_visibility_event(self, event: guild_events.GuildVisibilityEvent) -> None:
        # A guild going unavailable is as far as I'm concerned completely irrelevant.
        # Especially since we clear relevant entries on available and other entries are set to expire.
        if isinstance(event, guild_events.GuildUnavailableEvent):
            return

        await self.clear_guild_channels_for_guild(event.guild_id)
        if isinstance(event, guild_events.GuildAvailableEvent) and event.channels:
            client = await self.get_connection(ResourceIndex.CHANNEL)
            windows = redis_iterators.chunk_values(event.channels.items())
            setters = (
                client.mset(_cast_map_window(window, int, self.marshaller.serialize_guild_channel))
                for window in windows
            )
            id_setter = self._add_ids(
                ResourceIndex.GUILD, event.guild_id, ResourceIndex.CHANNEL, *map(int, event.channels.keys())
            )
            asyncio.gather(*setters, id_setter)

    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().subscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.subscribe(channel_events.GuildChannelEvent, self.__on_guild_channel_event)
            self.dispatch.dispatcher.subscribe(guild_events.GuildVisibilityEvent, self.__on_guild_visibility_event)

    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().unsubscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.unsubscribe(channel_events.GuildChannelEvent, self.__on_guild_channel_event)
            self.dispatch.dispatcher.unsubscribe(guild_events.GuildVisibilityEvent, self.__on_guild_visibility_event)

    async def clear_guild_channels(self) -> None:
        # <<Inherited docstring from sake.traits.GuildChannelCache>>
        channel_ids = await self._dump_relationship(ResourceIndex.GUILD, ResourceIndex.CHANNEL)
        client = await self.get_connection(ResourceIndex.CHANNEL)
        asyncio.gather(*itertools.starmap(client.delete, redis_iterators.chunk_values(itertools.chain(channel_ids))))

    async def clear_guild_channels_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.GuildChannelCache>>
        guild_id = int(guild_id)
        channel_ids = await self._get_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.CHANNEL, cast=bytes)
        if not channel_ids:
            return

        await self._delete_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.CHANNEL, *channel_ids)
        client = await self.get_connection(ResourceIndex.CHANNEL)
        #  TODO: is there any benefit to chunking on bulk delete?
        # Gather is awaited here to ensure that when internally bulk setting entries after triggering a bulk deletion
        # (based on events) we don't risk deleting entries after we've re-added them.
        await asyncio.gather(*itertools.starmap(client.delete, redis_iterators.chunk_values(channel_ids)))

    async def delete_guild_channel(
        self, channel_id: snowflakes.Snowflakeish, /, *, guild_id: typing.Optional[snowflakes.Snowflakeish] = None
    ) -> None:
        # <<Inherited docstring from sake.traits.GuildChannelCache>>
        channel_id = int(channel_id)
        if guild_id is None:
            try:
                guild_id = (await self.get_guild_channel(channel_id)).guild_id
            except errors.EntryNotFound:
                return

        client = await self.get_connection(ResourceIndex.CHANNEL)
        await self._delete_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.CHANNEL, channel_id)
        await client.delete(channel_id)

    async def get_guild_channel(self, channel_id: snowflakes.Snowflakeish, /) -> channels.GuildChannel:
        # <<Inherited docstring from sake.traits.GuildChannelCache>>
        channel_id = int(channel_id)
        client = await self.get_connection(ResourceIndex.CHANNEL)
        data = await client.get(channel_id)

        if not data:
            raise errors.EntryNotFound(f"Guild channel entry `{channel_id}` not found")

        return self.marshaller.deserialize_guild_channel(data)

    def iter_guild_channels(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[channels.GuildChannel]:
        # <<Inherited docstring from sake.traits.GuildChannelCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.CHANNEL, self.marshaller.deserialize_guild_channel, window_size=window_size
        )

    def iter_guild_channels_for_guild(
        self, guild_id: snowflakes.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> traits.CacheIterator[channels.GuildChannel]:
        # <<Inherited docstring from sake.traits.GuildChannelCache>>
        key = self._generate_reference_key(ResourceIndex.GUILD, guild_id, ResourceIndex.CHANNEL)
        return redis_iterators.ReferenceIterator(
            self, key, ResourceIndex.CHANNEL, self.marshaller.deserialize_guild_channel, window_size=window_size
        )

    async def set_guild_channel(self, channel: channels.GuildChannel, /) -> None:
        # <<Inherited docstring from sake.traits.GuildChannelCache>>
        client = await self.get_connection(ResourceIndex.CHANNEL)
        data = self.marshaller.serialize_guild_channel(channel)
        await client.set(int(channel.id), data)
        await self._add_ids(ResourceIndex.GUILD, int(channel.guild_id), ResourceIndex.CHANNEL, int(channel.id))


class IntegrationCache(_Reference, traits.IntegrationCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from sake.traits.Resource>>
        return (ResourceIndex.INTEGRATION,)

    async def __on_guild_leave_event(self, event: guild_events.GuildLeaveEvent) -> None:
        await self.clear_integrations_for_guild(event.guild_id)

    async def __on_integration_event(self, event: guild_events.IntegrationEvent) -> None:
        if isinstance(event, (guild_events.IntegrationCreateEvent, guild_events.IntegrationUpdateEvent)):
            await self.set_integration(event.integration)

        elif isinstance(event, guild_events.IntegrationDeleteEvent):
            await self.delete_integration(event.id, guild_id=event.guild_id)

    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().subscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.subscribe(guild_events.GuildLeaveEvent, self.__on_guild_leave_event)
            self.dispatch.dispatcher.subscribe(guild_events.IntegrationEvent, self.__on_integration_event)

    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().unsubscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.unsubscribe(guild_events.GuildLeaveEvent, self.__on_guild_leave_event)
            self.dispatch.dispatcher.unsubscribe(guild_events.IntegrationEvent, self.__on_integration_event)

    async def clear_integrations(self) -> None:
        # <<Inherited docstring from sake.traits.IntegrationCache>>
        ids = await self._dump_relationship(ResourceIndex.GUILD, ResourceIndex.INTEGRATION)
        client = await self.get_connection(ResourceIndex.INTEGRATION)
        asyncio.gather(*itertools.starmap(client.delete, redis_iterators.chunk_values(ids)))

    async def clear_integrations_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        guild_id = int(guild_id)
        ids = await self._get_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.INTEGRATION, cast=bytes)
        if not ids:
            return

        await self._delete_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.INTEGRATION, *ids)
        client = await self.get_connection(ResourceIndex.INTEGRATION)
        asyncio.gather(*itertools.starmap(client.delete, redis_iterators.chunk_values(ids)))

    async def delete_integration(
        self, integration_id: snowflakes.Snowflakeish, /, *, guild_id: typing.Optional[snowflakes.Snowflakeish] = None
    ) -> None:
        # <<Inherited docstring from sake.traits.IntegrationCache>>
        integration_id = int(integration_id)
        if guild_id is None:
            try:
                guild_id = (await self.get_integration(integration_id)).guild_id
            except errors.EntryNotFound:
                return

        await self._delete_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.INTEGRATION, integration_id)
        client = await self.get_connection(ResourceIndex.INTEGRATION)
        await client.delete(integration_id)

    async def get_integration(self, integration_id: snowflakes.Snowflakeish, /) -> guilds.Integration:
        # <<Inherited docstring from sake.traits.IntegrationCache>>
        client = await self.get_connection(ResourceIndex.INTEGRATION)
        data = await client.get(int(integration_id))

        if not data:
            raise errors.EntryNotFound(f"Integration entry `{integration_id}` not found")

        return self.marshaller.deserialize_integration(data)

    def iter_integrations(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[guilds.Integration]:
        # <<Inherited docstring from sake.traits.IntegrationCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.INTEGRATION, builder=self.marshaller.deserialize_integration, window_size=window_size
        )

    def iter_integrations_for_guild(
        self, guild_id: snowflakes.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> traits.CacheIterator[guilds.Integration]:
        key = self._generate_reference_key(ResourceIndex.GUILD, guild_id, ResourceIndex.INTEGRATION).encode()
        return redis_iterators.ReferenceIterator(
            self, key, ResourceIndex.INTEGRATION, self.marshaller.deserialize_integration, window_size=window_size
        )

    async def set_integration(self, integration: guilds.Integration, /) -> None:
        # <<Inherited docstring from sake.traits.IntegrationCache>>
        data = self.marshaller.serialize_integration(integration)
        client = await self.get_connection(ResourceIndex.INTEGRATION)
        await client.set(int(integration.id), data)
        await self._add_ids(ResourceIndex.GUILD, integration.guild_id, ResourceIndex.INTEGRATION, int(integration.id))


class InviteCache(ResourceClient, traits.InviteCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from sake.traits.Resource>>
        return (ResourceIndex.INVITE,)

    async def __on_invite_event(self, event: channel_events.InviteEvent) -> None:
        if isinstance(event, channel_events.InviteCreateEvent):
            await self.set_invite(event.invite)

        elif isinstance(event, channel_events.InviteDeleteEvent):
            await self.delete_invite(event.code)

    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().subscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.subscribe(channel_events.InviteEvent, self.__on_invite_event)

    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().unsubscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.unsubscribe(channel_events.InviteEvent, self.__on_invite_event)

    def with_invite_expire(self: ResourceT, expire: typing.Optional[ExpireT], /) -> ResourceT:
        """Set the default expire time for invite entries added with this client.

        Parameters
        ----------
        expire : typing.Union[datetime.timedelta, builtins.int, builtins.float]
            The default expire time to add for invites in this cache or `builtins.None`
            to set back to the default behaviour.
            This may either be the number of seconds as an int or float (where
            millisecond precision is supported) or a timedelta.

        Returns
        -------
        ResourceT
            The client this is being called on to enable chained calls.
        """
        if expire is not None:
            self.metadata["expire_invite"] = _convert_expire_time(expire)

        elif "expire_invite" in self.metadata:
            del self.metadata["expire_invite"]

        return self

    async def clear_invites(self) -> None:
        # <<Inherited docstring from sake.traits.InviteCache>>
        client = await self.get_connection(ResourceIndex.INVITE)
        await client.flushdb()

    async def delete_invite(self, invite_code: str, /) -> None:
        # <<Inherited docstring from sake.traits.InviteCache>>
        client = await self.get_connection(ResourceIndex.INVITE)
        # Aioredis treats keys and values as type invariant so we want to ensure this is a str and not a class which
        # subclasses str.
        await client.delete(str(invite_code))

    async def get_invite(self, invite_code: str, /) -> invites_.InviteWithMetadata:
        # <<Inherited docstring from sake.traits.InviteCache>>
        # Aioredis treats keys and values as type invariant so we want to ensure this is a str and not a class which
        # subclasses str.
        invite_code = str(invite_code)
        client = await self.get_connection(ResourceIndex.INVITE)
        data = await client.get(invite_code)
        if not data:
            raise errors.EntryNotFound(f"Invite entry `{invite_code}` not found")

        return self.marshaller.deserialize_invite(data)

    def iter_invites(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[invites_.InviteWithMetadata]:
        # <<Inherited docstring from sake.traits.InviteCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.INVITE, self.marshaller.deserialize_invite, window_size=window_size
        )

    async def set_invite(
        self, invite: invites_.InviteWithMetadata, /, *, expire_time: typing.Optional[ExpireT] = None
    ) -> None:
        # <<Inherited docstring from sake.traits.InviteCache>>
        client = await self.get_connection(ResourceIndex.INVITE)
        data = self.marshaller.serialize_invite(invite)

        if expire_time is None and invite.expires_at is not None:
            expire_time = round((invite.expires_at - time.utc_datetime()).total_seconds() * 1000)

            # If this invite has already expired or the system clock is direly out of sync then we cannot
            # use the expire time we just calculated.
            if expire_time <= 0:
                expire_time = None

        if expire_time is None:
            expire_time = int(self.metadata.get("expire_invite", DEFAULT_INVITE_EXPIRE))

        else:
            expire_time = _convert_expire_time(expire_time)

        # Aioredis treats keys and values as type invariant so we want to ensure this is a str and not a class which
        # subclasses str.
        await client.set(str(invite.code), data, pexpire=expire_time)

        if invite.target_user is not None:
            await self._optionally_set_user(invite.target_user)

        if invite.inviter is not None:
            await self._optionally_set_user(invite.inviter)


class MemberCache(ResourceClient, traits.MemberCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from sake.traits.Resource>>
        return (ResourceIndex.MEMBER,)

    async def __bulk_add_members(
        self, guild_id: snowflakes.Snowflakeish, members: typing.Mapping[snowflakes.Snowflake, guilds.Member]
    ) -> None:
        client = await self.get_connection(ResourceIndex.MEMBER)
        windows = redis_iterators.chunk_values(members.items())
        setters = (
            client.hmset_dict(int(guild_id), _cast_map_window(window, int, self.marshaller.serialize_member))
            for window in windows
        )
        user_setter = self._optionally_bulk_set_users(member.user for member in members.values())
        asyncio.gather(*setters, user_setter)

    async def __on_guild_availability(self, event: guild_events.GuildAvailableEvent) -> None:
        await self.clear_members_for_guild(event.guild_id)
        if event.members:
            await self.__bulk_add_members(event.guild_id, event.members)

    async def __on_member_event(self, event: member_events.MemberEvent) -> None:
        if isinstance(event, (member_events.MemberCreateEvent, member_events.MemberUpdateEvent)):
            await self.set_member(event.member)

        elif isinstance(event, member_events.MemberDeleteEvent):
            back_off = backoff.Backoff()
            async for _ in back_off:
                if "own_id" in self.metadata:
                    break

                try:
                    user = await self.rest.rest.fetch_my_user()

                except hikari_errors.RateLimitedError as exc:
                    back_off.set_next_backoff(exc.retry_after)

                except hikari_errors.InternalServerError:
                    pass

                else:
                    self.metadata["own_id"] = user.id
                    break

            own_id = snowflakes.Snowflake(self.metadata["own_id"])
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

    async def clear_members_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.MemberCache>>
        client = await self.get_connection(ResourceIndex.MEMBER)
        await client.delete(int(guild_id))

    async def delete_member(self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.MemberCache>>
        client = await self.get_connection(ResourceIndex.MEMBER)
        await client.hdel(int(guild_id), int(user_id))

    async def get_member(self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /) -> guilds.Member:
        # <<Inherited docstring from sake.traits.MemberCache>>
        guild_id = int(guild_id)
        user_id = int(user_id)
        client = await self.get_connection(ResourceIndex.MEMBER)
        data = await client.hget(guild_id, user_id)

        if not data:
            raise errors.EntryNotFound(f"Member entry `{user_id}` not found for guild `{guild_id}`")

        return self.marshaller.deserialize_member(data)

    def iter_members(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[guilds.Member]:
        # <<Inherited docstring from sake.traits.MemberCache>>
        return redis_iterators.MultiMapIterator(
            self, ResourceIndex.MEMBER, self.marshaller.deserialize_member, window_size=window_size
        )

    def iter_members_for_guild(
        self, guild_id: snowflakes.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> traits.CacheIterator[guilds.Member]:
        # <<Inherited docstring from sake.traits.MemberCache>>
        return redis_iterators.SpecificMapIterator(
            self,
            int(guild_id),
            ResourceIndex.MEMBER,
            self.marshaller.deserialize_member,
            window_size=window_size,
        )

    async def set_member(self, member: guilds.Member, /) -> None:
        # <<Inherited docstring from sake.traits.MemberCache>>
        client = await self.get_connection(ResourceIndex.MEMBER)
        data = self.marshaller.serialize_member(member)
        await client.hset(int(member.guild_id), int(member.user.id), data)
        await self._optionally_set_user(member.user)


class MessageCache(ResourceClient, traits.MessageCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from sake.traits.Resource>>
        return (ResourceIndex.MESSAGE,)

    async def __on_message_event(self, event: message_events.MessageEvent) -> None:
        if isinstance(event, message_events.MessageCreateEvent):
            await self.set_message(event.message)

        elif isinstance(event, message_events.MessageUpdateEvent):
            await self.update_message(event.message)

        elif isinstance(event, message_events.MessageDeleteEvent):
            client = await self.get_connection(ResourceIndex.MESSAGE)
            asyncio.gather(*itertools.starmap(client.delete, redis_iterators.chunk_values(map(int, event.message_ids))))

    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().subscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.subscribe(message_events.MessageEvent, self.__on_message_event)

    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().unsubscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.unsubscribe(message_events.MessageEvent, self.__on_message_event)

    def with_message_expire(self: ResourceT, expire: typing.Optional[ExpireT], /) -> ResourceT:
        """Set the default expire time for message entries added with this client.

        Parameters
        ----------
        expire : typing.Union[datetime.timedelta, builtins.int, builtins.float]
            The default expire time to add for messages in this cache or `builtins.None`
            to set back to the default behaviour.
            This may either be the number of seconds as an int or float (where
            millisecond precision is supported) or a timedelta.

        Returns
        -------
        ResourceT
            The client this is being called on to enable chained calls.
        """
        if expire is not None:
            self.metadata["expire_message"] = _convert_expire_time(expire)

        elif "expire_message" in self.metadata:
            del self.metadata["expire_message"]

        return self

    async def clear_messages(self) -> None:
        # <<Inherited docstring from sake.traits.MessageCache>>
        client = await self.get_connection(ResourceIndex.INVITE)
        await client.flushdb()

    async def delete_message(self, message_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.MessageCache>>
        client = await self.get_connection(ResourceIndex.MESSAGE)
        await client.delete(int(message_id))

    async def get_message(self, message_id: snowflakes.Snowflakeish, /) -> messages.Message:
        # <<Inherited docstring from sake.traits.MessageCache>>
        message_id = int(message_id)
        client = await self.get_connection(ResourceIndex.MESSAGE)
        data = await client.get(message_id)
        if not data:
            raise errors.EntryNotFound(f"Message entry `{message_id}` not found")

        return self.marshaller.deserialize_message(data)

    def iter_messages(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[messages.Message]:
        # <<Inherited docstring from sake.traits.MessageCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.MESSAGE, self.marshaller.deserialize_message, window_size=window_size
        )

    async def set_message(self, message: messages.Message, /, *, expire_time: typing.Optional[ExpireT] = None) -> None:
        # <<Inherited docstring from sake.traits.MessageCache>>
        data = await self.marshaller.serialize_message(message)
        client = await self.get_connection(ResourceIndex.MESSAGE)

        if expire_time is None:
            expire_time = int(self.metadata.get("expire_message", DEFAULT_FAST_EXPIRE))

        else:
            expire_time = _convert_expire_time(expire_time)

        await client.set(int(message.id), data, expire=expire_time)
        await self._optionally_set_user(message.author)

    async def update_message(self, message: messages.PartialMessage, /) -> bool:
        # <<Inherited docstring from sake.traits.MessageCache>>
        # This is a special case method for handling the partial message updates we get
        try:
            full_message = await self.get_message(message.id)
        except errors.EntryNotFound:
            return False

        if message.content is not undefined.UNDEFINED:
            full_message.content = message.content

        if message.timestamp is not undefined.UNDEFINED:
            full_message.timestamp = message.timestamp

        if message.edited_timestamp is not undefined.UNDEFINED:
            full_message.edited_timestamp = message.edited_timestamp

        if message.is_tts is not undefined.UNDEFINED:
            full_message.is_tts = message.is_tts

        if message.is_mentioning_everyone is not undefined.UNDEFINED:
            full_message.is_mentioning_everyone = message.is_mentioning_everyone

        if message.user_mentions is not undefined.UNDEFINED:
            full_message.user_mentions = message.user_mentions

        if message.role_mentions is not undefined.UNDEFINED:
            full_message.role_mentions = message.role_mentions

        if message.channel_mentions is not undefined.UNDEFINED:
            full_message.channel_mentions = message.channel_mentions

        if message.attachments is not undefined.UNDEFINED:
            full_message.attachments = message.attachments

        if message.embeds is not undefined.UNDEFINED:
            full_message.embeds = message.embeds

        if message.reactions is not undefined.UNDEFINED:
            full_message.reactions = message.reactions

        if message.is_pinned is not undefined.UNDEFINED:
            full_message.is_pinned = message.is_pinned

        if message.webhook_id is not undefined.UNDEFINED:
            full_message.webhook_id = message.webhook_id

        if message.type is not undefined.UNDEFINED:
            full_message.type = message.type

        if message.activity is not undefined.UNDEFINED:
            full_message.activity = message.activity

        if message.application is not undefined.UNDEFINED:
            full_message.application = message.application

        if message.message_reference is not undefined.UNDEFINED:
            full_message.message_reference = message.message_reference

        if message.flags is not undefined.UNDEFINED:
            full_message.flags = message.flags

        if message.nonce is not undefined.UNDEFINED:
            full_message.nonce = message.nonce

        await self.set_message(full_message)
        return True


class PresenceCache(ResourceClient, traits.PresenceCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        return (ResourceIndex.PRESENCE,)

    async def __bulk_add_presences(
        self, guild_id: snowflakes.Snowflake, presences: typing.Mapping[snowflakes.Snowflake, presences_.MemberPresence]
    ) -> None:
        client = await self.get_connection(ResourceIndex.PRESENCE)
        windows = redis_iterators.chunk_values(presences.items())
        setters = (
            client.hmset_dict(int(guild_id), _cast_map_window(window, int, self.marshaller.serialize_presence))
            for window in windows
        )
        asyncio.gather(*setters)

    async def __on_guild_visibility_event(self, event: guild_events.GuildVisibilityEvent) -> None:
        # A guild going unavailable is as far as I'm concerned completely irrelevant.
        # Especially since we clear relevant entries on available and other entries are set to expire.
        if isinstance(event, guild_events.GuildUnavailableEvent):
            return

        await self.clear_presences_for_guild(event.guild_id)
        if isinstance(event, guild_events.GuildAvailableEvent) and event.presences:
            await self.__bulk_add_presences(event.guild_id, event.presences)

    async def __on_member_chunk(self, event: shard_events.MemberChunkEvent) -> None:
        await self.__bulk_add_presences(event.guild_id, event.presences)

    async def __on_presence_update_event(self, event: guild_events.PresenceUpdateEvent) -> None:
        if event.presence.visible_status is presences_.Status.OFFLINE:
            await self.delete_presence(event.guild_id, event.user_id)
            # TODO: handle presence.user?

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
        # <<Inherited docstring from sake.traits.PresenceCache>>
        client = await self.get_connection(ResourceIndex.PRESENCE)
        await client.flushdb()

    async def clear_presences_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.PresenceCache>>
        client = await self.get_connection(ResourceIndex.PRESENCE)
        await client.delete(int(guild_id))

    async def delete_presence(self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.PresenceCache>>
        client = await self.get_connection(ResourceIndex.PRESENCE)
        await client.hdel(int(guild_id), int(user_id))

    async def get_presence(
        self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /
    ) -> presences_.MemberPresence:
        # <<Inherited docstring from sake.traits.PresenceCache>>
        client = await self.get_connection(ResourceIndex.PRESENCE)
        data = await client.hget(int(guild_id), int(user_id))
        return self.marshaller.deserialize_presence(data)

    def iter_presences(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[presences_.MemberPresence]:
        # <<Inherited docstring from sake.traits.PresenceCache>>
        return redis_iterators.MultiMapIterator(
            self, ResourceIndex.PRESENCE, self.marshaller.deserialize_presence, window_size=window_size
        )

    def iter_presences_for_guild(
        self, guild_id: snowflakes.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> traits.CacheIterator[presences_.MemberPresence]:
        # <<Inherited docstring from sake.traits.PresenceCache>>
        return redis_iterators.SpecificMapIterator(
            self,
            int(guild_id),
            ResourceIndex.PRESENCE,
            self.marshaller.deserialize_presence,
            window_size=window_size,
        )

    async def set_presence(self, presence: presences_.MemberPresence, /) -> None:
        # <<Inherited docstring from sake.traits.PresenceCache>>
        data = self.marshaller.serialize_presence(presence)
        client = await self.get_connection(ResourceIndex.PRESENCE)
        await client.hset(int(presence.guild_id), int(presence.user_id), data)


class RoleCache(_Reference, traits.RoleCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        return (ResourceIndex.ROLE,)

    async def __on_guild_visibility_event(self, event: guild_events.GuildVisibilityEvent) -> None:
        # A guild going unavailable is as far as I'm concerned completely irrelevant.
        # Especially since we clear relevant entries on available and other entries are set to expire.
        if isinstance(event, guild_events.GuildUnavailableEvent):
            return

        await self.clear_roles_for_guild(event.guild_id)
        if isinstance(event, (guild_events.GuildAvailableEvent, guild_events.GuildUpdateEvent)) and event.emojis:
            client = await self.get_connection(ResourceIndex.ROLE)
            windows = redis_iterators.chunk_values(event.roles.items())
            setters = (client.mset(_cast_map_window(window, int, self.marshaller.serialize_role)) for window in windows)
            id_setter = self._add_ids(
                ResourceIndex.GUILD, event.guild_id, ResourceIndex.ROLE, *map(int, event.roles.keys())
            )
            asyncio.gather(*setters, id_setter)

    async def __on_role_update(self, event: role_events.RoleEvent) -> None:
        if isinstance(event, (role_events.RoleCreateEvent, role_events.RoleUpdateEvent)):
            await self.set_role(event.role)

        elif isinstance(event, role_events.RoleDeleteEvent):
            await self.delete_role(event.role_id, guild_id=event.guild_id)

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
        references = await self._dump_relationship(ResourceIndex.GUILD, ResourceIndex.ROLE)
        client = await self.get_connection(ResourceIndex.ROLE)
        asyncio.gather(
            *itertools.starmap(client.delete, redis_iterators.chunk_values(itertools.chain(references.values())))
        )

    async def clear_roles_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.RoleCache>>
        role_ids = await self._get_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.ROLE, cast=bytes)
        if not role_ids:
            return

        await self._delete_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.ROLE, *role_ids)
        client = await self.get_connection(ResourceIndex.ROLE)
        # Gather is awaited here to ensure that when internally bulk setting entries after triggering a bulk deletion
        # (based on events) we don't risk deleting entries after we've re-added them.
        await asyncio.gather(*itertools.starmap(client.delete, redis_iterators.chunk_values(role_ids)))

    async def delete_role(
        self, role_id: snowflakes.Snowflakeish, /, *, guild_id: typing.Optional[snowflakes.Snowflakeish] = None
    ) -> None:
        # <<Inherited docstring from sake.traits.RoleCache>>
        role_id = int(role_id)
        if guild_id is None:
            try:
                guild_id = (await self.get_role(role_id)).guild_id
            except errors.EntryNotFound:
                return

        client = await self.get_connection(ResourceIndex.ROLE)
        await self._delete_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.ROLE, role_id)
        await client.delete(role_id)

    async def get_role(self, role_id: snowflakes.Snowflakeish, /) -> guilds.Role:
        # <<Inherited docstring from sake.traits.RoleCache>>
        role_id = int(role_id)
        client = await self.get_connection(ResourceIndex.ROLE)
        data = await client.get(role_id)

        if not data:
            raise errors.EntryNotFound(f"Role entry `{role_id}` not found")

        return self.marshaller.deserialize_role(data)

    def iter_roles(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[guilds.Role]:
        # <<Inherited docstring from sake.traits.RoleCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.ROLE, self.marshaller.deserialize_role, window_size=window_size
        )

    def iter_roles_for_guild(
        self, guild_id: snowflakes.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> traits.CacheIterator[guilds.Role]:
        # <<Inherited docstring from sake.traits.RoleCache>>
        key = self._generate_reference_key(ResourceIndex.GUILD, guild_id, ResourceIndex.ROLE)
        return redis_iterators.ReferenceIterator(
            self, key, ResourceIndex.ROLE, self.marshaller.deserialize_role, window_size=window_size
        )

    async def set_role(self, role: guilds.Role, /) -> None:
        # <<Inherited docstring from sake.traits.RoleCache>>
        client = await self.get_connection(ResourceIndex.ROLE)
        await client.set(int(role.id), self.marshaller.serialize_role(role))
        await self._add_ids(ResourceIndex.GUILD, role.guild_id, ResourceIndex.ROLE, int(role.id))


class VoiceStateCache(_Reference, traits.VoiceStateCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from sake.traits.Resource>>
        return (ResourceIndex.VOICE_STATE,)

    async def __on_guild_channel_delete_event(self, event: channel_events.GuildChannelDeleteEvent) -> None:
        await self.clear_voice_states_for_channel(event.channel_id)

    @staticmethod
    def __generate_references(
        voice_states: typing.Iterable[voices.VoiceState],
        *,
        include_reference_key: bool,
    ) -> typing.MutableMapping[snowflakes.Snowflake, typing.MutableSet[str]]:
        references: typing.MutableMapping[snowflakes.Snowflake, typing.MutableSet[str]] = {}
        for state in voice_states:
            assert state.channel_id is not None, "This channel ID shouldn't ever be None"
            if state.channel_id not in references:
                references[state.channel_id] = set()
                if not include_reference_key:
                    continue

                references[state.channel_id].add(redis_iterators.HashReferenceIterator.hash_key(state.guild_id))

            references[state.channel_id].add(str(state.user_id))

        return references

    async def __on_guild_visibility_event(self, event: guild_events.GuildVisibilityEvent) -> None:
        # A guild going unavailable is as far as I'm concerned completely irrelevant.
        # Especially since we clear relevant entries on available and other entries are set to expire.
        if isinstance(event, guild_events.GuildUnavailableEvent):
            return

        await self.clear_voice_states_for_guild(event.guild_id)
        if isinstance(event, guild_events.GuildAvailableEvent) and event.voice_states:
            client = await self.get_connection(ResourceIndex.VOICE_STATE)
            windows = redis_iterators.chunk_values(event.voice_states.items())
            setters = (
                client.hmset_dict(
                    int(event.guild_id), _cast_map_window(window, int, self.marshaller.serialize_voice_state)
                )
                for window in windows
            )

            references = self.__generate_references(event.voice_states.values(), include_reference_key=True)
            reference_setters = (
                self._add_ids(ResourceIndex.CHANNEL, channel_id, ResourceIndex.VOICE_STATE, *state_ids)
                for channel_id, state_ids in references.items()
                if state_ids
            )
            user_setter = self._optionally_bulk_set_users(state.member.user for state in event.voice_states.values())
            asyncio.gather(*setters, user_setter, *reference_setters)

    async def __on_voice_state_update(self, event: voice_events.VoiceStateUpdateEvent) -> None:
        if event.state.channel_id is None:
            await self.delete_voice_state(event.state.guild_id, event.state.user_id)
        else:
            await self.set_voice_state(event.state)

    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().subscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.subscribe(
                channel_events.GuildChannelDeleteEvent, self.__on_guild_channel_delete_event
            )
            self.dispatch.dispatcher.subscribe(guild_events.GuildVisibilityEvent, self.__on_guild_visibility_event)
            self.dispatch.dispatcher.subscribe(voice_events.VoiceStateUpdateEvent, self.__on_voice_state_update)

    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().unsubscribe_listeners()
        if self.dispatch is not None:
            self.dispatch.dispatcher.unsubscribe(
                channel_events.GuildChannelDeleteEvent, self.__on_guild_channel_delete_event
            )
            self.dispatch.dispatcher.unsubscribe(guild_events.GuildVisibilityEvent, self.__on_guild_visibility_event)
            self.dispatch.dispatcher.unsubscribe(voice_events.VoiceStateUpdateEvent, self.__on_voice_state_update)

    @staticmethod
    def _pop_reference(keys: typing.MutableSequence[bytes]) -> typing.Tuple[bytes, typing.Sequence[bytes]]:
        for key in keys:
            if key.startswith(b"KEY."):
                keys.remove(key)
                return key[4:], keys

        raise ValueError("Couldn't find reference key")

    async def clear_voice_states(self) -> None:
        # <<Inherited docstring from sake.traits.VoiceStateCache>>
        references = await self._dump_relationship(ResourceIndex.CHANNEL, ResourceIndex.VOICE_STATE)
        client = await self.get_connection(ResourceIndex.VOICE_STATE)
        asyncio.gather(
            *(client.hdel(key, *values) for key, values in map(self._pop_reference, references.values()) if values)
        )

    async def clear_voice_states_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.VoiceStateCache>>
        client = await self.get_connection(ResourceIndex.VOICE_STATE)
        states = await self.iter_voice_states_for_guild(guild_id)
        references = self.__generate_references(states, include_reference_key=False)
        id_deleters = (
            self._delete_ids(ResourceIndex.CHANNEL, key, ResourceIndex.VOICE_STATE, *values, reference_key=True)
            for key, values in references.items()
            if values
        )
        entry_deleters = (
            client.hdel(int(guild_id), *values)
            for values in redis_iterators.chunk_values(int(state.user_id) for state in states)
        )
        # Gather is awaited here to ensure that when internally bulk setting entries after triggering a bulk deletion
        # (based on events) we don't risk deleting entries after we've re-added them.
        await asyncio.gather(*id_deleters, *entry_deleters)

    async def clear_voice_states_for_channel(
        self, channel_id: snowflakes.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> None:
        # <<Inherited docstring from sake.traits.VoiceStateCache>>
        ids = await self._get_ids(ResourceIndex.CHANNEL, channel_id, ResourceIndex.VOICE_STATE, cast=bytes)
        if not ids:
            return

        await self._delete_ids(ResourceIndex.CHANNEL, channel_id, ResourceIndex.VOICE_STATE, *ids, reference_key=True)
        client = await self.get_connection(ResourceIndex.VOICE_STATE)
        asyncio.gather(*itertools.starmap(client.delete, redis_iterators.chunk_values(ids, window_size=window_size)))

    # We don't accept channel_id here to avoid the relationship lookup as channel_id isn't a static value and what we
    # want is the value stored rather than the current or "last" value.
    async def delete_voice_state(self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.VoiceStateCache>>
        user_id = int(user_id)
        client = await self.get_connection(ResourceIndex.VOICE_STATE)

        try:
            voice_state = await self.get_voice_state(guild_id, user_id)
        except errors.EntryNotFound:
            pass
        else:
            assert voice_state.channel_id is not None, "Cached voice states should always have a bound channel"
            await client.hdel(int(guild_id), user_id)
            await self._delete_ids(
                ResourceIndex.CHANNEL,
                voice_state.channel_id,
                ResourceIndex.VOICE_STATE,
                user_id,
                reference_key=True,
            )

    async def get_voice_state(
        self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /
    ) -> voices.VoiceState:
        # <<Inherited docstring from sake.traits.VoiceStateCache>>
        guild_id = int(guild_id)
        user_id = int(user_id)
        client = await self.get_connection(ResourceIndex.VOICE_STATE)
        data = await client.hget(guild_id, user_id)

        if not data:
            raise errors.EntryNotFound(f"Voice state entry `{user_id}` not found for guild `{guild_id}`")

        return self.marshaller.deserialize_voice_state(data)

    def iter_voice_states(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[voices.VoiceState]:
        # <<Inherited docstring from sake.traits.VoiceStateCache>>
        return redis_iterators.MultiMapIterator(
            self, ResourceIndex.VOICE_STATE, self.marshaller.deserialize_voice_state, window_size=window_size
        )

    def iter_voice_states_for_channel(
        self, channel_id: snowflakes.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> traits.CacheIterator[voices.VoiceState]:
        # <<Inherited docstring from sake.traits.VoiceStateCache>>
        key = self._generate_reference_key(ResourceIndex.CHANNEL, channel_id, ResourceIndex.VOICE_STATE)
        return redis_iterators.HashReferenceIterator(
            self,
            key,
            index=ResourceIndex.VOICE_STATE,
            builder=self.marshaller.deserialize_voice_state,
            window_size=window_size,
        )

    def iter_voice_states_for_guild(
        self, guild_id: snowflakes.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> traits.CacheIterator[voices.VoiceState]:
        # <<Inherited docstring from sake.traits.VoiceStateCache>>
        return redis_iterators.SpecificMapIterator(
            self,
            int(guild_id),
            ResourceIndex.VOICE_STATE,
            self.marshaller.deserialize_voice_state,
            window_size=window_size,
        )

    async def set_voice_state(self, voice_state: voices.VoiceState, /) -> None:
        # <<Inherited docstring from sake.traits.VoiceStateCache>>
        if voice_state.channel_id is None:
            raise ValueError("Cannot set a voice state which isn't bound to a channel")

        data = self.marshaller.serialize_voice_state(voice_state)
        # We have to ensure this is deleted first to make sure previous references are removed.
        await self.delete_voice_state(voice_state.guild_id, voice_state.user_id)
        client = await self.get_connection(ResourceIndex.VOICE_STATE)
        await client.hset(int(voice_state.guild_id), int(voice_state.user_id), data)
        await self._add_ids(
            ResourceIndex.CHANNEL,
            voice_state.channel_id,
            ResourceIndex.VOICE_STATE,
            int(voice_state.user_id),
            redis_iterators.HashReferenceIterator.hash_key(voice_state.guild_id),
        )
        await self._optionally_set_user(voice_state.member.user)


class RedisCache(
    GuildCache,
    EmojiCache,
    GuildChannelCache,
    IntegrationCache,
    InviteCache,
    MemberCache,
    MessageCache,
    PresenceCache,
    RoleCache,
    UserCache,
    VoiceStateCache,
    traits.Cache,
):
    """A Redis implementation of all the defined cache resources."""

    __slots__: typing.Sequence[str] = ()
