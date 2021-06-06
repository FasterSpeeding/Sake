# -*- coding: utf-8 -*-
# cython: language_level=3
# BSD 3-Clause License
#
# Copyright (c) 2020-2021, Faster Speeding
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
import inspect
import itertools
import json
import logging
import math
import typing

import aioredis  # type: ignore[import]
from hikari import channels as channels_
from hikari import errors as hikari_errors
from hikari import presences as presences_
from hikari import snowflakes
from hikari import traits as hikari_traits
from hikari import undefined
from hikari.events import channel_events
from hikari.events import guild_events
from hikari.events import lifetime_events
from hikari.events import member_events
from hikari.events import message_events
from hikari.events import role_events
from hikari.events import shard_events
from hikari.internal import time  # TODO: don't use this
from yuyo import backoff

from sake import errors
from sake import redis_iterators
from sake import traits

if typing.TYPE_CHECKING:
    import ssl as ssl_
    import types

    import aioredis.abc  # type: ignore[import]
    from hikari import emojis as emojis_
    from hikari import guilds
    from hikari import invites as invites_
    from hikari import messages
    from hikari import users
    from hikari import voices
    from hikari.api import entity_factory as entity_factory_
    from hikari.events import base_events


_EventT_inv = typing.TypeVar("_EventT_inv", bound="base_events.Event")
_EventT_co = typing.TypeVar("_EventT_co", bound="base_events.Event", covariant=True)
_KeyT = typing.TypeVar("_KeyT")
_OtherKeyT = typing.TypeVar("_OtherKeyT")
_ValueT = typing.TypeVar("_ValueT")
_OtherValueT = typing.TypeVar("_OtherValueT")
ObjectT = typing.Dict[str, typing.Any]
_LOGGER: typing.Final[logging.Logger] = logging.getLogger("hikari.sake.redis")
"""The logger instance used by this Sake implementation."""

RedisKeyT = typing.Union[int, str, bytes]
CallbackT = typing.Callable[["ResourceT", _EventT_co], typing.Coroutine[typing.Any, typing.Any, None]]
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


def _try_find_type(cls: typing.Type[_ValueT], /, *values: typing.Any) -> undefined.UndefinedOr[_ValueT]:
    for value in values:
        if isinstance(value, cls):
            return value

    return undefined.UNDEFINED


def _cast_map_window(
    window: typing.Iterable[typing.Tuple[_KeyT, _ValueT]],
    /,
    key_cast: typing.Callable[[_KeyT], _OtherKeyT],
    value_cast: typing.Callable[[_ValueT], _OtherValueT],
) -> typing.Dict[_OtherKeyT, _OtherValueT]:
    return dict((key_cast(key), value_cast(value)) for key, value in window)


async def _close_client(client: aioredis.Redis, /) -> None:
    # TODO: will we need to catch errors here?
    client.close()


def _convert_expire_time(expire: ExpireT, /) -> typing.Optional[int]:
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


@typing.runtime_checkable
class _ListenerProto(typing.Protocol[_EventT_inv]):
    async def __call__(self, event: _EventT_inv, /) -> None:
        raise NotImplementedError

    @property
    def __sake_event_type__(self) -> typing.Type[_EventT_inv]:
        raise NotImplementedError


@typing.runtime_checkable
class _RawListenerProto(typing.Protocol):
    async def __call__(self, event: shard_events.ShardPayloadEvent, /) -> None:
        raise NotImplementedError

    @property
    def __sake_event_names__(self) -> typing.Sequence[str]:
        raise NotImplementedError


def as_listener(
    event_type: typing.Type[_EventT_co], /
) -> typing.Callable[[CallbackT[ResourceT, _EventT_co]], CallbackT[ResourceT, _EventT_co]]:
    def decorator(listener: CallbackT[ResourceT, _EventT_co], /) -> CallbackT[ResourceT, _EventT_co]:
        listener.__sake_event_type__ = event_type  # type: ignore[attr-defined]
        assert isinstance(listener, _ListenerProto), "Incorrect attributes set for listener"
        return listener  # type: ignore[unreachable]

    return decorator


