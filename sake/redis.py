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
import itertools
import json
import logging
import typing
import warnings

import aioredis  # type: ignore[import]
from hikari import channels as channels_
from hikari import errors as hikari_errors
from hikari import intents as intents_
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
from sake import utility

if typing.TYPE_CHECKING:
    import ssl as ssl_
    import types

    from hikari import emojis as emojis_
    from hikari import guilds
    from hikari import invites as invites_
    from hikari import messages
    from hikari import users as users_
    from hikari import voices
    from hikari.api import entity_factory as entity_factory_
    from hikari.api import event_manager as event_manager_


_KeyT = typing.TypeVar("_KeyT")
_OtherKeyT = typing.TypeVar("_OtherKeyT")
_ValueT = typing.TypeVar("_ValueT")
_OtherValueT = typing.TypeVar("_OtherValueT")
ObjectT = typing.Dict[str, typing.Any]
_LOGGER: typing.Final[logging.Logger] = logging.getLogger("hikari.sake.redis")
"""The logger instance used by this Sake implementation."""

RedisValueT = typing.Union[int, str, bytes]
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


def _assert_optional_byte_sequence(seq: typing.Sequence[typing.Any], /) -> bool:
    for entry in seq:
        if entry is None:
            continue

        if isinstance(entry, bytes):
            break

        return False

    return True