def as_raw_listener(
    event_name: str, /, *event_names: str
) -> typing.Callable[
    [CallbackT[ResourceT, shard_events.ShardPayloadEvent]], CallbackT[ResourceT, shard_events.ShardPayloadEvent]
]:
    event_names = (event_name, *event_names)

    def decorator(
        listener: CallbackT[ResourceT, shard_events.ShardPayloadEvent], /
    ) -> CallbackT[ResourceT, shard_events.ShardPayloadEvent]:
        listener.__sake_event_names__ = event_names  # type: ignore[attr-defined]
        assert isinstance(listener, _RawListenerProto), "Incorrect attributes set for raw listener"
        return listener  # type: ignore[unreachable]

    return decorator


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
    events : typing.Optional[hikari.traits.EventManagerAware]
        The event manager aware Hikari client to bind this resource client to.
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
        "__dumps",
        "__entity_factory",
        "__event_managed",
        "__event_manager",
        "__listeners",
        "__loads",
        "__metadata",
        "__password",
        "__raw_listeners",
        "__rest",
        "__ssl",
        "__started",
    )

    def __init__(
        self,
        rest: hikari_traits.RESTAware,
        entity_factory: undefined.UndefinedOr[hikari_traits.EntityFactoryAware] = undefined.UNDEFINED,
        event_manager: undefined.UndefinedNoneOr[hikari_traits.EventManagerAware] = undefined.UNDEFINED,
        *,
        default_expire: ExpireT = DEFAULT_SLOW_EXPIRE,
        event_managed: bool = True,
        address: typing.Union[str, typing.Tuple[str, typing.Union[str, int]]],
        password: typing.Optional[str] = None,
        ssl: typing.Union[ssl_.SSLContext, bool, None] = None,
        metadata: typing.Optional[typing.MutableMapping[str, typing.Any]] = None,
        dumps: typing.Callable[[ObjectT], bytes] = lambda obj: json.dumps(obj).encode(),
        loads: typing.Callable[[bytes], ObjectT] = json.loads,
    ) -> None:
        entity_factory = _try_find_type(
            hikari_traits.EntityFactoryAware, entity_factory, rest, event_manager  # type: ignore[misc]
        )

        if entity_factory is undefined.UNDEFINED:
            raise ValueError("An entity factory implementation must be provided")

        if event_manager is undefined.UNDEFINED:
            event_manager = _try_find_type(hikari_traits.EventManagerAware, rest, entity_factory)

            if event_manager is undefined.UNDEFINED:
                raise ValueError("An event manager implementation must be provided")

        elif event_manager is None:
            ...  # TODO: add warning about running statelessly?

        self.__address = address
        self.__clients: typing.Dict[int, aioredis.Redis] = {}
        self.__default_expire = _convert_expire_time(default_expire)
        self.__dumps = dumps
        self.__entity_factory = entity_factory
        self.__event_managed = event_managed
        self.__event_manager = event_manager
        self.__listeners: typing.Dict[
            typing.Type[base_events.Event], typing.List[_ListenerProto[base_events.Event]]
        ] = {}
        self.__index_overrides: typing.Dict[ResourceIndex, int] = {}
        self.__loads = loads
        self.__metadata = metadata or {}
        self.__password = password
        self.__raw_listeners: typing.Dict[str, typing.List[_RawListenerProto]] = {}
        self.__rest = rest
        self.__ssl = ssl
        self.__started = False

        if event_managed and event_manager:
            event_manager.event_manager.subscribe(lifetime_events.StartingEvent, self.__on_starting_event)
            event_manager.event_manager.subscribe(lifetime_events.StoppingEvent, self.__on_stopping_event)

        for _, member in inspect.getmembers(self):
            if isinstance(member, _RawListenerProto):
                for name in member.__sake_event_names__:
                    try:
                        self.__raw_listeners[name].append(member)

                    except KeyError:
                        self.__raw_listeners[name] = [member]

            elif isinstance(member, _ListenerProto):
                try:
                    self.__listeners[member.__sake_event_type__].append(member)

                except KeyError:
                    self.__listeners[member.__sake_event_type__] = [member]

    async def __on_starting_event(self, _: lifetime_events.StartingEvent, /) -> None:
        await self.open()

    async def __on_stopping_event(self, _: lifetime_events.StoppingEvent, /) -> None:
        await self.close()

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

    @property
    def entity_factory(self) -> entity_factory_.EntityFactory:
        return self.__entity_factory.entity_factory

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

    def dump(self, data: ObjectT, /) -> bytes:
        return self.__dumps(data)

    def load(self, data: bytes, /) -> ObjectT:
        return self.__loads(data)

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

    def get_connection(self, resource: ResourceIndex, /) -> aioredis.Redis:
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

    async def _try_bulk_set_users(self, users_: typing.Iterator[ObjectT], /) -> None:
        try:
            client = self.get_connection(ResourceIndex.USER)
        except ValueError:
            pass
        else:
            expire_time = int(self.metadata.get("expire_user", DEFAULT_EXPIRE))
            # user_setters = []
            # expire_setters: typing.MutableSequence[typing.Coroutine[typing.Any, typing.Any, None]] = []

            for window in redis_iterators.chunk_values(users_):
                # transaction = client.multi_exec()
                #
                # for user_id in processed_window.keys():
                #     transaction.pexpire(user_id, expire_time)
                #
                # expire_setters.append(transaction.execute())
                await self._mset(ResourceIndex.USER, _get_id, window)
                await asyncio.gather(*(client.pexpire(_get_id(payload), expire_time) for payload in window))
                #  TODO: benchmark bulk setting expire with transaction vs this
                # user_setters.append(client.mset(processed_window))
                # expire_setters.extend((client.pexpire(user_id, expire_time) for user_id in processed_window.keys()))

            # asyncio.gather(*user_setters)
            # asyncio.gather(*expire_setters)

    async def _try_set_user(self, payload: ObjectT, /) -> None:
        try:
            client = self.get_connection(ResourceIndex.USER)
        except ValueError:
            pass
        else:
            expire_time = int(self.metadata.get("expire_user", DEFAULT_EXPIRE))
            await client.set(int(payload["id"]), self.dump(payload), pexpire=expire_time)

    async def _get(
        self, resource_index: ResourceIndex, key: RedisKeyT, /, *, error: str = "Resource not found"
    ) -> ObjectT:
        client = self.get_connection(resource_index)
        data = await client.get(key)

        if data is None:
            raise errors.EntryNotFound(error)

        assert isinstance(data, bytes)
        return self.load(data)

    async def _hget(
        self,
        resource_index: ResourceIndex,
        inner_key: RedisKeyT,
        outer_key: RedisKeyT,
        /,
        *,
        error: str = "Resource not found",
    ) -> ObjectT:
        client = self.get_connection(resource_index)
        data = await client.hget(inner_key, outer_key)

        if data is None:
            raise errors.EntryNotFound(error)

        assert isinstance(data, bytes)
        return self.load(data)

    async def hmget(
        self, resource_index: ResourceIndex, outer_key: RedisKeyT, inner_key: RedisKeyT, /, *inner_keys: RedisKeyT
    ) -> typing.Iterator[ObjectT]:
        client = await self.get_connection(resource_index)
        data = await client.hmget(outer_key, inner_key, *inner_keys)
        # TODO: won't this also return None or empty responses?
        assert not data or isinstance(data[0], bytes)
        return map(self.load, data)

    async def mget(
        self, resource_index: ResourceIndex, key: RedisKeyT, /, *keys: RedisKeyT
    ) -> typing.Iterator[ObjectT]:
        client = self.get_connection(resource_index)
        data = await client.mget(key, *keys)
        # TODO: won't this also return None or empty responses?
        assert not data or isinstance(data[0], bytes)
        return map(self.load, data)

    async def hscan(
        self,
        resource_index: ResourceIndex,
        key: RedisKeyT,
        /,
        *,
        cursor: int = 0,
        count: typing.Optional[int] = None,
        match: typing.Optional[str] = None,
    ) -> typing.Tuple[int, typing.Iterator[typing.Tuple[RedisKeyT, ObjectT]]]:
        client = self.get_connection(resource_index)
        cursor, window = await client.hscan(key, cursor=cursor, count=count, match=match)
        assert isinstance(cursor, int)
        assert not window or isinstance(window[0][1], bytes)
        return cursor, ((key, self.load(data)) for key, data in window)

    async def _set(
        self,
        resource_index: ResourceIndex,
        key: RedisKeyT,
        payload: ObjectT,
        /,
        *,
        pexpire: typing.Optional[int] = None,
    ) -> None:
        client = self.get_connection(resource_index)
        await client.set(key, self.dump(payload), pexpire=pexpire)

    async def _hset(
        self, resource_index: ResourceIndex, outer_key: RedisKeyT, inner_key: RedisKeyT, payload: ObjectT, /
    ) -> None:
        client = self.get_connection(resource_index)
        # TODO: expire?
        await client.hset(outer_key, inner_key, self.dump(payload))

    async def _hmset_dict(
        self,
        resource_index: ResourceIndex,
        key: RedisKeyT,
        key_getter: typing.Callable[[ObjectT], RedisKeyT],
        payloads: typing.Iterable[ObjectT],
        /,
    ) -> None:
        client = self.get_connection(resource_index)
        await client.hmset_dict(key, {key_getter(payload): self.dump(payload) for payload in payloads})

    async def _mset(
        self,
        resource_index: ResourceIndex,
        key_getter: typing.Callable[[ObjectT], RedisKeyT],
        payloads: typing.Iterable[ObjectT],
        /,
    ) -> None:
        client = self.get_connection(resource_index)
        await client.mset({key_getter(payload): self.dump(payload) for payload in payloads})

    async def _on_shard_payload_event(self, event: shard_events.ShardPayloadEvent, /) -> None:
        if listeners := self.__raw_listeners.get(event.name.upper()):
            await asyncio.gather(*(listener(event) for listener in listeners))

    async def _spawn_connection(self, resource: int, /) -> None:
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

        if self.__event_manager:
            self.__event_manager.event_manager.subscribe(shard_events.ShardPayloadEvent, self._on_shard_payload_event)

            for event_type, listeners in self.__listeners.items():
                for listener in listeners:
                    self.__event_manager.event_manager.subscribe(event_type, listener)

        self.__started = True

    async def close(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        # We want to ensure that we both only do anything here if the client was already started when the method was
        # originally called and also that the client is marked as "closed" before this starts severing connections.
        was_started = self.__started
        self.__started = False

        if not was_started:
            return

        if self.__event_manager:
            try:
                self.__event_manager.event_manager.unsubscribe(
                    shard_events.ShardPayloadEvent, self._on_shard_payload_event
                )
            except LookupError:
                pass

            for event_type, listeners in self.__listeners.items():
                for listener in listeners:
                    try:
                        self.__event_manager.event_manager.unsubscribe(event_type, listener)
                    except LookupError:
                        pass

        clients = self.__clients
        self.__clients = {}
        # Gather is awaited here so we can assure all clients are closed before this returns.
        await asyncio.gather(*map(_close_client, clients.values()))


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
        identifier: RedisKeyT,
        /,
        *identifiers: RedisKeyT,
    ) -> None:
        key = self._generate_reference_key(master, master_id, slave)
        client = self.get_connection(ResourceIndex.REFERENCE)
        await client.sadd(key, identifier, *identifiers)

    # To ensure at least 1 ID is always provided we have a required arg directly before the variable-length argument.
    async def _delete_ids(
        self,
        master: ResourceIndex,
        master_id: snowflakes.Snowflakeish,
        slave: ResourceIndex,
        identifier: RedisKeyT,
        /,
        *identifiers: RedisKeyT,
        reference_key: bool = False,
    ) -> None:
        key = self._generate_reference_key(master, master_id, slave)
        client = self.get_connection(ResourceIndex.REFERENCE)
        await client.srem(key, identifier, *identifiers)  # TODO: do i need to explicitly delete this if len is 0?

        if reference_key and await client.scard(key) == 1:
            await client.delete(key)

    async def _dump_relationship(
        self, master: ResourceIndex, slave: ResourceIndex, /
    ) -> typing.MutableMapping[bytes, typing.MutableSequence[bytes]]:
        client = self.get_connection(ResourceIndex.REFERENCE)
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
        /,
        *,
        cast: typing.Callable[[bytes], _ValueT],
    ) -> typing.MutableSequence[_ValueT]:
        key = self._generate_reference_key(master, master_id, slave)
        client = self.get_connection(ResourceIndex.REFERENCE)
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

    @as_raw_listener("USER_UPDATE")
    async def __on_own_user_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        await self.set_me(dict(event.payload))

    @as_raw_listener("READY")
    async def __on_shard_ready(self, event: shard_events.ShardPayloadEvent, /) -> None:
        await self.set_me(event.payload["user"])

    async def delete_me(self) -> None:
        # <<Inherited docstring from sake.traits.MeCache>>
        client = self.get_connection(ResourceIndex.USER)
        await client.delete(self.__ME_KEY)

    async def get_me(self) -> users.OwnUser:
        # <<Inherited docstring from sake.traits.MeCache>>
        payload = await self._get(ResourceIndex.USER, self.__ME_KEY)
        return self.entity_factory.deserialize_my_user(payload)

    async def set_me(self, payload: ObjectT, /) -> None:
        # <<Inherited docstring from sake.traits.MeCache>>
        await self._set(ResourceIndex.USER, self.__ME_KEY, payload)
        await self._try_set_user(payload)


class UserCache(_MeCache, traits.UserCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        return (ResourceIndex.USER,)

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
        client = self.get_connection(ResourceIndex.USER)
        await client.flushdb()

    async def delete_user(self, user_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.UserCache>>
        client = self.get_connection(ResourceIndex.USER)
        await client.delete(int(user_id))

    async def get_user(self, user_id: snowflakes.Snowflakeish, /) -> users.User:
        # <<Inherited docstring from sake.traits.UserCache>>
        payload = await self._get(ResourceIndex.USER, int(user_id))
        return self.entity_factory.deserialize_user(payload)

    def iter_users(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[users.User]:
        # <<Inherited docstring from sake.traits.UserCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.USER, self.entity_factory.deserialize_user, window_size=window_size
        )

    async def set_user(self, payload: ObjectT, /, *, expire_time: typing.Optional[ExpireT] = None) -> None:
        # <<Inherited docstring from sake.traits.UserCache>>
        if expire_time is None:
            expire_time = int(self.metadata.get("expire_user", DEFAULT_EXPIRE))

        else:
            expire_time = _convert_expire_time(expire_time)

        await self._set(ResourceIndex.USER, int(payload["id"]), payload, pexpire=expire_time)


def _add_guild_id(data: ObjectT, /, guild_id: int) -> ObjectT:
    data["guild_id"] = guild_id
    return data


def _get_id(data: ObjectT, /) -> int:
    return int(data["id"])


class PrefixCache(ResourceClient, traits.PrefixCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        return (ResourceIndex.PREFIX,)

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

    async def __bulk_add_emojis(self, emojis: typing.Iterable[ObjectT], /, guild_id: int) -> None:
        windows = redis_iterators.chunk_values(_add_guild_id(emoji, guild_id) for emoji in emojis)
        setters = (self._mset(ResourceIndex.EMOJI, _get_id, window) for window in windows)
        user_setter = self._try_bulk_set_users(user for payload in emojis if (user := payload.get("user")))
        reference_setter = self._add_ids(
            ResourceIndex.GUILD, guild_id, ResourceIndex.EMOJI, *(int(payload["id"]) for payload in emojis)
        )
        await asyncio.gather(*setters, user_setter, reference_setter)

    @as_raw_listener("GUILD_EMOJIS_UPDATE")
    async def __on_emojis_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        guild_id = int(event.payload["guild_id"])
        await self.clear_emojis_for_guild(guild_id)

        if emojis := event.payload.get("emojis"):
            await self.__bulk_add_emojis(emojis, guild_id)

    #  TODO: can we also listen for member delete to manage this?
    @as_raw_listener("GUILD_CREATE", "GUILD_UPDATE")
    async def __on_guild_create_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        guild_id = int(event.payload["id"])
        await self.clear_emojis_for_guild(guild_id)

        if emojis := event.payload["emojis"]:
            await self.__bulk_add_emojis(emojis, guild_id)

    @as_listener(guild_events.GuildLeaveEvent)
    async def __on_guild_delete(self, event: guild_events.GuildLeaveEvent, /) -> None:
        await self.clear_emojis_for_guild(event.guild_id)

    async def clear_emojis(self) -> None:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        emoji_ids = await self._dump_relationship(ResourceIndex.GUILD, ResourceIndex.EMOJI)
        client = self.get_connection(ResourceIndex.EMOJI)
        await asyncio.gather(
            *itertools.starmap(client.delete, redis_iterators.chunk_values(itertools.chain(emoji_ids)))
        )

    async def clear_emojis_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        emoji_ids = await self._get_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.EMOJI, cast=bytes)
        if not emoji_ids:
            return

        await self._delete_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.EMOJI, *emoji_ids)
        client = self.get_connection(ResourceIndex.EMOJI)
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
                #  TODO: we can avoid unmarshalling all together here now
                guild_id = (await self.get_emoji(emoji_id)).guild_id
            except errors.EntryNotFound:
                return

        client = self.get_connection(ResourceIndex.EMOJI)
        await self._delete_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.EMOJI, emoji_id)
        await client.delete(emoji_id)

    async def get_emoji(self, emoji_id: snowflakes.Snowflakeish, /) -> emojis_.KnownCustomEmoji:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        payload = await self._get(ResourceIndex.EMOJI, int(emoji_id))
        return self._deserialize_known_custom_emoji(payload)

    def _deserialize_known_custom_emoji(self, payload: ObjectT, /) -> emojis_.KnownCustomEmoji:
        guild_id = snowflakes.Snowflake(payload["guild_id"])
        return self.entity_factory.deserialize_known_custom_emoji(payload, guild_id=guild_id)

    def iter_emojis(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[emojis_.KnownCustomEmoji]:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.EMOJI, self._deserialize_known_custom_emoji, window_size=window_size
        )

    def iter_emojis_for_guild(
        self, guild_id: snowflakes.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> traits.CacheIterator[emojis_.KnownCustomEmoji]:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        key = self._generate_reference_key(ResourceIndex.GUILD, guild_id, ResourceIndex.EMOJI)
        return redis_iterators.ReferenceIterator(
            self, key, ResourceIndex.EMOJI, self._deserialize_known_custom_emoji, window_size=window_size
        )

    async def set_emoji(self, payload: ObjectT, /, guild_id: int) -> None:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        guild_id = int(guild_id)
        emoji_id = int(payload["id"])
        await self._set(ResourceIndex.EMOJI, emoji_id, payload)
        await self._add_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.EMOJI, emoji_id)

        if (user := payload.get("user")) is not None:
            await self._try_set_user(user)