class AioRedisFacade:
    __slots__ = ("client", "dump", "load")

    def __init__(
        self,
        *,
        client: aioredis.Redis,
        dump: typing.Callable[[ObjectT], bytes],
        load: typing.Callable[[bytes], ObjectT],
    ) -> None:
        self.client = client
        self.dump = dump
        self.load = load

    @property
    def is_closed(self) -> bool:
        state = self.client.closed
        assert isinstance(state, bool)
        return state

    async def close(self) -> None:
        self.client.close()

    async def dbsize(self) -> int:
        result = await self.client.dbsize()
        assert isinstance(result, int)
        return result

    async def delete(self, key: RedisValueT, /, *keys: RedisValueT) -> None:
        await self.client.delete(key, *keys)

    async def flushdb(self) -> None:
        await self.client.flushdb()

    async def get(self, key: RedisValueT, /, *, error: str = "Resource not found") -> ObjectT:
        data = await self.client.get(key)

        if data is None:
            raise errors.EntryNotFound(error)

        assert isinstance(data, bytes)
        return self.load(data)

    async def hdel(self, outer_key: RedisValueT, inner_key: RedisValueT, /, *inner_keys: RedisValueT) -> None:
        await self.client.hdel(outer_key, inner_key, *inner_keys)

    async def hget(
        self,
        inner_key: RedisValueT,
        outer_key: RedisValueT,
        /,
        *,
        error: str = "Resource not found",
    ) -> ObjectT:
        data = await self.client.hget(inner_key, outer_key)

        if data is None:
            raise errors.EntryNotFound(error)

        assert isinstance(data, bytes)
        return self.load(data)

    async def hlen(self, key: RedisValueT, /) -> int:
        length = await self.client.hlen(key)
        assert isinstance(length, int)
        return length

    async def hmget(
        self, outer_key: RedisValueT, inner_key: RedisValueT, /, *inner_keys: RedisValueT
    ) -> typing.Iterator[ObjectT]:
        data = await self.client.hmget(outer_key, inner_key, *inner_keys)
        assert typing.cast("typing.List[typing.Optional[bytes]]", _assert_optional_byte_sequence(data))
        return (self.load(entry) for entry in data if entry)

    async def hmset_dict(
        self,
        key: RedisValueT,
        key_getter: typing.Callable[[ObjectT], RedisValueT],
        payloads: typing.Iterable[ObjectT],
        /,
    ) -> None:
        await self.client.hmset_dict(key, {key_getter(payload): self.dump(payload) for payload in payloads})

    async def hscan(
        self,
        key: RedisValueT,
        /,
        *,
        cursor: int = 0,
        count: typing.Optional[int] = None,
        match: typing.Optional[str] = None,
    ) -> typing.Tuple[int, typing.Iterator[typing.Tuple[int, ObjectT]]]:
        cursor, window = await self.client.hscan(key, cursor=cursor, count=count, match=match)
        assert isinstance(cursor, int)
        assert not window or isinstance(window[0][1], bytes)
        return cursor, ((key, self.load(data)) for key, data in window)

    async def hset(self, outer_key: RedisValueT, inner_key: RedisValueT, payload: ObjectT, /) -> None:
        # TODO: expire?
        await self.client.hset(outer_key, inner_key, self.dump(payload))

    async def iscan(
        self, *, match: typing.Optional[str] = None, count: typing.Optional[int] = None
    ) -> typing.AsyncIterator[bytes]:
        async for key in self.client.iscan(match=match, count=count):
            assert isinstance(key, bytes)
            yield key

    async def keys(self, *, pattern: str = "*") -> typing.List[bytes]:
        result = await self.client.keys(pattern)
        assert isinstance(result, list)
        assert not result or isinstance(result[0], bytes)
        return result

    async def mget(self, key: RedisValueT, /, *keys: RedisValueT) -> typing.Iterator[ObjectT]:
        data = typing.cast("typing.List[typing.Optional[bytes]]", await self.client.mget(key, *keys))
        assert _assert_optional_byte_sequence(data)
        return (self.load(entry) for entry in data if entry)

    async def mset(
        self,
        key_getter: typing.Callable[[ObjectT], RedisValueT],
        payloads: typing.Iterable[ObjectT],
        /,
    ) -> None:
        await self.client.mset({key_getter(payload): self.dump(payload) for payload in payloads})

    async def sadd(self, key: RedisValueT, value: RedisValueT, /, *values: RedisValueT) -> None:
        await self.client.sadd(key, value, *values)

    async def scan(
        self, *, cursor: int = 0, count: typing.Optional[int] = None, match: typing.Optional[str] = None
    ) -> typing.Tuple[int, typing.List[bytes]]:
        cursor, keys = await self.client.scan(cursor=cursor, count=count, match=match)
        assert isinstance(cursor, int)
        assert isinstance(keys, list)
        assert not keys or isinstance(keys[0], bytes)
        return cursor, keys

    async def scard(self, key: RedisValueT, /) -> int:
        result = await self.client.scard(key)
        assert isinstance(result, int)
        return result

    async def set(
        self,
        key: RedisValueT,
        payload: ObjectT,
        /,
        *,
        pexpire: typing.Optional[int] = None,
    ) -> None:
        await self.client.set(key, self.dump(payload), pexpire=pexpire)

    async def smembers(self, key: RedisValueT, /) -> typing.List[RedisValueT]:
        result = await self.client.smembers(key)
        assert isinstance(result, list)
        assert not result or isinstance(result[0], (int, str, bytes))
        return result

    async def srem(self, key: RedisValueT, value: RedisValueT, /, *values: RedisValueT) -> None:
        await self.client.srem(key, value, *values)

    async def sscan(
        self,
        key: RedisValueT,
        /,
        *,
        cursor: int = 0,
        match: typing.Optional[str] = None,
        count: typing.Optional[int] = None,
    ) -> typing.Tuple[int, typing.List[RedisValueT]]:
        cursor, result = await self.client.sscan(key, cursor=cursor, match=match, count=count)
        assert isinstance(cursor, int)
        assert isinstance(result, list)
        assert not result or isinstance(result[0], (bytes, int, str))
        return cursor, result

    async def pexpire(self, key: RedisValueT, expire: int, /) -> None:
        await self.client.pexpire(key, expire)


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
        "__dump",
        "__entity_factory",
        "__event_managed",
        "__event_manager",
        "__listeners",
        "__load",
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
        default_expire: utility.ExpireT = DEFAULT_SLOW_EXPIRE,
        event_managed: bool = True,
        address: typing.Union[str, typing.Tuple[str, typing.Union[str, int]]],
        password: typing.Optional[str] = None,
        ssl: typing.Union[ssl_.SSLContext, bool, None] = None,
        metadata: typing.Optional[typing.MutableMapping[str, typing.Any]] = None,
        dumps: typing.Callable[[ObjectT], bytes] = lambda obj: json.dumps(obj).encode(),
        loads: typing.Callable[[bytes], ObjectT] = json.loads,
    ) -> None:
        entity_factory = utility.try_find_type(
            hikari_traits.EntityFactoryAware, entity_factory, rest, event_manager  # type: ignore[misc]
        )

        if entity_factory is undefined.UNDEFINED:
            raise ValueError("An entity factory implementation must be provided")

        if event_manager is undefined.UNDEFINED:
            event_manager = utility.try_find_type(hikari_traits.EventManagerAware, rest, entity_factory)

            if event_manager is undefined.UNDEFINED:
                raise ValueError("An event manager implementation must be provided or explicitly passed as None")

        elif event_manager is None:
            ...  # TODO: add warning about running statelessly?

        self.__address = address
        self.__clients: typing.Dict[int, AioRedisFacade] = {}
        self.__default_expire = utility.convert_expire_time(default_expire)
        self.__dump = dumps
        self.__entity_factory = entity_factory
        self.__event_managed = event_managed
        self.__event_manager = event_manager
        self.__listeners = utility.find_listeners(self)
        self.__index_overrides: typing.Dict[ResourceIndex, int] = {}
        self.__load = loads
        self.__metadata = metadata or {}
        self.__password = password
        self.__raw_listeners = utility.find_raw_listeners(self)
        self.__rest = rest
        self.__ssl = ssl
        self.__started = False

        if event_managed and event_manager:
            event_manager.event_manager.subscribe(lifetime_events.StartingEvent, self.__on_starting_event)
            event_manager.event_manager.subscribe(lifetime_events.StoppingEvent, self.__on_stopping_event)
            if isinstance(event_manager, hikari_traits.ShardAware):
                self.__check_intents(event_manager.intents)

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

    if not typing.TYPE_CHECKING:

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

    @property
    def entity_factory(self) -> entity_factory_.EntityFactory:
        return self.__entity_factory.entity_factory

    @property
    def event_manager(self) -> typing.Optional[event_manager_.EventManager]:
        return self.__event_manager.event_manager if self.__event_manager else None

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

    @classmethod
    @abc.abstractmethod
    def intents(cls) -> intents_.Intents:
        """The intents the resource requires to function properly.

        !!! note
            This should be called on specific base classes and will not be
            accurate after inheritance.

        Returns
        -------
        hikari.intents.Intents
            The intents the resource requires to function properly.
        """
        return intents_.Intents.NONE

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

    def all_indexes(self) -> typing.MutableSet[int]:
        """Get a set of all the Redis client indexes this is using.

        !!! note
            This accounts for index overrides.

        Returns
        -------
        typing.MutableSet[int]
            A set of all the Redis client indexes this is using.
        """
        results: typing.Set[int] = set()
        for sub_class in type(self).mro():
            if issubclass(sub_class, ResourceClient):
                results.update((self.__index_overrides.get(index, index) for index in sub_class.index()))

        return results

    @classmethod
    def all_intents(cls) -> intents_.Intents:
        result = intents_.Intents.NONE
        for sub_class in cls.mro():
            if issubclass(sub_class, ResourceClient):
                result |= sub_class.intents()

        return result

    def __check_intents(self, intents: intents_.Intents, /) -> None:
        for cls in type(self).mro():
            if issubclass(cls, ResourceClient):
                required_intents = cls.intents()
                if (required_intents & intents) != required_intents:
                    warnings.warn(
                        f"{cls.__name__} resource will not function properly without the"
                        f" following intents: {required_intents}",
                        stacklevel=3,
                    )

    def dump(self, data: ObjectT, /) -> bytes:
        return self.__dump(data)

    def load(self, data: bytes, /) -> ObjectT:
        return self.__load(data)

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

    def get_connection(self, resource: ResourceIndex, /) -> AioRedisFacade:
        """Get the connection for a specific resource.

        Parameters
        ----------
        resource : ResourceIndex
            The index of the resource to get a connection for.

        Returns
        -------
        AioRedisFacade
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
        return resource in self.__clients and not self.__clients[resource].is_closed

    async def _try_bulk_set_users(self, users: typing.Iterator[ObjectT], /) -> None:
        if not (client := self.__clients.get(ResourceIndex.USER)):
            return

        expire_time = int(self.metadata.get("expire_user", DEFAULT_EXPIRE))
        # user_setters = []
        # expire_setters: typing.MutableSequence[typing.Coroutine[typing.Any, typing.Any, None]] = []

        for window in redis_iterators.chunk_values(users):
            # transaction = client.multi_exec()
            #
            # for user_id in processed_window.keys():
            #     transaction.pexpire(user_id, expire_time)
            #
            # expire_setters.append(transaction.execute())
            await client.mset(_get_id, window)
            await asyncio.gather(*(client.pexpire(_get_id(payload), expire_time) for payload in window))
            #  TODO: benchmark bulk setting expire with transaction vs this
            # user_setters.append(client.mset(processed_window))
            # expire_setters.extend((client.pexpire(user_id, expire_time) for user_id in processed_window.keys()))

        # asyncio.gather(*user_setters)
        # asyncio.gather(*expire_setters)

    async def _try_set_user(self, payload: ObjectT, /) -> None:
        if not (client := self.__clients.get(ResourceIndex.USER)):
            return

        expire_time = int(self.metadata.get("expire_user", DEFAULT_EXPIRE))
        await client.set(int(payload["id"]), payload, pexpire=expire_time)

    async def __on_shard_payload_event(self, event: shard_events.ShardPayloadEvent, /) -> None:
        if listeners := self.__raw_listeners.get(event.name.upper()):
            await asyncio.gather(*(listener(event) for listener in listeners))

    async def __spawn_connection(self, resource: int, /) -> None:
        client = await aioredis.create_redis_pool(
            address=self.__address,
            db=int(resource),
            password=self.__password,
            ssl=self.__ssl,
            # encoding="utf-8",
        )
        self.__clients[resource] = AioRedisFacade(client=client, dump=self.__dump, load=self.__load)

    async def open(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        if self.__started:
            return

        try:
            # Gather is awaited here so we can assure all clients are started before this returns.
            await asyncio.gather(*map(self.__spawn_connection, self.all_indexes()))
        except aioredis.RedisError:
            # Ensure no dangling clients are left if this fails to start.
            clients = self.__clients
            self.__clients = {}
            await asyncio.gather(*(client.close() for client in clients.values()))
            raise

        if self.__event_manager:
            self.__event_manager.event_manager.subscribe(shard_events.ShardPayloadEvent, self.__on_shard_payload_event)

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=hikari_errors.MissingIntentWarning)
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
                    shard_events.ShardPayloadEvent, self.__on_shard_payload_event
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
        await asyncio.gather(*(client.close() for client in clients.values()))


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
        /,
        *identifiers: RedisValueT,
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
        identifier: RedisValueT,
        /,
        *identifiers: RedisValueT,
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
        members = typing.cast("typing.List[bytes]", await client.smembers(key))
        return [*map(cast, members)]


# To avoid breaking Mro conflicts this is kept as a separate class despite only being exposed through the UserCache.
class _MeCache(ResourceClient, traits.MeCache):
    __slots__: typing.Sequence[str] = ()

    __ME_KEY: typing.Final[str] = "ME"

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        # This isn't a full MeCache implementation in itself as it's reliant on the UserCache implementation.
        return ()

    @classmethod
    def intents(cls) -> intents_.Intents:
        # <<Inherited docstring from ResourceClient>>
        return intents_.Intents.NONE

    @utility.as_raw_listener("USER_UPDATE")
    async def __on_own_user_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        await self.set_me(dict(event.payload))

    @utility.as_raw_listener("READY")
    async def __on_shard_ready(self, event: shard_events.ShardPayloadEvent, /) -> None:
        await self.set_me(event.payload["user"])

    async def delete_me(self) -> None:
        # <<Inherited docstring from sake.traits.MeCache>>
        client = self.get_connection(ResourceIndex.USER)
        await client.delete(self.__ME_KEY)

    async def get_me(self) -> users_.OwnUser:
        # <<Inherited docstring from sake.traits.MeCache>>
        payload = await self.get_connection(ResourceIndex.USER).get(self.__ME_KEY)
        return self.entity_factory.deserialize_my_user(payload)

    async def set_me(self, payload: ObjectT, /) -> None:
        # <<Inherited docstring from sake.traits.MeCache>>
        await self.get_connection(ResourceIndex.USER).set(self.__ME_KEY, payload)
        await self._try_set_user(payload)


class UserCache(_MeCache, traits.UserCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        return (ResourceIndex.USER,)

    @classmethod
    def intents(cls) -> intents_.Intents:
        # <<Inherited docstring from ResourceClient>>
        return intents_.Intents.NONE

    def with_user_expire(self: ResourceT, expire: typing.Optional[utility.ExpireT], /) -> ResourceT:
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
            self.metadata["expire_user"] = utility.convert_expire_time(expire)

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

    async def get_user(self, user_id: snowflakes.Snowflakeish, /) -> users_.User:
        # <<Inherited docstring from sake.traits.UserCache>>
        payload = await self.get_connection(ResourceIndex.USER).get(int(user_id))
        return self.entity_factory.deserialize_user(payload)

    def iter_users(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[users_.User]:
        # <<Inherited docstring from sake.traits.UserCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.USER, self.entity_factory.deserialize_user, window_size=window_size
        )

    async def set_user(self, payload: ObjectT, /, *, expire_time: typing.Optional[utility.ExpireT] = None) -> None:
        # <<Inherited docstring from sake.traits.UserCache>>
        if expire_time is None:
            expire_time = int(self.metadata.get("expire_user", DEFAULT_EXPIRE))

        else:
            expire_time = utility.convert_expire_time(expire_time)

        await self.get_connection(ResourceIndex.USER).set(int(payload["id"]), payload, pexpire=expire_time)


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

    @classmethod
    def intents(cls) -> intents_.Intents:
        # <<Inherited docstring from ResourceClient>>
        return intents_.Intents.GUILD_EMOJIS | intents_.Intents.GUILDS

    async def __bulk_add_emojis(self, emojis: typing.Iterable[ObjectT], /, guild_id: int) -> None:
        client = self.get_connection(ResourceIndex.EMOJI)
        windows = redis_iterators.chunk_values(_add_guild_id(emoji, guild_id) for emoji in emojis)
        setters = (client.mset(_get_id, window) for window in windows)
        user_setter = self._try_bulk_set_users(user for payload in emojis if (user := payload.get("user")))
        reference_setter = self._add_ids(
            ResourceIndex.GUILD, guild_id, ResourceIndex.EMOJI, *(int(payload["id"]) for payload in emojis)
        )
        await asyncio.gather(*setters, user_setter, reference_setter)

    @utility.as_raw_listener("GUILD_EMOJIS_UPDATE")
    async def __on_emojis_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        guild_id = int(event.payload["guild_id"])
        await self.clear_emojis_for_guild(guild_id)

        if emojis := event.payload.get("emojis"):
            await self.__bulk_add_emojis(emojis, guild_id)

    #  TODO: can we also listen for member delete to manage this?
    @utility.as_raw_listener("GUILD_CREATE", "GUILD_UPDATE")
    async def __on_guild_create_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        guild_id = int(event.payload["id"])
        await self.clear_emojis_for_guild(guild_id)

        if emojis := event.payload["emojis"]:
            await self.__bulk_add_emojis(emojis, guild_id)

    @utility.as_listener(guild_events.GuildLeaveEvent)
    async def __on_guild_leave_event(self, event: guild_events.GuildLeaveEvent, /) -> None:
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
                payload = await self.get_connection(ResourceIndex.EMOJI).get(emoji_id)
                guild_id = int(payload["guild_id"])
            except errors.EntryNotFound:
                return

        client = self.get_connection(ResourceIndex.EMOJI)
        await self._delete_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.EMOJI, emoji_id)
        await client.delete(emoji_id)

    async def get_emoji(self, emoji_id: snowflakes.Snowflakeish, /) -> emojis_.KnownCustomEmoji:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        payload = await self.get_connection(ResourceIndex.EMOJI).get(int(emoji_id))
        return self.__deserialize_known_custom_emoji(payload)

    def __deserialize_known_custom_emoji(self, payload: ObjectT, /) -> emojis_.KnownCustomEmoji:
        guild_id = snowflakes.Snowflake(payload["guild_id"])
        return self.entity_factory.deserialize_known_custom_emoji(payload, guild_id=guild_id)

    def iter_emojis(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[emojis_.KnownCustomEmoji]:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.EMOJI, self.__deserialize_known_custom_emoji, window_size=window_size
        )

    def iter_emojis_for_guild(
        self, guild_id: snowflakes.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> traits.CacheIterator[emojis_.KnownCustomEmoji]:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        key = self._generate_reference_key(ResourceIndex.GUILD, guild_id, ResourceIndex.EMOJI)
        return redis_iterators.ReferenceIterator(
            self, key, ResourceIndex.EMOJI, self.__deserialize_known_custom_emoji, window_size=window_size
        )

    async def set_emoji(self, payload: ObjectT, /, guild_id: int) -> None:
        # <<Inherited docstring from sake.traits.EmojiCache>>
        guild_id = int(guild_id)
        emoji_id = int(payload["id"])
        payload["guild_id"] = guild_id  # TODO: is this necessary
        await self.get_connection(ResourceIndex.EMOJI).set(emoji_id, payload)
        await self._add_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.EMOJI, emoji_id)

        if (user := payload.get("user")) is not None:
            await self._try_set_user(user)


class GuildCache(ResourceClient, traits.GuildCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        return (ResourceIndex.GUILD,)

    @classmethod
    def intents(cls) -> intents_.Intents:
        # <<Inherited docstring from ResourceClient>>
        return intents_.Intents.GUILDS

    @utility.as_raw_listener("GUILD_CREATE", "GUILD_UPDATE")
    async def __on_guild_create_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        await self.set_guild(dict(event.payload))

    @utility.as_listener(guild_events.GuildLeaveEvent)
    async def __on_guild_leave_event(self, event: guild_events.GuildLeaveEvent, /) -> None:
        await self.delete_guild(event.guild_id)

    async def clear_guilds(self) -> None:
        # <<Inherited docstring from sake.traits.GuildCache>>
        client = self.get_connection(ResourceIndex.GUILD)
        await client.flushdb()

    async def delete_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.GuildCache>>
        client = self.get_connection(ResourceIndex.GUILD)
        await client.delete(int(guild_id))

    def __deserialize_guild(self, payload: ObjectT, /) -> guilds.GatewayGuild:
        return self.entity_factory.deserialize_gateway_guild(payload).guild

    async def get_guild(self, guild_id: snowflakes.Snowflakeish, /) -> guilds.GatewayGuild:
        # <<Inherited docstring from sake.traits.GuildCache>>
        payload = await self.get_connection(ResourceIndex.GUILD).get(int(guild_id))
        return self.__deserialize_guild(payload)

    def iter_guilds(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[guilds.GatewayGuild]:
        # <<Inherited docstring from sake.traits.GuildCache>>
        return redis_iterators.Iterator(self, ResourceIndex.GUILD, self.__deserialize_guild, window_size=window_size)

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
        await self.get_connection(ResourceIndex.GUILD).set(int(payload["id"]), payload)


# TODO: guild_id isn't always included in the payload?
class GuildChannelCache(_Reference, traits.RefGuildChannelCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from sake.traits.Resource>>
        return (ResourceIndex.CHANNEL,)

    @classmethod
    def intents(cls) -> intents_.Intents:
        # <<Inherited docstring from ResourceClient>>
        return intents_.Intents.GUILDS

    @utility.as_raw_listener("CHANNEL_CREATE", "CHANNEL_UPDATE")
    async def __on_channel_create_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        await self.set_guild_channel(dict(event.payload))

    @utility.as_listener(channel_events.GuildChannelDeleteEvent)  # we don't actually get events for DM channels
    async def __on_channel_delete_event(self, event: channel_events.GuildChannelDeleteEvent, /) -> None:
        await self.delete_guild_channel(event.channel_id, guild_id=event.guild_id)

    @utility.as_raw_listener("CHANNEL_PINS_UPDATE")
    async def __on_channel_pins_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        try:
            payload = await self.get_connection(ResourceIndex.CHANNEL).get(int(event.payload["channel_id"]))
        except errors.EntryNotFound:
            pass
        else:
            payload["last_pin_timestamp"] = event.payload.get("last_pin_timestamp")
            await self.set_guild_channel(payload)

    @utility.as_raw_listener("GUILD_CREATE")  # GUILD_UPDATE doesn't include channels
    async def __on_guild_create_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        guild_id = int(event.payload["id"])
        await self.clear_guild_channels_for_guild(guild_id)
        client = self.get_connection(ResourceIndex.CHANNEL)

        channel_ids: typing.List[int] = []
        setters: typing.List[typing.Awaitable[None]] = []
        raw_channels = event.payload["channels"]
        for window in redis_iterators.chunk_values(_add_guild_id(payload, guild_id) for payload in raw_channels):
            setters.append(client.mset(_get_id, window))
            channel_ids.extend(int(payload["id"]) for payload in window)

        id_setter = self._add_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.CHANNEL, *channel_ids)
        await asyncio.gather(*setters, id_setter)

    @utility.as_listener(guild_events.GuildLeaveEvent)  # TODO: can we also use member remove events here?
    async def __on_guild_leave_event(self, event: guild_events.GuildLeaveEvent, /) -> None:
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
                payload = await self.get_connection(ResourceIndex.CHANNEL).get(channel_id)
                guild_id = int(payload["guild_id"])
            except errors.EntryNotFound:
                return

        client = self.get_connection(ResourceIndex.CHANNEL)
        await self._delete_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.CHANNEL, channel_id)
        await client.delete(channel_id)

    async def get_guild_channel(self, channel_id: snowflakes.Snowflakeish, /) -> channels_.GuildChannel:
        # <<Inherited docstring from sake.traits.GuildChannelCache>>
        payload = await self.get_connection(ResourceIndex.CHANNEL).get(int(channel_id))
        return self.__deserialize_guild_channel(payload)

    def __deserialize_guild_channel(self, payload: ObjectT, /) -> channels_.GuildChannel:
        channel = self.entity_factory.deserialize_channel(payload)
        assert isinstance(channel, channels_.GuildChannel)
        return channel

    def iter_guild_channels(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[channels_.GuildChannel]:
        # <<Inherited docstring from sake.traits.GuildChannelCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.CHANNEL, self.__deserialize_guild_channel, window_size=window_size
        )

    def iter_guild_channels_for_guild(
        self, guild_id: snowflakes.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> traits.CacheIterator[channels_.GuildChannel]:
        # <<Inherited docstring from sake.traits.GuildChannelCache>>
        key = self._generate_reference_key(ResourceIndex.GUILD, guild_id, ResourceIndex.CHANNEL)
        return redis_iterators.ReferenceIterator(
            self, key, ResourceIndex.CHANNEL, self.__deserialize_guild_channel, window_size=window_size
        )

    async def set_guild_channel(self, payload: ObjectT, /) -> None:
        # <<Inherited docstring from sake.traits.GuildChannelCache>>
        channel_id = int(payload["id"])
        guild_id = int(payload["guild_id"])
        await self.get_connection(ResourceIndex.CHANNEL).set(channel_id, payload)
        await self._add_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.CHANNEL, channel_id)


class IntegrationCache(_Reference, traits.IntegrationCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from sake.traits.Resource>>
        return (ResourceIndex.INTEGRATION,)

    @classmethod
    def intents(cls) -> intents_.Intents:
        # <<Inherited docstring from ResourceClient>>
        return intents_.Intents.GUILDS | intents_.Intents.GUILD_INTEGRATIONS

    @utility.as_listener(guild_events.GuildLeaveEvent)
    async def __on_guild_leave_event(self, event: guild_events.GuildLeaveEvent, /) -> None:
        await self.clear_integrations_for_guild(event.guild_id)

    @utility.as_raw_listener("INTEGRATION_CREATE", "INTEGRATION_UPDATE")
    async def __on_integration_event(self, event: shard_events.ShardPayloadEvent, /) -> None:
        await self.set_integration(dict(event.payload), int(event.payload["guild_id"]))

    @utility.as_listener(guild_events.IntegrationDeleteEvent)
    async def __on_integration_delete_event(self, event: guild_events.IntegrationDeleteEvent, /) -> None:
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
                payload = await self.get_connection(ResourceIndex.INTEGRATION).get(integration_id)
                guild_id = int(payload["guild_id"])
            except errors.EntryNotFound:
                return

        await self._delete_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.INTEGRATION, integration_id)
        client = self.get_connection(ResourceIndex.INTEGRATION)
        await client.delete(integration_id)

    async def get_integration(self, integration_id: snowflakes.Snowflakeish, /) -> guilds.Integration:
        # <<Inherited docstring from sake.traits.IntegrationCache>>
        payload = await self.get_connection(ResourceIndex.INTEGRATION).get(int(integration_id))
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
        guild_id = int(guild_id)
        payload["guild_id"] = guild_id  # TODO: is this necessary?
        await self.get_connection(ResourceIndex.INTEGRATION).set(integration_id, payload)
        await self._add_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.INTEGRATION, integration_id)


class InviteCache(ResourceClient, traits.InviteCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from sake.traits.Resource>>
        return (ResourceIndex.INVITE,)

    @classmethod
    def intents(cls) -> intents_.Intents:
        # <<Inherited docstring from ResourceClient>>
        return intents_.Intents.GUILD_INVITES

    @utility.as_raw_listener("INVITE_CREATE")
    async def __on_invite_create(self, event: shard_events.ShardPayloadEvent, /) -> None:
        await self.set_invite(dict(event.payload))

    @utility.as_listener(channel_events.InviteDeleteEvent)
    async def __on_invite_delete_event(self, event: channel_events.InviteDeleteEvent, /) -> None:
        await self.delete_invite(event.code)  # TODO: on guild leave?

    def with_invite_expire(self: ResourceT, expire: typing.Optional[utility.ExpireT], /) -> ResourceT:
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
            self.metadata["expire_invite"] = utility.convert_expire_time(expire)

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
        payload = await self.get_connection(ResourceIndex.INVITE).get(str(invite_code))
        return self.entity_factory.deserialize_invite_with_metadata(payload)

    def iter_invites(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[invites_.InviteWithMetadata]:
        # <<Inherited docstring from sake.traits.InviteCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.INVITE, self.entity_factory.deserialize_invite_with_metadata, window_size=window_size
        )

    async def set_invite(self, payload: ObjectT, /, *, expire_time: typing.Optional[utility.ExpireT] = None) -> None:
        # <<Inherited docstring from sake.traits.InviteCache>>
        if expire_time is None and (raw_max_age := payload.get("max_age")):
            max_age = datetime.timedelta(seconds=int(raw_max_age))
            expires_at = time.iso8601_datetime_string_to_datetime(payload["created_at"]) + max_age
            expire_time = round((expires_at - time.utc_datetime()).total_seconds() * 1000)

            # If this invite has already expired or the system clock is direly out of sync then we cannot
            # use the expire time we just calculated.
            if expire_time <= 0:
                expire_time = None

        if expire_time is None:
            expire_time = int(self.metadata.get("expire_invite", DEFAULT_INVITE_EXPIRE))

        else:
            expire_time = utility.convert_expire_time(expire_time)

        # Aioredis treats keys and values as type invariant so we want to ensure this is a str and not a class which
        # subclasses str.
        await self.get_connection(ResourceIndex.INVITE).set(str(payload["code"]), payload, pexpire=expire_time)

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


class MemberCache(ResourceClient, traits.MemberCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from sake.traits.Resource>>
        return (ResourceIndex.MEMBER,)

    @classmethod
    def intents(cls) -> intents_.Intents:
        # <<Inherited docstring from ResourceClient>>
        return intents_.Intents.GUILD_MEMBERS | intents_.Intents.GUILDS

    def chunk_on_guild_create(self: ResourceT, shard_aware: typing.Optional[hikari_traits.ShardAware], /) -> ResourceT:
        if shard_aware:
            if not self.event_manager:
                raise ValueError("An event manager-less cache instance cannot request member chunk on guild crate")

            if not (shard_aware.intents & intents_.Intents.GUILD_MEMBERS):
                raise ValueError("Cannot request guild member chunks without the GUILD_MEMBERS intents declared")

            presences = (shard_aware.intents & intents_.Intents.GUILD_PRESENCES) == intents_.Intents.GUILD_PRESENCES
            self.metadata["chunk_on_create"] = shard_aware
            self.metadata["chunk_presences"] = presences and isinstance(self, traits.PresenceCache)

        else:
            self.metadata.pop("chunk_on_create", None)

        return self

    async def __bulk_set_members(self, members: typing.Iterator[ObjectT], /, guild_id: int) -> None:
        client = self.get_connection(ResourceIndex.MEMBER)
        windows = redis_iterators.chunk_values((_add_guild_id(payload, guild_id) for payload in members))
        setters = (client.hmset_dict(guild_id, _get_sub_user_id, window) for window in windows)
        user_setter = self._try_bulk_set_users(member["user"] for member in members)
        await asyncio.gather(*setters, user_setter)

    @utility.as_raw_listener("GUILD_CREATE")  # members aren't included in GUILD_UPDATE
    async def __on_guild_create(self, event: shard_events.ShardPayloadEvent, /) -> None:
        guild_id = int(event.payload["id"])
        await self.clear_members_for_guild(guild_id)
        await self.__bulk_set_members(event.payload["members"], guild_id)

        if shard_aware := self.metadata.get("chunk_on_create"):
            assert isinstance(shard_aware, hikari_traits.ShardAware)
            include_presences = bool(self.metadata.get("chunk_presences", False))
            await shard_aware.request_guild_members(guild_id, include_presences=include_presences)

    @utility.as_listener(guild_events.GuildLeaveEvent)
    async def __on_guild_leave_event(self, event: guild_events.GuildLeaveEvent, /) -> None:
        await self.clear_members_for_guild(event.guild_id)

    @utility.as_raw_listener("GUILD_MEMBER_ADD", "GUILD_MEMBER_UPDATE")
    async def __on_guild_member_add_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        await self.set_member(dict(event.payload), int(event.payload["guild_id"]))

    @utility.as_listener(member_events.MemberDeleteEvent)
    async def __on_member_delete_event(self, event: member_events.MemberDeleteEvent, /) -> None:
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

    @utility.as_raw_listener("GUILD_MEMBERS_CHUNK")
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
        payload = await self.get_connection(ResourceIndex.MEMBER).hget(int(guild_id), int(user_id))
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
        await self.get_connection(ResourceIndex.MEMBER).hset(int(guild_id), int(user_data["id"]), payload)
        await self._try_set_user(user_data)


class MessageCache(ResourceClient, traits.MessageCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from sake.traits.Resource>>
        return (ResourceIndex.MESSAGE,)

    @classmethod
    def intents(cls) -> intents_.Intents:
        # <<Inherited docstring from ResourceClient>>
        return intents_.Intents.ALL_MESSAGES

    @utility.as_raw_listener("MESSAGE_CREATE")
    async def __on_message_create(self, event: shard_events.ShardPayloadEvent, /) -> None:
        await self.set_message(dict(event.payload))

    @utility.as_listener(message_events.MessageDeleteEvent)  # type: ignore[misc]
    async def __on_message_delete_event(self, event: message_events.MessageDeleteEvent, /) -> None:
        if event.is_bulk:
            client = self.get_connection(ResourceIndex.MESSAGE)
            message_ids = (int(mid) for mid in event.message_ids)
            await asyncio.gather(*itertools.starmap(client.delete, redis_iterators.chunk_values(message_ids)))

        else:
            await self.delete_message(event.message_id)

    @utility.as_raw_listener("MESSAGE_UPDATE")
    async def __on_message_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        await self.update_message(dict(event.payload))

    def with_message_expire(self: ResourceT, expire: typing.Optional[utility.ExpireT], /) -> ResourceT:
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
            self.metadata["expire_message"] = utility.convert_expire_time(expire)

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
        payload = await self.get_connection(ResourceIndex.MESSAGE).get(int(message_id))
        return self.entity_factory.deserialize_message(payload)

    def iter_messages(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[messages.Message]:
        # <<Inherited docstring from sake.traits.MessageCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.MESSAGE, self.entity_factory.deserialize_message, window_size=window_size
        )

    async def set_message(self, payload: ObjectT, /, *, expire_time: typing.Optional[utility.ExpireT] = None) -> None:
        # <<Inherited docstring from sake.traits.MessageCache>>
        if expire_time is None:
            expire_time = int(self.metadata.get("expire_message", DEFAULT_FAST_EXPIRE))

        else:
            expire_time = utility.convert_expire_time(expire_time)

        await self.get_connection(ResourceIndex.MESSAGE).set(int(payload["id"]), payload, pexpire=expire_time)
        await self._try_set_user(payload["author"])

    async def update_message(
        self, payload: ObjectT, /, *, expire_time: typing.Optional[utility.ExpireT] = None
    ) -> bool:
        # <<Inherited docstring from sake.traits.MessageCache>>
        # This is a special case method for handling the partial message updates we get
        try:
            full_message = await self.get_connection(ResourceIndex.MESSAGE).get(int(payload["id"]))
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

    @classmethod
    def intents(cls) -> intents_.Intents:
        # <<Inherited docstring from ResourceClient>>
        return intents_.Intents.GUILDS | intents_.Intents.GUILD_PRESENCES

    async def __bulk_add_presences(self, presences: typing.Iterator[ObjectT], /, guild_id: int) -> None:
        client = self.get_connection(ResourceIndex.PRESENCE)
        windows = redis_iterators.chunk_values(_add_guild_id(payload, guild_id) for payload in presences)
        setters = (client.hmset_dict(guild_id, _get_sub_user_id, window) for window in windows)
        await asyncio.gather(*setters)

    @utility.as_raw_listener("GUILD_CREATE")  # Presences is not included on GUILD_UPDATE
    async def __on_guild_create(self, event: shard_events.ShardPayloadEvent, /) -> None:
        await self.__bulk_add_presences(event.payload["presences"], int(event.payload["id"]))

    @utility.as_listener(guild_events.GuildLeaveEvent)
    async def __on_guild_leave_event(self, event: guild_events.GuildLeaveEvent, /) -> None:
        await self.clear_presences_for_guild(event.guild_id)

    @utility.as_raw_listener("GUILD_MEMBERS_CHUNK")
    async def __on_guild_members_chunk(self, event: shard_events.ShardPayloadEvent, /) -> None:
        if presences := event.payload.get("presences"):
            await self.__bulk_add_presences(presences, int(event.payload["guild_id"]))

    @utility.as_raw_listener("PRESENCE_UPDATE")
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
        payload = await self.get_connection(ResourceIndex.PRESENCE).hget(int(guild_id), int(user_id))
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
        client = self.get_connection(ResourceIndex.PRESENCE)
        await client.hset(int(payload["guild_id"]), int(payload["user"]["id"]), payload)


class RoleCache(_Reference, traits.RoleCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        return (ResourceIndex.ROLE,)

    @classmethod
    def intents(cls) -> intents_.Intents:
        # <<Inherited docstring from ResourceClient>>
        return intents_.Intents.GUILDS

    @utility.as_raw_listener("GUILD_CREATE", "GUILD_UPDATE")
    async def __on_guild_create_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        guild_id = int(event.payload["id"])
        await self.clear_roles_for_guild(guild_id)
        roles = event.payload["roles"]
        windows = redis_iterators.chunk_values(_add_guild_id(payload, guild_id) for payload in roles)
        client = self.get_connection(ResourceIndex.ROLE)
        setters = (client.mset(_get_id, window) for window in windows)
        id_setter = self._add_ids(
            ResourceIndex.GUILD, guild_id, ResourceIndex.ROLE, *(int(payload["id"]) for payload in roles)
        )
        await asyncio.gather(*setters, id_setter)

    @utility.as_listener(guild_events.GuildLeaveEvent)
    async def __on_guild_leave_event(self, event: guild_events.GuildLeaveEvent, /) -> None:
        await self.clear_roles_for_guild(event.guild_id)

    @utility.as_raw_listener("GUILD_ROLE_CREATE", "GUILD_ROLE_UPDATE")
    async def __on_guild_role_create_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        await self.set_role(event.payload["role"], int(event.payload["guild_id"]))

    @utility.as_listener(role_events.RoleDeleteEvent)
    async def __on_guild_role_delete_event(self, event: role_events.RoleDeleteEvent, /) -> None:
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
            try:
                payload = await self.get_connection(ResourceIndex.ROLE).get(int(role_id))
                guild_id = int(payload["guild_id"])
            except errors.EntryNotFound:
                return

        client = self.get_connection(ResourceIndex.ROLE)
        await self._delete_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.ROLE, role_id)
        await client.delete(role_id)

    async def get_role(self, role_id: snowflakes.Snowflakeish, /) -> guilds.Role:
        # <<Inherited docstring from sake.traits.RoleCache>>
        payload = await self.get_connection(ResourceIndex.ROLE).get(int(role_id))
        return self.__deserialize_role(payload)

    def __deserialize_role(self, payload: ObjectT, /) -> guilds.Role:
        guild_id = snowflakes.Snowflake(payload["guild_id"])
        return self.entity_factory.deserialize_role(payload, guild_id=guild_id)

    def iter_roles(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[guilds.Role]:
        # <<Inherited docstring from sake.traits.RoleCache>>
        return redis_iterators.Iterator(self, ResourceIndex.ROLE, self.__deserialize_role, window_size=window_size)

    def iter_roles_for_guild(
        self, guild_id: snowflakes.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> traits.CacheIterator[guilds.Role]:
        # <<Inherited docstring from sake.traits.RoleCache>>
        key = self._generate_reference_key(ResourceIndex.GUILD, guild_id, ResourceIndex.ROLE)
        return redis_iterators.ReferenceIterator(
            self, key, ResourceIndex.ROLE, self.__deserialize_role, window_size=window_size
        )

    async def set_role(self, payload: ObjectT, /, guild_id: int) -> None:
        # <<Inherited docstring from sake.traits.RoleCache>>
        guild_id = int(guild_id)
        role_id = int(payload["id"])
        payload["guild_id"] = guild_id
        await self.get_connection(ResourceIndex.ROLE).set(role_id, payload)
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

    @classmethod
    def intents(cls) -> intents_.Intents:
        # <<Inherited docstring from ResourceClient>>
        return intents_.Intents.GUILDS | intents_.Intents.GUILD_VOICE_STATES

    @utility.as_listener(channel_events.ChannelDeleteEvent)  # type: ignore[misc]
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

    @utility.as_raw_listener("GUILD_CREATE")  # voice states aren't included in GUILD_UPDATE
    async def __on_guild_create(self, event: shard_events.ShardPayloadEvent, /) -> None:
        client = self.get_connection(ResourceIndex.VOICE_STATE)
        guild_id = int(event.payload["id"])
        await self.clear_voice_states_for_guild(guild_id)

        voice_states = event.payload["voice_states"]
        members = {int(payload["user"]["id"]): payload for payload in event.payload["members"]}

        windows = redis_iterators.chunk_values(
            _add_voice_fields(payload, guild_id, members[int(payload["user_id"])]) for payload in voice_states
        )
        setters = (client.hmset_dict(guild_id, _get_user_id, window) for window in windows)

        references = self.__generate_references(voice_states, guild_id=guild_id)
        reference_setters = (
            self._add_ids(ResourceIndex.CHANNEL, channel_id, ResourceIndex.VOICE_STATE, *state_ids)
            for channel_id, state_ids in references.items()
            if state_ids
        )
        await asyncio.gather(*setters, *reference_setters)

    @utility.as_listener(guild_events.GuildLeaveEvent)  # TODO: should this also clear when it goes unavailable?
    async def __on_guild_leave_event(self, event: guild_events.GuildLeaveEvent, /) -> None:
        await self.clear_voice_states_for_guild(event.guild_id)

    @utility.as_raw_listener("VOICE_STATE_UPDATE")
    async def __on_voice_state_update(self, event: shard_events.ShardPayloadEvent, /) -> None:
        guild_id = int(event.payload["guild_id"])
        if event.payload.get("channel_id") is None:
            await self.delete_voice_state(guild_id, int(event.payload["user_id"]))
        else:
            await self.set_voice_state(dict(event.payload), guild_id)

    @staticmethod
    def __pop_reference(keys: typing.MutableSequence[bytes], /) -> typing.Tuple[bytes, typing.Sequence[bytes]]:
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
            *(client.hdel(key, *values) for key, values in map(self.__pop_reference, references.values()) if values)
        )

    async def clear_voice_states_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.VoiceStateCache>>
        guild_id = int(guild_id)
        client = self.get_connection(ResourceIndex.VOICE_STATE)
        states = list(
            itertools.chain.from_iterable([v async for v in redis_iterators.iter_hash_values(client, guild_id)])
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
            payload = await self.get_connection(ResourceIndex.VOICE_STATE).hget(guild_id, user_id)
            channel_id = int(payload["channel_id"])
        except errors.EntryNotFound:
            pass
        else:
            await client.hdel(guild_id, user_id)
            await self._delete_ids(
                ResourceIndex.CHANNEL,
                channel_id,
                ResourceIndex.VOICE_STATE,
                user_id,
                reference_key=True,
            )

    async def get_voice_state(
        self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /
    ) -> voices.VoiceState:
        # <<Inherited docstring from sake.traits.VoiceStateCache>>
        payload = await self.get_connection(ResourceIndex.VOICE_STATE).hget(int(guild_id), int(user_id))
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
        await self.get_connection(ResourceIndex.VOICE_STATE).hset(guild_id, user_id, payload)
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