class GuildCache(ResourceClient, traits.GuildCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        return (ResourceIndex.GUILD,)

    @as_raw_listener("GUILD_CREATE", "GUILD_UPDATE")
    async def __on_guild_create_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        await self.set_guild(dict(event.payload))

    @as_listener(guild_events.GuildLeaveEvent)
    async def __on_guild_delete(self, event: guild_events.GuildLeaveEvent, /) -> None:
        await self.delete_guild(event.guild_id)

    async def clear_guilds(self) -> None:
        # <<Inherited docstring from sake.traits.GuildCache>>
        client = self.get_connection(ResourceIndex.GUILD)
        await client.flushdb()

    async def delete_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.GuildCache>>
        client = self.get_connection(ResourceIndex.GUILD)
        await client.delete(int(guild_id))

    def _deserialize_guild(self, payload: ObjectT, /) -> guilds.GatewayGuild:
        return self.entity_factory.deserialize_gateway_guild(payload).guild

    async def get_guild(self, guild_id: snowflakes.Snowflakeish, /) -> guilds.GatewayGuild:
        # <<Inherited docstring from sake.traits.GuildCache>>
        payload = await self._get(ResourceIndex.GUILD, int(guild_id))
        return self._deserialize_guild(payload)

    def iter_guilds(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[guilds.GatewayGuild]:
        # <<Inherited docstring from sake.traits.GuildCache>>
        return redis_iterators.Iterator(self, ResourceIndex.GUILD, self._deserialize_guild, window_size=window_size)

    async def set_guild(self, payload: ObjectT, /) -> None:
        # <<Inherited docstring from sake.traits.GuildCache>>
        # These entries are cached in separate stores.
        payload.pop("channels", None)
        payload.pop("emojis", None)
        payload.pop("members", None)
        payload.pop("presences", None)
        payload.pop("roles", None)
        payload.pop("threads", None)
        payload.pop("voice_states", None)
        await self._set(ResourceIndex.GUILD, int(payload["id"]), payload)


# TODO: guild_id isn't always included in the payload?
class GuildChannelCache(_Reference, traits.RefGuildChannelCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from sake.traits.Resource>>
        return (ResourceIndex.CHANNEL,)

    @as_raw_listener("CHANNEL_CREATE", "CHANNEL_UPDATE")
    async def __on_channel_create_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        await self.set_guild_channel(dict(event.payload))

    @as_listener(channel_events.GuildChannelDeleteEvent)  # we don't actually get events for DM channels anymore.
    async def __on_channel_delete(self, event: channel_events.GuildChannelDeleteEvent, /) -> None:
        await self.delete_guild_channel(event.channel_id, guild_id=event.guild_id)

    @as_raw_listener("CHANNEL_PINS_UPDATE")
    async def __on_channel_pins_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        try:
            payload = await self._get(ResourceIndex.CHANNEL, int(event.payload["channel_id"]))
        except errors.EntryNotFound:
            pass
        else:
            payload["last_pin_timestamp"] = event.payload.get("last_pin_timestamp")
            await self.set_guild_channel(payload)

    @as_raw_listener("GUILD_CREATE")  # GUILD_UPDATE doesn't include channels
    async def __on_guild_create_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        guild_id = int(event.payload["id"])
        await self.clear_guild_channels_for_guild(guild_id)

        channel_ids: typing.List[int] = []
        setters: typing.List[typing.Awaitable[None]] = []
        raw_channels = event.payload["channels"]
        for window in redis_iterators.chunk_values(_add_guild_id(payload, guild_id) for payload in raw_channels):
            setters.append(self._mset(ResourceIndex.CHANNEL, _get_id, window))
            channel_ids.extend(int(payload["id"]) for payload in window)

        id_setter = self._add_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.CHANNEL, *channel_ids)
        await asyncio.gather(*setters, id_setter)

    @as_listener(guild_events.GuildLeaveEvent)  # TODO: can we also use member remove events here?
    async def __on_guild_delete(self, event: guild_events.GuildLeaveEvent, /) -> None:
        await self.clear_guild_channels_for_guild(event.guild_id)

    async def clear_guild_channels(self) -> None:
        # <<Inherited docstring from sake.traits.GuildChannelCache>>
        channel_ids = await self._dump_relationship(ResourceIndex.GUILD, ResourceIndex.CHANNEL)
        client = self.get_connection(ResourceIndex.CHANNEL)
        await asyncio.gather(
            *itertools.starmap(client.delete, redis_iterators.chunk_values(itertools.chain(channel_ids)))
        )

    async def clear_guild_channels_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.GuildChannelCache>>
        guild_id = int(guild_id)
        channel_ids = await self._get_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.CHANNEL, cast=bytes)
        if not channel_ids:
            return

        await self._delete_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.CHANNEL, *channel_ids)
        client = self.get_connection(ResourceIndex.CHANNEL)
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
                # TODO: we can avoid deserialization in cases like this now since we know the loader and dumpers
                guild_id = (await self.get_guild_channel(channel_id)).guild_id
            except errors.EntryNotFound:
                return

        client = self.get_connection(ResourceIndex.CHANNEL)
        await self._delete_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.CHANNEL, channel_id)
        await client.delete(channel_id)

    async def get_guild_channel(self, channel_id: snowflakes.Snowflakeish, /) -> channels_.GuildChannel:
        # <<Inherited docstring from sake.traits.GuildChannelCache>>
        payload = await self._get(ResourceIndex.CHANNEL, int(channel_id))
        return self._deserialize_guild_channel(payload)

    def _deserialize_guild_channel(self, payload: ObjectT, /) -> channels_.GuildChannel:
        channel = self.entity_factory.deserialize_channel(payload)
        assert isinstance(channel, channels_.GuildChannel)
        return channel

    def iter_guild_channels(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[channels_.GuildChannel]:
        # <<Inherited docstring from sake.traits.GuildChannelCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.CHANNEL, self._deserialize_guild_channel, window_size=window_size
        )

    def iter_guild_channels_for_guild(
        self, guild_id: snowflakes.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> traits.CacheIterator[channels_.GuildChannel]:
        # <<Inherited docstring from sake.traits.GuildChannelCache>>
        key = self._generate_reference_key(ResourceIndex.GUILD, guild_id, ResourceIndex.CHANNEL)
        return redis_iterators.ReferenceIterator(
            self, key, ResourceIndex.CHANNEL, self._deserialize_guild_channel, window_size=window_size
        )

    async def set_guild_channel(self, payload: ObjectT, /) -> None:
        # <<Inherited docstring from sake.traits.GuildChannelCache>>
        channel_id = int(payload["id"])
        guild_id = int(payload["guild_id"])
        await self._set(ResourceIndex.CHANNEL, channel_id, payload)
        await self._add_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.CHANNEL, channel_id)


class IntegrationCache(_Reference, traits.IntegrationCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from sake.traits.Resource>>
        return (ResourceIndex.INTEGRATION,)

    @as_listener(guild_events.GuildLeaveEvent)
    async def __on_guild_delete_event(self, event: guild_events.GuildLeaveEvent, /) -> None:
        await self.clear_integrations_for_guild(event.guild_id)

    @as_raw_listener("INTEGRATION_CREATE", "INTEGRATION_UPDATE")
    async def __on_integration_event(self, event: shard_events.ShardPayloadEvent, /) -> None:
        await self.set_integration(dict(event.payload), int(event.payload["guild_id"]))

    @as_listener(guild_events.IntegrationDeleteEvent)
    async def __on_integration_delete(self, event: guild_events.IntegrationDeleteEvent, /) -> None:
        await self.delete_integration(event.id, guild_id=event.guild_id)

    async def clear_integrations(self) -> None:
        # <<Inherited docstring from sake.traits.IntegrationCache>>
        ids = await self._dump_relationship(ResourceIndex.GUILD, ResourceIndex.INTEGRATION)
        client = self.get_connection(ResourceIndex.INTEGRATION)
        await asyncio.gather(*itertools.starmap(client.delete, redis_iterators.chunk_values(ids)))

    async def clear_integrations_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        guild_id = int(guild_id)
        ids = await self._get_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.INTEGRATION, cast=bytes)
        if not ids:
            return

        await self._delete_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.INTEGRATION, *ids)
        client = self.get_connection(ResourceIndex.INTEGRATION)
        await asyncio.gather(*itertools.starmap(client.delete, redis_iterators.chunk_values(ids)))

    async def delete_integration(
        self, integration_id: snowflakes.Snowflakeish, /, *, guild_id: typing.Optional[snowflakes.Snowflakeish] = None
    ) -> None:
        # <<Inherited docstring from sake.traits.IntegrationCache>>
        integration_id = int(integration_id)
        if guild_id is None:
            try:
                # TODO: we don't have to unmarshall for this anymore
                guild_id = (await self.get_integration(integration_id)).guild_id
            except errors.EntryNotFound:
                return

        await self._delete_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.INTEGRATION, integration_id)
        client = self.get_connection(ResourceIndex.INTEGRATION)
        await client.delete(integration_id)

    async def get_integration(self, integration_id: snowflakes.Snowflakeish, /) -> guilds.Integration:
        # <<Inherited docstring from sake.traits.IntegrationCache>>
        payload = await self._get(ResourceIndex.INTEGRATION, int(integration_id))
        return self.entity_factory.deserialize_integration(payload)

    def iter_integrations(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[guilds.Integration]:
        # <<Inherited docstring from sake.traits.IntegrationCache>>
        return redis_iterators.Iterator(
            self,
            ResourceIndex.INTEGRATION,
            builder=self.entity_factory.deserialize_integration,
            window_size=window_size,
        )

    def iter_integrations_for_guild(
        self, guild_id: snowflakes.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> traits.CacheIterator[guilds.Integration]:
        key = self._generate_reference_key(ResourceIndex.GUILD, guild_id, ResourceIndex.INTEGRATION).encode()
        return redis_iterators.ReferenceIterator(
            self,
            key,
            ResourceIndex.INTEGRATION,
            builder=self.entity_factory.deserialize_integration,
            window_size=window_size,
        )

    async def set_integration(self, payload: ObjectT, /, guild_id: int) -> None:
        # <<Inherited docstring from sake.traits.IntegrationCache>>
        integration_id = int(payload["id"])
        await self._set(ResourceIndex.INTEGRATION, integration_id, payload)
        await self._add_ids(ResourceIndex.GUILD, int(guild_id), ResourceIndex.INTEGRATION, integration_id)


class InviteCache(ResourceClient, traits.InviteCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from sake.traits.Resource>>
        return (ResourceIndex.INVITE,)

    @as_raw_listener("INVITE_CREATE")
    async def __on_invite_create(self, event: shard_events.ShardPayloadEvent, /) -> None:
        await self.set_invite(dict(event.payload))

    @as_listener(channel_events.InviteDeleteEvent)
    async def __on_invite_delete(self, event: channel_events.InviteDeleteEvent, /) -> None:
        await self.delete_invite(event.code)

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
        client = self.get_connection(ResourceIndex.INVITE)
        await client.flushdb()

    async def delete_invite(self, invite_code: str, /) -> None:
        # <<Inherited docstring from sake.traits.InviteCache>>
        client = self.get_connection(ResourceIndex.INVITE)
        # Aioredis treats keys and values as type invariant so we want to ensure this is a str and not a class which
        # subclasses str.
        await client.delete(str(invite_code))

    async def get_invite(self, invite_code: str, /) -> invites_.InviteWithMetadata:
        # <<Inherited docstring from sake.traits.InviteCache>>
        # Aioredis treats keys and values as type invariant so we want to ensure this is a str and not a class which
        # subclasses str.
        payload = await self._get(ResourceIndex.INVITE, str(invite_code))
        return self.entity_factory.deserialize_invite_with_metadata(payload)

    def iter_invites(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[invites_.InviteWithMetadata]:
        # <<Inherited docstring from sake.traits.InviteCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.INVITE, self.entity_factory.deserialize_invite_with_metadata, window_size=window_size
        )

    async def set_invite(self, payload: ObjectT, /, *, expire_time: typing.Optional[ExpireT] = None) -> None:
        # <<Inherited docstring from sake.traits.InviteCache>>
        if expire_time is None and (max_age := payload.get("max_age")):
            expires_at = time.iso8601_datetime_string_to_datetime(payload["created_at"]) + max_age
            expire_time = round((expires_at - time.utc_datetime()).total_seconds() * 1000)

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
        await self._set(ResourceIndex.INVITE, str(payload["code"]), payload, pexpire=expire_time)

        if target_user := payload.get("target_user"):
            await self._try_set_user(target_user)

        if inviter := payload.get("inviter"):
            await self._try_set_user(inviter)


class _OwnIDStore:
    __slots__: typing.Sequence[str] = ("_event", "_is_waiting", "_rest", "value")

    KEY: typing.Final[typing.ClassVar[str]] = "OWN_ID"

    def __init__(self, rest: hikari_traits.RESTAware, /) -> None:
        self._event = asyncio.Event()
        self._is_waiting = False
        self._rest = rest
        self.value: typing.Optional[snowflakes.Snowflake] = None

    async def await_value(self) -> int:
        if self._is_waiting:
            await self._event.wait()
            assert self.value is not None
            return self.value

        back_off = backoff.Backoff()
        async for _ in back_off:
            if self.value is not None:
                return self.value

            try:
                user = await self._rest.rest.fetch_my_user()

            except hikari_errors.RateLimitedError as exc:  # TODO: ratelimited too long?
                back_off.set_next_backoff(exc.retry_after)

            except hikari_errors.InternalServerError:
                pass

            else:
                self.value = user.id
                break

        assert self.value is not None
        return self.value


def _get_sub_user_id(payload: ObjectT, /) -> int:
    return int(payload["user"]["id"])


# TODO: add chunking
class MemberCache(ResourceClient, traits.MemberCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from sake.traits.Resource>>
        return (ResourceIndex.MEMBER,)

    async def __bulk_set_members(self, members: typing.Iterator[ObjectT], /, guild_id: int) -> None:
        windows = redis_iterators.chunk_values((_add_guild_id(payload, guild_id) for payload in members))
        setters = (self._hmset_dict(ResourceIndex.MEMBER, guild_id, _get_sub_user_id, window) for window in windows)
        user_setter = self._try_bulk_set_users(member["user"] for member in members)
        await asyncio.gather(*setters, user_setter)

    @as_raw_listener("GUILD_CREATE")  # members aren't included in GUILD_UPDATE
    async def __on_guild_create(self, event: shard_events.ShardPayloadEvent, /) -> None:
        guild_id = int(event.payload["id"])
        await self.clear_members_for_guild(guild_id)
        await self.__bulk_set_members(event.payload["members"], guild_id)

    @as_listener(guild_events.GuildLeaveEvent)
    async def __on_guild_delete(self, event: guild_events.GuildLeaveEvent, /) -> None:
        await self.clear_members_for_guild(event.guild_id)

    @as_raw_listener("GUILD_MEMBER_ADD", "GUILD_MEMBER_UPDATE")
    async def __on_guild_member_add_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        await self.set_member(dict(event.payload), int(event.payload["guild_id"]))

    @as_listener(member_events.MemberDeleteEvent)
    async def __on_member_event(self, event: member_events.MemberDeleteEvent, /) -> None:
        try:
            own_id = self.metadata[_OwnIDStore.KEY]
            assert isinstance(own_id, _OwnIDStore)

        except KeyError:
            own_id = self.metadata[_OwnIDStore.KEY] = _OwnIDStore(self.rest)

        if (own_id_value := own_id.value) is None:
            own_id_value = await own_id.await_value()

        if event.user_id == own_id_value:
            await self.clear_members_for_guild(event.guild_id)
        else:
            await self.delete_member(event.guild_id, event.user_id)

    @as_raw_listener("GUILD_MEMBERS_CHUNK")
    async def __on_guild_members_chunk_event(self, event: shard_events.ShardPayloadEvent, /) -> None:
        await self.__bulk_set_members(event.payload["members"], int(event.payload["guild_id"]))

    async def clear_members(self) -> None:
        # <<Inherited docstring from sake.traits.MemberCache>>
        client = self.get_connection(ResourceIndex.MEMBER)
        await client.flushdb()

    async def clear_members_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.MemberCache>>
        client = self.get_connection(ResourceIndex.MEMBER)
        await client.delete(int(guild_id))

    async def delete_member(self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.MemberCache>>
        client = self.get_connection(ResourceIndex.MEMBER)
        await client.hdel(int(guild_id), int(user_id))

    async def get_member(self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /) -> guilds.Member:
        # <<Inherited docstring from sake.traits.MemberCache>>
        payload = await self._hget(ResourceIndex.MEMBER, int(guild_id), int(user_id))
        return self.entity_factory.deserialize_member(payload)

    def iter_members(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[guilds.Member]:
        # <<Inherited docstring from sake.traits.MemberCache>>
        return redis_iterators.MultiMapIterator(
            self, ResourceIndex.MEMBER, self.entity_factory.deserialize_member, window_size=window_size
        )

    def iter_members_for_guild(
        self, guild_id: snowflakes.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> traits.CacheIterator[guilds.Member]:
        # <<Inherited docstring from sake.traits.MemberCache>>
        return redis_iterators.SpecificMapIterator(
            self,
            int(guild_id),
            ResourceIndex.MEMBER,
            self.entity_factory.deserialize_member,
            window_size=window_size,
        )

    async def set_member(self, payload: ObjectT, /, guild_id: int) -> None:
        # <<Inherited docstring from sake.traits.MemberCache>>
        user_data = payload["user"]
        await self._hset(ResourceIndex.MEMBER, int(guild_id), int(user_data["id"]), payload)
        await self._try_set_user(user_data)


class MessageCache(ResourceClient, traits.MessageCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from sake.traits.Resource>>
        return (ResourceIndex.MESSAGE,)

    @as_raw_listener("MESSAGE_CREATE")
    async def on_message_create(self, event: shard_events.ShardPayloadEvent, /) -> None:
        await self.set_message(dict(event.payload))

    @as_listener(message_events.MessageDeleteEvent)  # type: ignore[misc]
    async def on_message_delete(self, event: message_events.MessageDeleteEvent, /) -> None:
        if event.is_bulk:
            client = self.get_connection(ResourceIndex.MESSAGE)
            message_ids = (int(mid) for mid in event.message_ids)
            await asyncio.gather(*itertools.starmap(client.delete, redis_iterators.chunk_values(message_ids)))

        else:
            await self.delete_message(event.message_id)

    @as_raw_listener("MESSAGE_UPDATE")
    async def on_message_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        await self.update_message(dict(event.payload))

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
        client = self.get_connection(ResourceIndex.INVITE)
        await client.flushdb()

    async def delete_message(self, message_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.MessageCache>>
        client = self.get_connection(ResourceIndex.MESSAGE)
        await client.delete(int(message_id))

    async def get_message(self, message_id: snowflakes.Snowflakeish, /) -> messages.Message:
        # <<Inherited docstring from sake.traits.MessageCache>>
        payload = await self._get(ResourceIndex.MESSAGE, int(message_id))
        return self.entity_factory.deserialize_message(payload)

    def iter_messages(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[messages.Message]:
        # <<Inherited docstring from sake.traits.MessageCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.MESSAGE, self.entity_factory.deserialize_message, window_size=window_size
        )

    async def set_message(self, payload: ObjectT, /, *, expire_time: typing.Optional[ExpireT] = None) -> None:
        # <<Inherited docstring from sake.traits.MessageCache>>
        if expire_time is None:
            expire_time = int(self.metadata.get("expire_message", DEFAULT_FAST_EXPIRE))

        else:
            expire_time = _convert_expire_time(expire_time)

        await self._set(ResourceIndex.MESSAGE, int(payload["id"]), payload, pexpire=expire_time)
        await self._try_set_user(payload["author"])

    async def update_message(
        self, payload: ObjectT, /, *, expire_time: typing.Optional[ExpireT] = None
    ) -> bool:  # noqa: C901
        # <<Inherited docstring from sake.traits.MessageCache>>
        # This is a special case method for handling the partial message updates we get
        try:
            full_message = await self._get(ResourceIndex.MESSAGE, int(payload["id"]))
        except errors.EntryNotFound:
            return False

        full_message.update(payload)
        await self.set_message(full_message, expire_time=expire_time)
        return True


class PresenceCache(ResourceClient, traits.PresenceCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        return (ResourceIndex.PRESENCE,)

    async def __bulk_add_presences(self, presences: typing.Iterator[ObjectT], /, guild_id: int) -> None:
        windows = redis_iterators.chunk_values(_add_guild_id(payload, guild_id) for payload in presences)
        setters = (self._hmset_dict(ResourceIndex.PRESENCE, guild_id, _get_sub_user_id, window) for window in windows)
        await asyncio.gather(*setters)

    @as_raw_listener("GUILD_CREATE")  # Presences is not included on GUILD_UPDATE
    async def __on_guild_create(self, event: shard_events.ShardPayloadEvent, /) -> None:
        await self.__bulk_add_presences(event.payload["presences"], int(event.payload["id"]))

    @as_listener(guild_events.GuildLeaveEvent)
    async def __on_guild_delete(self, event: guild_events.GuildLeaveEvent, /) -> None:
        await self.clear_presences_for_guild(event.guild_id)

    @as_raw_listener("GUILD_MEMBERS_CHUNK")
    async def __on_guild_members_chunk(self, event: shard_events.ShardPayloadEvent, /) -> None:
        if presences := event.payload.get("presences"):
            await self.__bulk_add_presences(presences, int(event.payload["guild_id"]))

    @as_raw_listener("PRESENCE_UPDATE")
    async def __on_presence_update_event(self, event: shard_events.ShardPayloadEvent, /) -> None:
        if event.payload["status"] == presences_.Status.OFFLINE:
            await self.delete_presence(int(event.payload["guild_id"]), int(event.payload["user"]["id"]))
            # TODO: handle presence.user?

        else:
            await self.set_presence(dict(event.payload))

    async def clear_presences(self) -> None:
        # <<Inherited docstring from sake.traits.PresenceCache>>
        client = self.get_connection(ResourceIndex.PRESENCE)
        await client.flushdb()

    async def clear_presences_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.PresenceCache>>
        client = self.get_connection(ResourceIndex.PRESENCE)
        await client.delete(int(guild_id))

    async def delete_presence(self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.PresenceCache>>
        client = self.get_connection(ResourceIndex.PRESENCE)
        await client.hdel(int(guild_id), int(user_id))

    async def get_presence(
        self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /
    ) -> presences_.MemberPresence:
        # <<Inherited docstring from sake.traits.PresenceCache>>
        payload = await self._hget(ResourceIndex.PRESENCE, int(guild_id), int(user_id))
        return self.entity_factory.deserialize_member_presence(payload)

    def iter_presences(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[presences_.MemberPresence]:
        # <<Inherited docstring from sake.traits.PresenceCache>>
        return redis_iterators.MultiMapIterator(
            self, ResourceIndex.PRESENCE, self.entity_factory.deserialize_member_presence, window_size=window_size
        )

    def iter_presences_for_guild(
        self, guild_id: snowflakes.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> traits.CacheIterator[presences_.MemberPresence]:
        # <<Inherited docstring from sake.traits.PresenceCache>>
        return redis_iterators.SpecificMapIterator(
            self,
            int(guild_id),
            ResourceIndex.PRESENCE,
            self.entity_factory.deserialize_member_presence,
            window_size=window_size,
        )

    async def set_presence(self, payload: ObjectT, /) -> None:
        # <<Inherited docstring from sake.traits.PresenceCache>>
        await self._hset(ResourceIndex.PRESENCE, int(payload["guild_id"]), int(payload["user"]["id"]), payload)


class RoleCache(_Reference, traits.RoleCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        return (ResourceIndex.ROLE,)

    @as_raw_listener("GUILD_CREATE", "GUILD_UPDATE")
    async def __on_guild_create_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        guild_id = int(event.payload["id"])
        await self.clear_roles_for_guild(guild_id)
        roles = event.payload["roles"]
        windows = redis_iterators.chunk_values(_add_guild_id(payload, guild_id) for payload in roles)
        setters = (self._mset(ResourceIndex.ROLE, _get_id, window) for window in windows)
        id_setter = self._add_ids(
            ResourceIndex.GUILD, guild_id, ResourceIndex.ROLE, *(int(payload["id"]) for payload in roles)
        )
        await asyncio.gather(*setters, id_setter)

    @as_listener(guild_events.GuildLeaveEvent)  # TODO: we're probably missing guild delete listeners in places
    async def __on_guild_delete(self, event: guild_events.GuildLeaveEvent, /) -> None:
        await self.clear_roles_for_guild(event.guild_id)

    @as_raw_listener("GUILD_ROLE_CREATE", "GUILD_ROLE_UPDATE")
    async def __on_guild_role_create_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        await self.set_role(event.payload["role"], int(event.payload["guild_id"]))

    @as_listener(role_events.RoleDeleteEvent)
    async def __on_guild_role_delete(self, event: role_events.RoleDeleteEvent, /) -> None:
        await self.delete_role(event.role_id, guild_id=event.guild_id)

    async def clear_roles(self) -> None:
        # <<Inherited docstring from sake.traits.RoleCache>>
        references = await self._dump_relationship(ResourceIndex.GUILD, ResourceIndex.ROLE)
        client = self.get_connection(ResourceIndex.ROLE)
        await asyncio.gather(
            *itertools.starmap(client.delete, redis_iterators.chunk_values(itertools.chain(references.values())))
        )

    async def clear_roles_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.RoleCache>>
        role_ids = await self._get_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.ROLE, cast=bytes)
        if not role_ids:
            return

        await self._delete_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.ROLE, *role_ids)
        client = self.get_connection(ResourceIndex.ROLE)
        # Gather is awaited here to ensure that when internally bulk setting entries after triggering a bulk deletion
        # (based on events) we don't risk deleting entries after we've re-added them.
        await asyncio.gather(*itertools.starmap(client.delete, redis_iterators.chunk_values(role_ids)))

    async def delete_role(
        self, role_id: snowflakes.Snowflakeish, /, *, guild_id: typing.Optional[snowflakes.Snowflakeish] = None
    ) -> None:
        # <<Inherited docstring from sake.traits.RoleCache>>
        role_id = int(role_id)
        if guild_id is None:
            try:  # TODO: we don't need to deserialize for this anymore
                guild_id = (await self.get_role(role_id)).guild_id
            except errors.EntryNotFound:
                return

        client = self.get_connection(ResourceIndex.ROLE)
        await self._delete_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.ROLE, role_id)
        await client.delete(role_id)

    async def get_role(self, role_id: snowflakes.Snowflakeish, /) -> guilds.Role:
        # <<Inherited docstring from sake.traits.RoleCache>>
        payload = await self._get(ResourceIndex.ROLE, int(role_id))
        return self._deserialize_role(payload)

    def _deserialize_role(self, payload: ObjectT, /) -> guilds.Role:
        guild_id = snowflakes.Snowflake(payload["guild_id"])
        return self.entity_factory.deserialize_role(payload, guild_id=guild_id)

    def iter_roles(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[guilds.Role]:
        # <<Inherited docstring from sake.traits.RoleCache>>
        return redis_iterators.Iterator(self, ResourceIndex.ROLE, self._deserialize_role, window_size=window_size)

    def iter_roles_for_guild(
        self, guild_id: snowflakes.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> traits.CacheIterator[guilds.Role]:
        # <<Inherited docstring from sake.traits.RoleCache>>
        key = self._generate_reference_key(ResourceIndex.GUILD, guild_id, ResourceIndex.ROLE)
        return redis_iterators.ReferenceIterator(
            self, key, ResourceIndex.ROLE, self._deserialize_role, window_size=window_size
        )

    async def set_role(self, payload: ObjectT, /, guild_id: int) -> None:
        # <<Inherited docstring from sake.traits.RoleCache>>
        guild_id = int(guild_id)
        role_id = int(payload["id"])
        payload["guild_id"] = guild_id
        await self._set(ResourceIndex.ROLE, role_id, payload)
        await self._add_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.ROLE, role_id)


def _add_voice_fields(payload: ObjectT, /, guild_id: int, member: ObjectT) -> ObjectT:
    payload["guild_id"] = guild_id
    payload["member"] = member
    return payload


def _get_user_id(payload: ObjectT, /) -> int:
    return int(payload["user_id"])


class VoiceStateCache(_Reference, traits.VoiceStateCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from sake.traits.Resource>>
        return (ResourceIndex.VOICE_STATE,)

    @as_listener(channel_events.ChannelDeleteEvent)  # type: ignore[misc]
    async def __on_channel_delete_event(self, event: channel_events.ChannelDeleteEvent, /) -> None:
        await self.clear_voice_states_for_channel(event.channel_id)

    @staticmethod
    def __generate_references(
        voice_states: typing.Iterable[ObjectT], /, *, guild_id: typing.Optional[int] = None
    ) -> typing.Dict[int, typing.MutableSet[str]]:
        all_references: typing.Dict[int, typing.MutableSet[str]] = {}
        for payload in voice_states:
            channel_id = int(payload["channel_id"])
            user_id = int(payload["user_id"])
            try:
                references = all_references[channel_id]

            except KeyError:
                references = all_references[channel_id] = set()

            if guild_id is not None:
                references.add(redis_iterators.HashReferenceIterator.hash_key(guild_id))

            references.add(str(user_id))

        return all_references

    @as_raw_listener("GUILD_CREATE")  # voice states aren't included in GUILD_UPDATE
    async def __on_guild_create(self, event: shard_events.ShardPayloadEvent, /) -> None:
        guild_id = int(event.payload["id"])
        await self.clear_voice_states_for_guild(guild_id)

        voice_states = event.payload["voice_states"]
        members = {int(payload["user"]["id"]): payload for payload in event.payload["members"]}

        windows = redis_iterators.chunk_values(
            _add_voice_fields(payload, guild_id, members[int(payload["user_id"])]) for payload in voice_states
        )
        setters = (self._hmset_dict(ResourceIndex.VOICE_STATE, guild_id, _get_user_id, window) for window in windows)

        references = self.__generate_references(voice_states, guild_id=guild_id)
        reference_setters = (
            self._add_ids(ResourceIndex.CHANNEL, channel_id, ResourceIndex.VOICE_STATE, *state_ids)
            for channel_id, state_ids in references.items()
            if state_ids
        )
        await asyncio.gather(*setters, *reference_setters)

    @as_listener(guild_events.GuildLeaveEvent)  # TODO: should this also clear when it goes unavailable?
    async def __on_guild_delete(self, event: guild_events.GuildLeaveEvent, /) -> None:
        await self.clear_voice_states_for_guild(event.guild_id)

    @as_raw_listener("VOICE_STATE_UPDATE")
    async def __on_voice_state_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        guild_id = int(event.payload["guild_id"])
        if event.payload.get("channel_id") is None:
            await self.delete_voice_state(guild_id, int(event.payload["user_id"]))
        else:
            await self.set_voice_state(dict(event.payload), guild_id)

    @staticmethod
    def _pop_reference(keys: typing.MutableSequence[bytes], /) -> typing.Tuple[bytes, typing.Sequence[bytes]]:
        for key in keys:
            if key.startswith(b"KEY."):
                keys.remove(key)
                return key[4:], keys

        raise ValueError("Couldn't find reference key")

    async def clear_voice_states(self) -> None:
        # <<Inherited docstring from sake.traits.VoiceStateCache>>
        references = await self._dump_relationship(ResourceIndex.CHANNEL, ResourceIndex.VOICE_STATE)
        client = self.get_connection(ResourceIndex.VOICE_STATE)
        await asyncio.gather(
            *(client.hdel(key, *values) for key, values in map(self._pop_reference, references.values()) if values)
        )

    async def clear_voice_states_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.VoiceStateCache>>
        guild_id = int(guild_id)
        client = self.get_connection(ResourceIndex.VOICE_STATE)
        states = list(
            itertools.chain.from_iterable(
                [v async for v in redis_iterators.iter_hash_values(self, ResourceIndex.VOICE_STATE, guild_id)]
            )
        )
        references = self.__generate_references(states)
        id_deleters = (
            self._delete_ids(ResourceIndex.CHANNEL, key, ResourceIndex.VOICE_STATE, *values, reference_key=True)
            for key, values in references.items()
            if values
        )
        entry_deleters = (
            client.hdel(guild_id, *values)
            for values in redis_iterators.chunk_values(int(state["user_id"]) for state in states)
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
        client = self.get_connection(ResourceIndex.VOICE_STATE)
        await asyncio.gather(
            *itertools.starmap(client.delete, redis_iterators.chunk_values(ids, window_size=window_size))
        )

    # We don't accept channel_id here to avoid the relationship lookup as channel_id isn't a static value and what we
    # want is the value stored rather than the current or "last" value.
    async def delete_voice_state(self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.VoiceStateCache>>
        guild_id = int(guild_id)
        user_id = int(user_id)
        client = self.get_connection(ResourceIndex.VOICE_STATE)

        try:
            # TODO: we don't need to deserialize here anymore
            voice_state = await self.get_voice_state(guild_id, user_id)
        except errors.EntryNotFound:
            pass
        else:
            assert voice_state.channel_id is not None, "Cached voice states should always have a bound channel"
            await client.hdel(guild_id, user_id)
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
        payload = await self._hget(ResourceIndex.VOICE_STATE, int(guild_id), int(user_id))
        return self.entity_factory.deserialize_voice_state(payload)

    def iter_voice_states(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[voices.VoiceState]:
        # <<Inherited docstring from sake.traits.VoiceStateCache>>
        return redis_iterators.MultiMapIterator(
            self, ResourceIndex.VOICE_STATE, self.entity_factory.deserialize_voice_state, window_size=window_size
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
            builder=self.entity_factory.deserialize_voice_state,
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
            self.entity_factory.deserialize_voice_state,
            window_size=window_size,
        )

    async def set_voice_state(self, payload: ObjectT, /, guild_id: int) -> None:
        # <<Inherited docstring from sake.traits.VoiceStateCache>>
        channel_id = payload.get("channel_id")
        if not channel_id:
            raise ValueError("Cannot set a voice state which isn't bound to a channel")

        channel_id = int(channel_id)
        guild_id = int(guild_id)
        payload["guild_id"] = guild_id
        user_id = int(payload["user_id"])
        # We have to ensure this is deleted first to make sure previous references are removed.
        await self.delete_voice_state(guild_id, channel_id)
        await self._hset(ResourceIndex.VOICE_STATE, guild_id, user_id, payload)
        await self._add_ids(
            ResourceIndex.CHANNEL,
            channel_id,
            ResourceIndex.VOICE_STATE,
            user_id,
            redis_iterators.HashReferenceIterator.hash_key(guild_id),
        )
        await self._try_set_user(payload["member"]["user"])


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
