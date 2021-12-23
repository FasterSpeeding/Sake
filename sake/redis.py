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

__all__: typing.Sequence[str] = [
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
import sys
import typing
import warnings

import aioredis
import hikari
from hikari import traits
from hikari.internal import time  # TODO: don't use this

from . import abc as sake_abc
from . import errors
from . import redis_iterators
from . import utility

if typing.TYPE_CHECKING:
    import collections.abc as collections
    import types

ObjectT = typing.Dict[str, typing.Any]
RedisKeyT = typing.Union[str, bytes]
RedisValueT = typing.Union[int, str, bytes]
_ResourceT = typing.TypeVar("_ResourceT", bound="ResourceClient")

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("hikari.sake.redis")
"""The logger instance used by this Sake implementation."""

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


if typing.TYPE_CHECKING and sys.version_info >= (3, 10):

    def _assert_optional_byte_sequence(
        seq: typing.Sequence[typing.Any], /
    ) -> typing.TypeGuard[collections.Sequence[bytes]]:
        ...


else:

    def _assert_optional_byte_sequence(seq: typing.Sequence[typing.Any], /) -> bool:
        for entry in seq:
            if entry is None:
                continue

            if isinstance(entry, bytes):
                break

            return False

        return True


_T = typing.TypeVar("_T")
_KeyT = typing.TypeVar("_KeyT")
_ValueT = typing.TypeVar("_ValueT")


def _to_map(
    iterator: typing.Iterable[_T], key_cast: typing.Callable[[_T], _KeyT], value_cast: typing.Callable[[_T], _ValueT]
) -> dict[_KeyT, _ValueT]:
    return {key_cast(entry): value_cast(entry) for entry in iterator}


# TODO: document that it isn't guaranteed that deletion will be finished before clear command coroutines finish.
# TODO: may go back to approach where client logic and interface are separate classes
class ResourceClient(sake_abc.Resource, abc.ABC):
    """A base client which all resources in this implementation will implement.

    !!! note
        This cannot be initialised by itself and is useless alone.

    Parameters
    ----------
    rest : hikari.traits.RESTAware
        The REST aware Hikari client to bind this resource client to.
    address : typing.Union[str, typing.Tuple[str, typing.Union[str, int]]
        The address to use to connect to the Redis backend server this
        resource is linked to.

        This may either be a string url in the form of `"redis://localhost:4242"`
        or a tuple of an address to a port in the form of `("localhost", 4242)`.

    Other Parameters
    ----------------
    event_manager : typing.Optional[hikari.api.EventManagerAware]
        The event manager to bind this resource client to.

        If provided then this client will automatically manage resources
        based on received gateway events.
    event_managed : bool
        Whether the client should be started and stopped based on the attached
        event_manager's lifetime events.
    password : typing.Optional[str]
        The password to use to connect to the backend Redis server.
    """

    __slots__: typing.Sequence[str] = (
        "__address",
        "__clients",
        "__default_expire",
        "__dump",
        "__event_manager",
        "__index_overrides",
        "__listeners",
        "__load",
        "__metadata",
        "__password",
        "__raw_listeners",
        "__rest",
        "__started",
    )

    def __init__(
        self,
        rest: traits.RESTAware,
        event_manager: typing.Optional[hikari.api.EventManager] = None,
        *,
        config: typing.Optional[typing.MutableMapping[str, typing.Any]] = None,
        default_expire: utility.ExpireT = DEFAULT_SLOW_EXPIRE,
        event_managed: bool = False,
        address: typing.Union[str, typing.Tuple[str, typing.Union[str, int]]],
        password: typing.Optional[str] = None,
        dumps: typing.Callable[[ObjectT], bytes] = lambda obj: json.dumps(obj).encode(),
        loads: typing.Callable[[bytes], ObjectT] = json.loads,
    ) -> None:
        self.__address = address
        self.__clients: typing.Dict[int, aioredis.Redis] = {}
        self.__metadata = config or {}
        self.__default_expire = utility.convert_expire_time(default_expire)
        self.__dump = dumps
        self.__event_manager = event_manager
        self.__index_overrides: typing.Dict[ResourceIndex, int] = {}
        self.__listeners = utility.find_listeners(self)
        self.__load = loads
        self.__password = password
        self.__raw_listeners = utility.find_raw_listeners(self)
        self.__rest = rest
        self.__started = False

        if event_manager:
            if event_managed:
                event_manager.subscribe(hikari.StartingEvent, self.__on_starting_event)
                event_manager.subscribe(hikari.StoppingEvent, self.__on_stopping_event)

            if isinstance(event_manager, traits.ShardAware):
                self.__check_intents(event_manager.intents)

        elif event_managed:
            raise ValueError("Client cannot be event_managed when not attached to an event manager.")

    async def __on_starting_event(self, _: hikari.StartingEvent, /) -> None:
        await self.open()

    async def __on_stopping_event(self, _: hikari.StoppingEvent, /) -> None:
        await self.close()

    async def __aenter__(self: _ResourceT) -> _ResourceT:
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: typing.Optional[typing.Type[Exception]],
        exc_val: typing.Optional[Exception],
        exc_tb: typing.Optional[types.TracebackType],
    ) -> None:
        await self.close()

    if not typing.TYPE_CHECKING:

        def __enter__(self) -> typing.NoReturn:
            # This is async only.
            cls = type(self)
            raise TypeError(f"{cls.__module__}.{cls.__qualname__} is async-only, did you mean 'async with'?") from None

        def __exit__(
            self,
            exc_type: typing.Optional[typing.Type[Exception]],
            exc_val: typing.Optional[Exception],
            exc_tb: typing.Optional[types.TracebackType],
        ) -> None:
            return None

    @property
    def default_expire(self) -> typing.Optional[int]:
        return self.__default_expire  # TODO: , pexpire=self.default_expire

    @property
    def event_manager(self) -> typing.Optional[hikari.api.EventManager]:
        return self.__event_manager

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
    def intents(cls) -> hikari.Intents:
        """The intents the resource requires to function properly.

        !!! note
            This should be called on specific base classes and will not be
            accurate after inheritance.

        Returns
        -------
        hikari.intents.Intents
            The intents the resource requires to function properly.
        """
        return hikari.Intents.NONE

    @property
    def metadata(self) -> typing.MutableMapping[str, typing.Any]:
        return self.__metadata

    @property  # unlike here where this is 100% required for building models.
    def rest(self) -> traits.RESTAware:
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
    def all_intents(cls) -> hikari.Intents:
        result = hikari.Intents.NONE
        for sub_class in cls.mro():
            if issubclass(sub_class, ResourceClient):
                result |= sub_class.intents()

        return result

    def __check_intents(self, intents: hikari.Intents, /) -> None:
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
        self: _ResourceT, index: ResourceIndex, override: typing.Optional[int] = None, /
    ) -> _ResourceT:
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
            raise ValueError(f"Resource index `{resource}` isn't valid for this client") from None

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
        resource_ = self.__index_overrides.get(resource, resource)
        return resource_ in self.__clients and self.__clients[resource_].connection is not None

    async def _try_bulk_set_users(self, users: typing.Iterator[ObjectT], /) -> None:
        if not (client := self.__clients.get(ResourceIndex.USER)):
            return

        expire_time = int(self.metadata.get("expire_user", DEFAULT_EXPIRE))
        # user_setters = []
        # expire_setters: typing.List[typing.Coroutine[typing.Any, typing.Any, None]] = []

        for window in redis_iterators.chunk_values(users):
            # transaction = client.multi_exec()
            #
            # for user_id in processed_window.keys():
            #     transaction.pexpire(user_id, expire_time)
            #
            # expire_setters.append(transaction.execute())
            await client.mset(_to_map(window, _get_id, self.dump))
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
        await client.set(str(int(payload["id"])), self.dump(payload), px=expire_time)

    async def __on_shard_payload_event(self, event: hikari.ShardPayloadEvent, /) -> None:
        if listeners := self.__raw_listeners.get(event.name.upper()):
            await asyncio.gather(*(listener(event) for listener in listeners))

    async def __spawn_connection(self, resource: int, /) -> None:
        client = await aioredis.from_url(
            self.__address,
            db=int(resource),
            password=self.__password,
            # encoding="utf-8",
        )
        self.__clients[resource] = client

    async def open(self) -> None:
        # <<Inherited docstring from sake.abc.Resource>>
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
            self.__event_manager.subscribe(hikari.ShardPayloadEvent, self.__on_shard_payload_event)

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=hikari.MissingIntentWarning)
                for event_type, listeners in self.__listeners.items():
                    for listener in listeners:
                        self.__event_manager.subscribe(event_type, listener)

        self.__started = True

    async def close(self) -> None:
        # <<Inherited docstring from sake.abc.Resource>>
        # We want to ensure that we both only do anything here if the client was already started when the method was
        # originally called and also that the client is marked as "closed" before this starts severing connections.
        was_started = self.__started
        self.__started = False

        if not was_started:
            return

        if self.__event_manager:
            try:
                self.__event_manager.unsubscribe(hikari.ShardPayloadEvent, self.__on_shard_payload_event)
            except LookupError:
                pass

            for event_type, listeners in self.__listeners.items():
                for listener in listeners:
                    try:
                        self.__event_manager.unsubscribe(event_type, listener)
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
    def _generate_reference_key(master: ResourceIndex, master_id: str, slave: ResourceIndex) -> str:
        return f"{master}:{master_id}:{int(slave)}"

    # To ensure at least 1 ID is always provided we have a required arg directly before the variable-length argument.
    async def _add_ids(
        self,
        master: ResourceIndex,
        master_id: str,
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
        master_id: str,
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
    ) -> typing.Dict[bytes, typing.List[bytes]]:
        client = self.get_connection(ResourceIndex.REFERENCE)
        keys = await client.keys(pattern=f"{master}:*:{slave}")
        values = await asyncio.gather(*map(client.smembers, keys))
        references = {keys[index]: value for index, value in enumerate(values)}
        asyncio.gather(*(client.srem(key, *members) for key, members in references.items() if members))
        return references

    async def _get_ids(
        self,
        master: ResourceIndex,
        master_id: str,
        slave: ResourceIndex,
        /,
        *,
        cast: typing.Callable[[bytes], _T],
    ) -> typing.List[_T]:
        key = self._generate_reference_key(master, master_id, slave)
        client = self.get_connection(ResourceIndex.REFERENCE)
        members = typing.cast("typing.List[bytes]", await client.smembers(key))
        return [*map(cast, members)]


# To avoid breaking Mro conflicts this is kept as a separate class despite only being exposed through the UserCache.
class _MeCache(ResourceClient, sake_abc.MeCache):
    __slots__: typing.Sequence[str] = ()

    __ME_KEY: typing.Final[str] = "ME"

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        # This isn't a full MeCache implementation in itself as it's reliant on the UserCache implementation.
        return ()

    @classmethod
    def intents(cls) -> hikari.Intents:
        # <<Inherited docstring from ResourceClient>>
        return hikari.Intents.NONE

    @utility.as_raw_listener("USER_UPDATE")
    async def __on_own_user_update(self, event: hikari.ShardPayloadEvent, /) -> None:
        await self.set_me(dict(event.payload))

    @utility.as_raw_listener("READY")
    async def __on_shard_ready(self, event: hikari.ShardPayloadEvent, /) -> None:
        await self.set_me(event.payload["user"])

    async def delete_me(self) -> None:
        # <<Inherited docstring from sake.abc.MeCache>>
        await self.get_connection(ResourceIndex.USER).delete(self.__ME_KEY)

    async def get_me(self) -> hikari.OwnUser:
        # <<Inherited docstring from sake.abc.MeCache>>
        if payload := await self.get_connection(ResourceIndex.USER).get(self.__ME_KEY):
            return self.rest.entity_factory.deserialize_my_user(self.load(payload))

        raise errors.EntryNotFound("Own user not found")

    async def set_me(self, payload: ObjectT, /) -> None:
        # <<Inherited docstring from sake.abc.MeCache>>
        await self.get_connection(ResourceIndex.USER).set(self.__ME_KEY, self.dump(payload))
        await self._try_set_user(payload)


class UserCache(_MeCache, sake_abc.UserCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        return (ResourceIndex.USER,)

    @classmethod
    def intents(cls) -> hikari.Intents:
        # <<Inherited docstring from ResourceClient>>
        return hikari.Intents.NONE

    def with_user_expire(self: _ResourceT, expire: typing.Optional[utility.ExpireT], /) -> _ResourceT:
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
        _ResourceT
            The client this is being called on to enable chained calls.
        """
        if expire is not None:
            self.metadata["expire_user"] = utility.convert_expire_time(expire)

        elif "expire_user" in self.metadata:
            del self.metadata["expire_user"]

        return self

    async def clear_users(self) -> None:
        # <<Inherited docstring from sake.abc.UserCache>>
        await self.get_connection(ResourceIndex.USER).flushdb()

    async def delete_user(self, user_id: hikari.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.abc.UserCache>>
        await self.get_connection(ResourceIndex.USER).delete(str(user_id))

    async def get_user(self, user_id: hikari.Snowflakeish, /) -> hikari.User:
        # <<Inherited docstring from sake.abc.UserCache>>
        if payload := await self.get_connection(ResourceIndex.USER).get(str(user_id)):
            return self.rest.entity_factory.deserialize_user(self.load(payload))

        raise errors.EntryNotFound("User not found")

    def iter_users(self, *, window_size: int = WINDOW_SIZE) -> sake_abc.CacheIterator[hikari.User]:
        # <<Inherited docstring from sake.abc.UserCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.USER, self.rest.entity_factory.deserialize_user, window_size=window_size
        )

    async def set_user(self, payload: ObjectT, /, *, expire_time: typing.Optional[utility.ExpireT] = None) -> None:
        # <<Inherited docstring from sake.abc.UserCache>>
        if expire_time is None:
            expire_time = int(self.metadata.get("expire_user", DEFAULT_EXPIRE))

        else:
            expire_time = utility.convert_expire_time(expire_time)

        await self.get_connection(ResourceIndex.USER).set(str(int(payload["id"])), self.dump(payload), px=expire_time)


def _add_guild_id(data: ObjectT, /, guild_id: str) -> ObjectT:
    data["guild_id"] = guild_id
    return data


def _get_id(data: ObjectT, /) -> str:
    return str(int(data["id"]))


def _decode_prefixes(data: typing.Any, /) -> typing.List[str]:
    return [prefix.decode() for prefix in typing.cast("typing.List[bytes]", data)]


class PrefixCache(ResourceClient, sake_abc.PrefixCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        return (ResourceIndex.PREFIX,)

    @classmethod
    def intents(cls) -> hikari.Intents:
        # <<Inherited docstring from ResourceClient>>
        return hikari.Intents.NONE

    async def clear_prefixes(self) -> None:
        # <<Inherited docstring from sake.abc.PrefixCache>>
        await self.get_connection(ResourceIndex.PREFIX).flushdb()

    async def clear_prefixes_for_guild(self, guild_id: hikari.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.abc.PrefixCache>>
        await self.get_connection(ResourceIndex.PREFIX).delete(str(guild_id))

    async def delete_prefixes(self, guild_id: hikari.Snowflakeish, prefix: str, /, *prefixes: str) -> None:
        # <<Inherited docstring from sake.abc.PrefixCache>>
        await self.get_connection(ResourceIndex.PREFIX).srem(str(guild_id), prefix, *prefixes)

    async def get_prefixes(self, guild_id: hikari.Snowflakeish, /) -> typing.Sequence[str]:
        # <<Inherited docstring from sake.abc.PrefixCache>>
        if data := await self.get_connection(ResourceIndex.PREFIX).smembers(str(guild_id)):
            return _decode_prefixes(data)

        raise errors.EntryNotFound(f"Prefix entry `{guild_id}` not found")

    def iter_prefixes(self, *, window_size: int = WINDOW_SIZE) -> sake_abc.CacheIterator[typing.Sequence[str]]:
        # <<Inherited docstring from sake.abc.PrefixCache>>
        return redis_iterators.Iterator(self, ResourceIndex.PREFIX, _decode_prefixes, window_size=window_size)

    async def add_prefixes(self, guild_id: hikari.Snowflakeish, prefix: str, /, *prefixes: str) -> None:
        # <<Inherited docstring from sake.abc.PrefixCache>>
        await self.get_connection(ResourceIndex.PREFIX).sadd(str(guild_id), str(prefix), *map(str, prefixes))

    async def set_prefixes(self, guild_id: hikari.Snowflakeish, prefixes: typing.Iterable[str], /) -> None:
        # <<Inherited docstring from sake.abc.PrefixCache>>
        await self.clear_prefixes_for_guild(guild_id)
        if prefixes:
            await self.add_prefixes(guild_id, *prefixes)


class EmojiCache(_Reference, sake_abc.RefEmojiCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        return (ResourceIndex.EMOJI,)

    @classmethod
    def intents(cls) -> hikari.Intents:
        # <<Inherited docstring from ResourceClient>>
        return hikari.Intents.GUILD_EMOJIS | hikari.Intents.GUILDS

    async def __bulk_add_emojis(self, emojis: typing.Iterable[ObjectT], /, guild_id: int) -> None:
        str_guild_id = str(guild_id)
        client = self.get_connection(ResourceIndex.EMOJI)
        windows = redis_iterators.chunk_values(_add_guild_id(emoji, str_guild_id) for emoji in emojis)
        setters = (client.mset(_to_map(window, _get_id, self.dump)) for window in windows)
        user_setter = self._try_bulk_set_users(user for payload in emojis if (user := payload.get("user")))
        reference_setter = self._add_ids(
            ResourceIndex.GUILD, str_guild_id, ResourceIndex.EMOJI, *(int(payload["id"]) for payload in emojis)
        )
        await asyncio.gather(*setters, user_setter, reference_setter)

    @utility.as_raw_listener("GUILD_EMOJIS_UPDATE")
    async def __on_emojis_update(self, event: hikari.ShardPayloadEvent, /) -> None:
        guild_id = int(event.payload["guild_id"])
        await self.clear_emojis_for_guild(guild_id)

        if emojis := event.payload.get("emojis"):
            await self.__bulk_add_emojis(emojis, guild_id)

    #  TODO: can we also listen for member delete to manage this?
    @utility.as_raw_listener("GUILD_CREATE", "GUILD_UPDATE")
    async def __on_guild_create_update(self, event: hikari.ShardPayloadEvent, /) -> None:
        guild_id = int(event.payload["id"])
        await self.clear_emojis_for_guild(guild_id)

        if emojis := event.payload["emojis"]:
            await self.__bulk_add_emojis(emojis, guild_id)

    @utility.as_listener(hikari.GuildLeaveEvent)
    async def __on_guild_leave_event(self, event: hikari.GuildLeaveEvent, /) -> None:
        await self.clear_emojis_for_guild(event.guild_id)

    async def clear_emojis(self) -> None:
        # <<Inherited docstring from sake.abc.EmojiCache>>
        emoji_ids = await self._dump_relationship(ResourceIndex.GUILD, ResourceIndex.EMOJI)
        client = self.get_connection(ResourceIndex.EMOJI)
        await asyncio.gather(
            *itertools.starmap(client.delete, redis_iterators.chunk_values(itertools.chain(emoji_ids)))
        )

    async def clear_emojis_for_guild(self, guild_id: hikari.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.abc.EmojiCache>>
        str_guild_id = str(guild_id)
        emoji_ids = await self._get_ids(ResourceIndex.GUILD, str_guild_id, ResourceIndex.EMOJI, cast=bytes)
        if not emoji_ids:
            return

        await self._delete_ids(ResourceIndex.GUILD, str_guild_id, ResourceIndex.EMOJI, *emoji_ids)
        client = self.get_connection(ResourceIndex.EMOJI)
        # Gather is awaited here to ensure that when internally bulk setting entries after triggering a bulk deletion
        # (based on events) we don't risk deleting entries after we've re-added them.
        await asyncio.gather(*itertools.starmap(client.delete, redis_iterators.chunk_values(emoji_ids)))

    async def delete_emoji(
        self, emoji_id: hikari.Snowflakeish, /, *, guild_id: typing.Optional[hikari.Snowflakeish] = None
    ) -> None:
        # <<Inherited docstring from sake.abc.EmojiCache>>
        str_emoji_id = str(emoji_id)
        if guild_id is None:
            payload = await self.get_connection(ResourceIndex.EMOJI).get(str_emoji_id)
            if not payload:
                return

            guild_id = int(self.load(payload)["guild_id"])

        client = self.get_connection(ResourceIndex.EMOJI)
        await self._delete_ids(ResourceIndex.GUILD, str(guild_id), ResourceIndex.EMOJI, str_emoji_id)
        await client.delete(str_emoji_id)

    async def get_emoji(self, emoji_id: hikari.Snowflakeish, /) -> hikari.KnownCustomEmoji:
        # <<Inherited docstring from sake.abc.EmojiCache>>
        if payload := await self.get_connection(ResourceIndex.EMOJI).get(str(emoji_id)):
            return self.__deserialize_known_custom_emoji(self.load(payload))

        raise errors.EntryNotFound("Emoji not found")

    def __deserialize_known_custom_emoji(self, payload: ObjectT, /) -> hikari.KnownCustomEmoji:
        guild_id = hikari.Snowflake(payload["guild_id"])
        return self.rest.entity_factory.deserialize_known_custom_emoji(payload, guild_id=guild_id)

    def iter_emojis(self, *, window_size: int = WINDOW_SIZE) -> sake_abc.CacheIterator[hikari.KnownCustomEmoji]:
        # <<Inherited docstring from sake.abc.EmojiCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.EMOJI, self.__deserialize_known_custom_emoji, window_size=window_size
        )

    def iter_emojis_for_guild(
        self, guild_id: hikari.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.KnownCustomEmoji]:
        # <<Inherited docstring from sake.abc.EmojiCache>>
        key = self._generate_reference_key(ResourceIndex.GUILD, str(guild_id), ResourceIndex.EMOJI)
        return redis_iterators.ReferenceIterator(
            self, key, ResourceIndex.EMOJI, self.__deserialize_known_custom_emoji, window_size=window_size
        )

    async def set_emoji(self, guild_id: hikari.Snowflakeish, payload: ObjectT, /) -> None:
        # <<Inherited docstring from sake.abc.EmojiCache>>
        str_guild_id = str(guild_id)
        emoji_id = str(int(payload["id"]))
        payload["guild_id"] = str_guild_id  # TODO: is this necessary
        await self.get_connection(ResourceIndex.EMOJI).set(emoji_id, self.dump(payload))
        await self._add_ids(ResourceIndex.GUILD, str_guild_id, ResourceIndex.EMOJI, emoji_id)

        if (user := payload.get("user")) is not None:
            await self._try_set_user(user)


class GuildCache(ResourceClient, sake_abc.GuildCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        return (ResourceIndex.GUILD,)

    @classmethod
    def intents(cls) -> hikari.Intents:
        # <<Inherited docstring from ResourceClient>>
        return hikari.Intents.GUILDS

    @utility.as_raw_listener("GUILD_CREATE", "GUILD_UPDATE")
    async def __on_guild_create_update(self, event: hikari.ShardPayloadEvent, /) -> None:
        await self.set_guild(dict(event.payload))

    @utility.as_listener(hikari.GuildLeaveEvent)
    async def __on_guild_leave_event(self, event: hikari.GuildLeaveEvent, /) -> None:
        await self.delete_guild(event.guild_id)

    async def clear_guilds(self) -> None:
        # <<Inherited docstring from sake.abc.GuildCache>>
        await self.get_connection(ResourceIndex.GUILD).flushdb()

    async def delete_guild(self, guild_id: hikari.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.abc.GuildCache>>
        await self.get_connection(ResourceIndex.GUILD).delete(str(guild_id))

    def __deserialize_guild(self, payload: ObjectT, /) -> hikari.GatewayGuild:
        # Hikari's deserialization logic expcets these fields to be present.
        payload["roles"] = []
        payload["emojis"] = []
        return self.rest.entity_factory.deserialize_gateway_guild(payload).guild

    async def get_guild(self, guild_id: hikari.Snowflakeish, /) -> hikari.GatewayGuild:
        # <<Inherited docstring from sake.abc.GuildCache>>
        if payload := await self.get_connection(ResourceIndex.GUILD).get(str(guild_id)):
            return self.__deserialize_guild(self.load(payload))

        raise errors.EntryNotFound("Guild not found")

    def iter_guilds(self, *, window_size: int = WINDOW_SIZE) -> sake_abc.CacheIterator[hikari.GatewayGuild]:
        # <<Inherited docstring from sake.abc.GuildCache>>
        return redis_iterators.Iterator(self, ResourceIndex.GUILD, self.__deserialize_guild, window_size=window_size)

    async def set_guild(self, payload: ObjectT, /) -> None:
        # <<Inherited docstring from sake.abc.GuildCache>>
        # These entries are cached in separate stores.
        payload.pop("channels", None)
        payload.pop("emojis", None)
        payload.pop("members", None)
        payload.pop("presences", None)
        payload.pop("roles", None)
        payload.pop("threads", None)
        payload.pop("voice_states", None)
        await self.get_connection(ResourceIndex.GUILD).set(str(int(payload["id"])), self.dump(payload))


# TODO: guild_id isn't always included in the payload?
class GuildChannelCache(_Reference, sake_abc.RefGuildChannelCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from sake.abc.Resource>>
        return (ResourceIndex.CHANNEL,)

    @classmethod
    def intents(cls) -> hikari.Intents:
        # <<Inherited docstring from ResourceClient>>
        return hikari.Intents.GUILDS

    @utility.as_raw_listener("CHANNEL_CREATE", "CHANNEL_UPDATE")
    async def __on_channel_create_update(self, event: hikari.ShardPayloadEvent, /) -> None:
        await self.set_guild_channel(dict(event.payload))

    @utility.as_listener(hikari.GuildChannelDeleteEvent)  # we don't actually get events for DM channels
    async def __on_channel_delete_event(self, event: hikari.GuildChannelDeleteEvent, /) -> None:
        await self.delete_guild_channel(event.channel_id, guild_id=event.guild_id)

    @utility.as_raw_listener("CHANNEL_PINS_UPDATE")
    async def __on_channel_pins_update(self, event: hikari.ShardPayloadEvent, /) -> None:
        if payload := await self.get_connection(ResourceIndex.CHANNEL).get(str(int(event.payload["channel_id"]))):
            payload = self.load(payload)
            payload["last_pin_timestamp"] = event.payload.get("last_pin_timestamp")
            await self.set_guild_channel(payload)

    @utility.as_raw_listener("GUILD_CREATE")  # GUILD_UPDATE doesn't include channels
    async def __on_guild_create_update(self, event: hikari.ShardPayloadEvent, /) -> None:
        guild_id = int(event.payload["id"])
        str_guild_id = str(guild_id)
        await self.clear_guild_channels_for_guild(guild_id)
        client = self.get_connection(ResourceIndex.CHANNEL)

        channel_ids: typing.List[int] = []
        setters: typing.List[typing.Awaitable[None]] = []
        raw_channels = event.payload["channels"]
        for window in redis_iterators.chunk_values(_add_guild_id(payload, str_guild_id) for payload in raw_channels):
            setters.append(client.mset(_to_map(window, _get_id, self.dump)))
            channel_ids.extend(int(payload["id"]) for payload in window)

        id_setter = self._add_ids(ResourceIndex.GUILD, str_guild_id, ResourceIndex.CHANNEL, *channel_ids)
        await asyncio.gather(*setters, id_setter)

    @utility.as_listener(hikari.GuildLeaveEvent)  # TODO: can we also use member remove events here?
    async def __on_guild_leave_event(self, event: hikari.GuildLeaveEvent, /) -> None:
        await self.clear_guild_channels_for_guild(event.guild_id)

    async def clear_guild_channels(self) -> None:
        # <<Inherited docstring from sake.abc.GuildChannelCache>>
        channel_ids = await self._dump_relationship(ResourceIndex.GUILD, ResourceIndex.CHANNEL)
        client = self.get_connection(ResourceIndex.CHANNEL)
        await asyncio.gather(
            *itertools.starmap(client.delete, redis_iterators.chunk_values(itertools.chain(channel_ids)))
        )

    async def clear_guild_channels_for_guild(self, guild_id: hikari.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.abc.GuildChannelCache>>
        str_guild_id = str(guild_id)
        channel_ids = await self._get_ids(ResourceIndex.GUILD, str_guild_id, ResourceIndex.CHANNEL, cast=bytes)
        if not channel_ids:
            return

        await self._delete_ids(ResourceIndex.GUILD, str_guild_id, ResourceIndex.CHANNEL, *channel_ids)
        client = self.get_connection(ResourceIndex.CHANNEL)
        #  TODO: is there any benefit to chunking on bulk delete?
        # Gather is awaited here to ensure that when internally bulk setting entries after triggering a bulk deletion
        # (based on events) we don't risk deleting entries after we've re-added them.
        await asyncio.gather(*itertools.starmap(client.delete, redis_iterators.chunk_values(channel_ids)))

    async def delete_guild_channel(
        self, channel_id: hikari.Snowflakeish, /, *, guild_id: typing.Optional[hikari.Snowflakeish] = None
    ) -> None:
        # <<Inherited docstring from sake.abc.GuildChannelCache>>
        str_channel_id = str(channel_id)
        if guild_id is None:
            payload = await self.get_connection(ResourceIndex.CHANNEL).get(str_channel_id)
            if not payload:
                return

            guild_id = int(self.load(payload)["guild_id"])

        client = self.get_connection(ResourceIndex.CHANNEL)
        await self._delete_ids(ResourceIndex.GUILD, str(guild_id), ResourceIndex.CHANNEL, str_channel_id)
        await client.delete(str_channel_id)

    async def get_guild_channel(self, channel_id: hikari.Snowflakeish, /) -> hikari.GuildChannel:
        # <<Inherited docstring from sake.abc.GuildChannelCache>>
        if payload := await self.get_connection(ResourceIndex.CHANNEL).get(str(channel_id)):
            return self.__deserialize_guild_channel(self.load(payload))

        raise errors.EntryNotFound("Guild channel not found")

    def __deserialize_guild_channel(self, payload: ObjectT, /) -> hikari.GuildChannel:
        channel = self.rest.entity_factory.deserialize_channel(payload)
        assert isinstance(channel, hikari.GuildChannel)
        return channel

    def iter_guild_channels(self, *, window_size: int = WINDOW_SIZE) -> sake_abc.CacheIterator[hikari.GuildChannel]:
        # <<Inherited docstring from sake.abc.GuildChannelCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.CHANNEL, self.__deserialize_guild_channel, window_size=window_size
        )

    def iter_guild_channels_for_guild(
        self, guild_id: hikari.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.GuildChannel]:
        # <<Inherited docstring from sake.abc.GuildChannelCache>>
        key = self._generate_reference_key(ResourceIndex.GUILD, str(guild_id), ResourceIndex.CHANNEL)
        return redis_iterators.ReferenceIterator(
            self, key, ResourceIndex.CHANNEL, self.__deserialize_guild_channel, window_size=window_size
        )

    async def set_guild_channel(self, payload: ObjectT, /) -> None:
        # <<Inherited docstring from sake.abc.GuildChannelCache>>
        str_channel_id = str(int(payload["id"]))
        guild_id = str(int(payload["guild_id"]))
        await self.get_connection(ResourceIndex.CHANNEL).set(str_channel_id, self.dump(payload))
        await self._add_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.CHANNEL, str_channel_id)


class IntegrationCache(_Reference, sake_abc.IntegrationCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from sake.abc.Resource>>
        return (ResourceIndex.INTEGRATION,)

    @classmethod
    def intents(cls) -> hikari.Intents:
        # <<Inherited docstring from ResourceClient>>
        return hikari.Intents.GUILDS | hikari.Intents.GUILD_INTEGRATIONS

    @utility.as_listener(hikari.GuildLeaveEvent)
    async def __on_guild_leave_event(self, event: hikari.GuildLeaveEvent, /) -> None:
        await self.clear_integrations_for_guild(event.guild_id)

    @utility.as_raw_listener("INTEGRATION_CREATE", "INTEGRATION_UPDATE")
    async def __on_integration_event(self, event: hikari.ShardPayloadEvent, /) -> None:
        await self.set_integration(int(event.payload["guild_id"]), dict(event.payload))

    @utility.as_listener(hikari.IntegrationDeleteEvent)
    async def __on_integration_delete_event(self, event: hikari.IntegrationDeleteEvent, /) -> None:
        await self.delete_integration(event.id, guild_id=event.guild_id)

    async def clear_integrations(self) -> None:
        # <<Inherited docstring from sake.abc.IntegrationCache>>
        ids = await self._dump_relationship(ResourceIndex.GUILD, ResourceIndex.INTEGRATION)
        client = self.get_connection(ResourceIndex.INTEGRATION)
        await asyncio.gather(*itertools.starmap(client.delete, redis_iterators.chunk_values(ids)))

    async def clear_integrations_for_guild(self, guild_id: hikari.Snowflakeish, /) -> None:
        str_guild_id = str(guild_id)
        ids = await self._get_ids(ResourceIndex.GUILD, str_guild_id, ResourceIndex.INTEGRATION, cast=bytes)
        if not ids:
            return

        await self._delete_ids(ResourceIndex.GUILD, str_guild_id, ResourceIndex.INTEGRATION, *ids)
        client = self.get_connection(ResourceIndex.INTEGRATION)
        await asyncio.gather(*itertools.starmap(client.delete, redis_iterators.chunk_values(ids)))

    async def delete_integration(
        self, integration_id: hikari.Snowflakeish, /, *, guild_id: typing.Optional[hikari.Snowflakeish] = None
    ) -> None:
        # <<Inherited docstring from sake.abc.IntegrationCache>>
        str_integration_id = str(integration_id)
        if guild_id is None:
            payload = await self.get_connection(ResourceIndex.INTEGRATION).get(str_integration_id)
            if not payload:
                return

            guild_id = int(self.load(payload)["guild_id"])

        await self._delete_ids(ResourceIndex.GUILD, str(guild_id), ResourceIndex.INTEGRATION, str_integration_id)
        client = self.get_connection(ResourceIndex.INTEGRATION)
        await client.delete(str_integration_id)

    async def get_integration(self, integration_id: hikari.Snowflakeish, /) -> hikari.Integration:
        # <<Inherited docstring from sake.abc.IntegrationCache>>
        if payload := await self.get_connection(ResourceIndex.INTEGRATION).get(str(integration_id)):
            return self.rest.entity_factory.deserialize_integration(self.load(payload))

        raise errors.EntryNotFound("Integration not found")

    def iter_integrations(self, *, window_size: int = WINDOW_SIZE) -> sake_abc.CacheIterator[hikari.Integration]:
        # <<Inherited docstring from sake.abc.IntegrationCache>>
        return redis_iterators.Iterator(
            self,
            ResourceIndex.INTEGRATION,
            builder=self.rest.entity_factory.deserialize_integration,
            window_size=window_size,
        )

    def iter_integrations_for_guild(
        self, guild_id: hikari.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.Integration]:
        key = self._generate_reference_key(ResourceIndex.GUILD, str(guild_id), ResourceIndex.INTEGRATION).encode()
        return redis_iterators.ReferenceIterator(
            self,
            key,
            ResourceIndex.INTEGRATION,
            builder=self.rest.entity_factory.deserialize_integration,
            window_size=window_size,
        )

    async def set_integration(self, guild_id: hikari.Snowflakeish, payload: ObjectT, /) -> None:
        # <<Inherited docstring from sake.abc.IntegrationCache>>
        integration_id = str(int(payload["id"]))
        str_guild_id = str(guild_id)
        payload["guild_id"] = str_guild_id  # TODO: is this necessary?
        await self.get_connection(ResourceIndex.INTEGRATION).set(integration_id, self.dump(payload))
        await self._add_ids(ResourceIndex.GUILD, str_guild_id, ResourceIndex.INTEGRATION, integration_id)


class InviteCache(ResourceClient, sake_abc.InviteCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from sake.abc.Resource>>
        return (ResourceIndex.INVITE,)

    @classmethod
    def intents(cls) -> hikari.Intents:
        # <<Inherited docstring from ResourceClient>>
        return hikari.Intents.GUILD_INVITES

    @utility.as_raw_listener("INVITE_CREATE")
    async def __on_invite_create(self, event: hikari.ShardPayloadEvent, /) -> None:
        await self.set_invite(dict(event.payload))

    @utility.as_listener(hikari.InviteDeleteEvent)
    async def __on_invite_delete_event(self, event: hikari.InviteDeleteEvent, /) -> None:
        await self.delete_invite(event.code)  # TODO: on guild leave?

    def with_invite_expire(self: _ResourceT, expire: typing.Optional[utility.ExpireT], /) -> _ResourceT:
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
        _ResourceT
            The client this is being called on to enable chained calls.
        """
        if expire is not None:
            self.metadata["expire_invite"] = utility.convert_expire_time(expire)

        elif "expire_invite" in self.metadata:
            del self.metadata["expire_invite"]

        return self

    async def clear_invites(self) -> None:
        # <<Inherited docstring from sake.abc.InviteCache>>
        client = self.get_connection(ResourceIndex.INVITE).flushdb()
        await client

    async def delete_invite(self, invite_code: str, /) -> None:
        # <<Inherited docstring from sake.abc.InviteCache>>
        await self.get_connection(ResourceIndex.INVITE).delete(str(invite_code))

    async def get_invite(self, invite_code: str, /) -> hikari.InviteWithMetadata:
        # <<Inherited docstring from sake.abc.InviteCache>>
        if payload := await self.get_connection(ResourceIndex.INVITE).get(str(invite_code)):
            return self.rest.entity_factory.deserialize_invite_with_metadata(self.load(payload))

        raise errors.EntryNotFound("Invite not found")

    def iter_invites(self, *, window_size: int = WINDOW_SIZE) -> sake_abc.CacheIterator[hikari.InviteWithMetadata]:
        # <<Inherited docstring from sake.abc.InviteCache>>
        return redis_iterators.Iterator(
            self,
            ResourceIndex.INVITE,
            self.rest.entity_factory.deserialize_invite_with_metadata,
            window_size=window_size,
        )

    async def set_invite(self, payload: ObjectT, /, *, expire_time: typing.Optional[utility.ExpireT] = None) -> None:
        # <<Inherited docstring from sake.abc.InviteCache>>
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
        await self.get_connection(ResourceIndex.INVITE).set(str(payload["code"]), self.dump(payload), px=expire_time)

        if target_user := payload.get("target_user"):
            await self._try_set_user(target_user)

        if inviter := payload.get("inviter"):
            await self._try_set_user(inviter)


class _OwnIDStore:
    __slots__: typing.Sequence[str] = ("_event", "_is_waiting", "_lock", "_rest", "value")

    KEY: typing.Final[str] = "OWN_ID"

    def __init__(self, rest: traits.RESTAware, /) -> None:
        self._lock: typing.Optional[asyncio.Lock] = None
        self._is_waiting = False
        self._rest = rest
        self.value: typing.Optional[hikari.Snowflake] = None

    async def await_value(self) -> int:
        if self._is_waiting:
            assert self._lock
            async with self._lock:
                assert self.value is not None
                return self.value

        self._lock = asyncio.Lock()
        async with self._lock:
            user = await self._rest.rest.fetch_my_user()
            self.value = user.id
            return self.value


def _get_sub_user_id(payload: ObjectT, /) -> str:
    return str(int(payload["user"]["id"]))


class MemberCache(ResourceClient, sake_abc.MemberCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from sake.abc.Resource>>
        return (ResourceIndex.MEMBER,)

    @classmethod
    def intents(cls) -> hikari.Intents:
        # <<Inherited docstring from ResourceClient>>
        return hikari.Intents.GUILD_MEMBERS | hikari.Intents.GUILDS

    def chunk_on_guild_create(self: _ResourceT, shard_aware: typing.Optional[traits.ShardAware], /) -> _ResourceT:
        if shard_aware:
            if not self.event_manager:
                raise ValueError("An event manager-less cache instance cannot request member chunk on guild create")

            if not (shard_aware.intents & hikari.Intents.GUILD_MEMBERS):
                raise ValueError("Cannot request guild member chunks without the GUILD_MEMBERS intents declared")

            presences = (shard_aware.intents & hikari.Intents.GUILD_PRESENCES) == hikari.Intents.GUILD_PRESENCES
            self.metadata["chunk_on_create"] = shard_aware
            self.metadata["chunk_presences"] = presences and isinstance(self, sake_abc.PresenceCache)

        else:
            self.metadata.pop("chunk_on_create", None)

        return self

    async def __bulk_set_members(self, members: typing.Iterator[ObjectT], /, guild_id: int) -> None:
        str_guild_id = str(guild_id)
        client = self.get_connection(ResourceIndex.MEMBER)
        windows = redis_iterators.chunk_values((_add_guild_id(payload, str_guild_id) for payload in members))
        setters = (
            client.hset(str_guild_id, mapping=_to_map(window, _get_sub_user_id, self.dump)) for window in windows
        )
        user_setter = self._try_bulk_set_users(member["user"] for member in members)
        await asyncio.gather(*setters, user_setter)

    @utility.as_raw_listener("GUILD_CREATE")  # members aren't included in GUILD_UPDATE
    async def __on_guild_create(self, event: hikari.ShardPayloadEvent, /) -> None:
        guild_id = int(event.payload["id"])
        await self.clear_members_for_guild(guild_id)
        await self.__bulk_set_members(event.payload["members"], guild_id)

        if shard_aware := self.metadata.get("chunk_on_create"):
            assert isinstance(shard_aware, traits.ShardAware)
            include_presences = bool(self.metadata.get("chunk_presences", False))
            await shard_aware.request_guild_members(guild_id, include_presences=include_presences)

    @utility.as_listener(hikari.GuildLeaveEvent)
    async def __on_guild_leave_event(self, event: hikari.GuildLeaveEvent, /) -> None:
        await self.clear_members_for_guild(event.guild_id)

    @utility.as_raw_listener("GUILD_MEMBER_ADD", "GUILD_MEMBER_UPDATE")
    async def __on_guild_member_add_update(self, event: hikari.ShardPayloadEvent, /) -> None:
        await self.set_member(int(event.payload["guild_id"]), dict(event.payload))

    @utility.as_listener(hikari.MemberDeleteEvent)
    async def __on_member_delete_event(self, event: hikari.MemberDeleteEvent, /) -> None:
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
    async def __on_guild_members_chunk_event(self, event: hikari.ShardPayloadEvent, /) -> None:
        await self.__bulk_set_members(event.payload["members"], int(event.payload["guild_id"]))

    async def clear_members(self) -> None:
        # <<Inherited docstring from sake.abc.MemberCache>>
        await self.get_connection(ResourceIndex.MEMBER).flushdb()

    async def clear_members_for_guild(self, guild_id: hikari.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.abc.MemberCache>>
        await self.get_connection(ResourceIndex.MEMBER).delete(str(guild_id))

    async def delete_member(self, guild_id: hikari.Snowflakeish, user_id: hikari.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.abc.MemberCache>>
        await self.get_connection(ResourceIndex.MEMBER).hdel(str(guild_id), str(user_id))

    async def get_member(self, guild_id: hikari.Snowflakeish, user_id: hikari.Snowflakeish, /) -> hikari.Member:
        # <<Inherited docstring from sake.abc.MemberCache>>
        if payload := await self.get_connection(ResourceIndex.MEMBER).hget(str(guild_id), str(user_id)):
            return self.rest.entity_factory.deserialize_member(self.load(payload))

        raise errors.EntryNotFound("Member not found")

    def iter_members(self, *, window_size: int = WINDOW_SIZE) -> sake_abc.CacheIterator[hikari.Member]:
        # <<Inherited docstring from sake.abc.MemberCache>>
        return redis_iterators.MultiMapIterator(
            self, ResourceIndex.MEMBER, self.rest.entity_factory.deserialize_member, window_size=window_size
        )

    def iter_members_for_guild(
        self, guild_id: hikari.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.Member]:
        # <<Inherited docstring from sake.abc.MemberCache>>
        return redis_iterators.SpecificMapIterator(
            self,
            str(guild_id),
            ResourceIndex.MEMBER,
            self.rest.entity_factory.deserialize_member,
            window_size=window_size,
        )

    async def set_member(self, guild_id: hikari.Snowflakeish, payload: ObjectT, /) -> None:
        # <<Inherited docstring from sake.abc.MemberCache>>
        user_data = payload["user"]
        await self.get_connection(ResourceIndex.MEMBER).hset(
            str(guild_id), str(int(user_data["id"])), self.dump(payload)
        )
        await self._try_set_user(user_data)


class MessageCache(ResourceClient, sake_abc.MessageCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from sake.abc.Resource>>
        return (ResourceIndex.MESSAGE,)

    @classmethod
    def intents(cls) -> hikari.Intents:
        # <<Inherited docstring from ResourceClient>>
        return hikari.Intents.ALL_MESSAGES

    @utility.as_raw_listener("MESSAGE_CREATE")
    async def __on_message_create(self, event: hikari.ShardPayloadEvent, /) -> None:
        await self.set_message(dict(event.payload))

    @utility.as_listener(hikari.MessageDeleteEvent)
    async def __on_message_delete_event(self, event: hikari.MessageDeleteEvent, /) -> None:
        await self.delete_message(event.message_id)

    @utility.as_listener(hikari.GuildBulkMessageDeleteEvent)
    async def __on_guild_message_bulk_delete_event(self, event: hikari.GuildBulkMessageDeleteEvent, /) -> None:
        client = self.get_connection(ResourceIndex.MESSAGE)
        message_ids = map(str, event.message_ids)
        await asyncio.gather(*itertools.starmap(client.delete, redis_iterators.chunk_values(message_ids)))

    @utility.as_raw_listener("MESSAGE_UPDATE")
    async def __on_message_update(self, event: hikari.ShardPayloadEvent, /) -> None:
        await self.update_message(dict(event.payload))

    def with_message_expire(self: _ResourceT, expire: typing.Optional[utility.ExpireT], /) -> _ResourceT:
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
        _ResourceT
            The client this is being called on to enable chained calls.
        """
        if expire is not None:
            self.metadata["expire_message"] = utility.convert_expire_time(expire)

        elif "expire_message" in self.metadata:
            del self.metadata["expire_message"]

        return self

    async def clear_messages(self) -> None:
        # <<Inherited docstring from sake.abc.MessageCache>>
        await self.get_connection(ResourceIndex.INVITE).flushdb()

    async def delete_message(self, message_id: hikari.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.abc.MessageCache>>
        await self.get_connection(ResourceIndex.MESSAGE).delete(str(message_id))

    async def get_message(self, message_id: hikari.Snowflakeish, /) -> hikari.Message:
        # <<Inherited docstring from sake.abc.MessageCache>>
        if payload := await self.get_connection(ResourceIndex.MESSAGE).get(str(message_id)):
            return self.rest.entity_factory.deserialize_message(self.load(payload))

        raise errors.EntryNotFound("Message not found")

    def iter_messages(self, *, window_size: int = WINDOW_SIZE) -> sake_abc.CacheIterator[hikari.Message]:
        # <<Inherited docstring from sake.abc.MessageCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.MESSAGE, self.rest.entity_factory.deserialize_message, window_size=window_size
        )

    async def set_message(self, payload: ObjectT, /, *, expire_time: typing.Optional[utility.ExpireT] = None) -> None:
        # <<Inherited docstring from sake.abc.MessageCache>>
        if expire_time is None:
            expire_time = int(self.metadata.get("expire_message", DEFAULT_FAST_EXPIRE))

        else:
            expire_time = utility.convert_expire_time(expire_time)

        await self.get_connection(ResourceIndex.MESSAGE).set(str(payload["id"]), self.dump(payload), px=expire_time)
        await self._try_set_user(payload["author"])

    async def update_message(
        self, payload: ObjectT, /, *, expire_time: typing.Optional[utility.ExpireT] = None
    ) -> bool:
        # <<Inherited docstring from sake.abc.MessageCache>>
        # This is a special case method for handling the partial message updates we get
        if full_message := await self.get_connection(ResourceIndex.MESSAGE).get(str(int(payload["id"]))):
            # TODO: do we need to unset fields?
            full_message = self.load(full_message)
            full_message.update(payload)
            await self.set_message(full_message, expire_time=expire_time)
            return True

        return False


class PresenceCache(ResourceClient, sake_abc.PresenceCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        return (ResourceIndex.PRESENCE,)

    @classmethod
    def intents(cls) -> hikari.Intents:
        # <<Inherited docstring from ResourceClient>>
        return hikari.Intents.GUILDS | hikari.Intents.GUILD_PRESENCES

    async def __bulk_add_presences(self, presences: typing.Iterator[ObjectT], /, guild_id: int) -> None:
        str_guild_id = str(guild_id)
        client = self.get_connection(ResourceIndex.PRESENCE)
        windows = redis_iterators.chunk_values(_add_guild_id(payload, str_guild_id) for payload in presences)
        setters = (
            client.hset(str_guild_id, mapping=_to_map(window, _get_sub_user_id, self.dump)) for window in windows
        )
        await asyncio.gather(*setters)

    @utility.as_raw_listener("GUILD_CREATE")  # Presences is not included on GUILD_UPDATE
    async def __on_guild_create(self, event: hikari.ShardPayloadEvent, /) -> None:
        await self.__bulk_add_presences(event.payload["presences"], int(event.payload["id"]))

    @utility.as_listener(hikari.GuildLeaveEvent)
    async def __on_guild_leave_event(self, event: hikari.GuildLeaveEvent, /) -> None:
        await self.clear_presences_for_guild(event.guild_id)

    @utility.as_raw_listener("GUILD_MEMBERS_CHUNK")
    async def __on_guild_members_chunk(self, event: hikari.ShardPayloadEvent, /) -> None:
        if presences := event.payload.get("presences"):
            await self.__bulk_add_presences(presences, int(event.payload["guild_id"]))

    @utility.as_raw_listener("PRESENCE_UPDATE")
    async def __on_presence_update_event(self, event: hikari.ShardPayloadEvent, /) -> None:
        if event.payload["status"] == hikari.Status.OFFLINE:
            await self.delete_presence(int(event.payload["guild_id"]), int(event.payload["user"]["id"]))

        else:
            await self.set_presence(dict(event.payload))

    async def clear_presences(self) -> None:
        # <<Inherited docstring from sake.abc.PresenceCache>>
        await self.get_connection(ResourceIndex.PRESENCE).flushdb()

    async def clear_presences_for_guild(self, guild_id: hikari.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.abc.PresenceCache>>
        await self.get_connection(ResourceIndex.PRESENCE).delete(str(guild_id))

    async def delete_presence(self, guild_id: hikari.Snowflakeish, user_id: hikari.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.abc.PresenceCache>>
        await self.get_connection(ResourceIndex.PRESENCE).hdel(str(guild_id), str(user_id))

    async def get_presence(
        self, guild_id: hikari.Snowflakeish, user_id: hikari.Snowflakeish, /
    ) -> hikari.MemberPresence:
        # <<Inherited docstring from sake.abc.PresenceCache>>
        if payload := await self.get_connection(ResourceIndex.PRESENCE).hget(str(guild_id), str(user_id)):
            return self.rest.entity_factory.deserialize_member_presence(self.load(payload))

        raise errors.EntryNotFound("Presence not found")

    def iter_presences(self, *, window_size: int = WINDOW_SIZE) -> sake_abc.CacheIterator[hikari.MemberPresence]:
        # <<Inherited docstring from sake.abc.PresenceCache>>
        return redis_iterators.MultiMapIterator(
            self, ResourceIndex.PRESENCE, self.rest.entity_factory.deserialize_member_presence, window_size=window_size
        )

    def iter_presences_for_guild(
        self, guild_id: hikari.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.MemberPresence]:
        # <<Inherited docstring from sake.abc.PresenceCache>>
        return redis_iterators.SpecificMapIterator(
            self,
            str(guild_id),
            ResourceIndex.PRESENCE,
            self.rest.entity_factory.deserialize_member_presence,
            window_size=window_size,
        )

    async def set_presence(self, payload: ObjectT, /) -> None:
        # <<Inherited docstring from sake.abc.PresenceCache>>
        client = self.get_connection(ResourceIndex.PRESENCE)
        await client.hset(str(int(payload["guild_id"])), str(int(payload["user"]["id"])), self.dump(payload))


class RoleCache(_Reference, sake_abc.RoleCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        return (ResourceIndex.ROLE,)

    @classmethod
    def intents(cls) -> hikari.Intents:
        # <<Inherited docstring from ResourceClient>>
        return hikari.Intents.GUILDS

    @utility.as_raw_listener("GUILD_CREATE", "GUILD_UPDATE")
    async def __on_guild_create_update(self, event: hikari.ShardPayloadEvent, /) -> None:
        guild_id = int(event.payload["id"])
        str_guild_id = str(guild_id)
        await self.clear_roles_for_guild(guild_id)
        roles = event.payload["roles"]
        windows = redis_iterators.chunk_values(_add_guild_id(payload, str_guild_id) for payload in roles)
        client = self.get_connection(ResourceIndex.ROLE)
        setters = (client.mset(_to_map(window, _get_id, self.dump)) for window in windows)
        id_setter = self._add_ids(
            ResourceIndex.GUILD, str_guild_id, ResourceIndex.ROLE, *(int(payload["id"]) for payload in roles)
        )
        await asyncio.gather(*setters, id_setter)

    @utility.as_listener(hikari.GuildLeaveEvent)
    async def __on_guild_leave_event(self, event: hikari.GuildLeaveEvent, /) -> None:
        await self.clear_roles_for_guild(event.guild_id)

    @utility.as_raw_listener("GUILD_ROLE_CREATE", "GUILD_ROLE_UPDATE")
    async def __on_guild_role_create_update(self, event: hikari.ShardPayloadEvent, /) -> None:
        await self.set_role(int(event.payload["guild_id"]), event.payload["role"])

    @utility.as_listener(hikari.RoleDeleteEvent)
    async def __on_guild_role_delete_event(self, event: hikari.RoleDeleteEvent, /) -> None:
        await self.delete_role(event.role_id, guild_id=event.guild_id)

    async def clear_roles(self) -> None:
        # <<Inherited docstring from sake.abc.RoleCache>>
        references = await self._dump_relationship(ResourceIndex.GUILD, ResourceIndex.ROLE)
        client = self.get_connection(ResourceIndex.ROLE)
        await asyncio.gather(
            *itertools.starmap(client.delete, redis_iterators.chunk_values(itertools.chain(references.values())))
        )

    async def clear_roles_for_guild(self, guild_id: hikari.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.abc.RoleCache>>
        str_guild_id = str(guild_id)
        role_ids = await self._get_ids(ResourceIndex.GUILD, str_guild_id, ResourceIndex.ROLE, cast=bytes)
        if not role_ids:
            return

        await self._delete_ids(ResourceIndex.GUILD, str_guild_id, ResourceIndex.ROLE, *role_ids)
        client = self.get_connection(ResourceIndex.ROLE)
        # Gather is awaited here to ensure that when internally bulk setting entries after triggering a bulk deletion
        # (based on events) we don't risk deleting entries after we've re-added them.
        await asyncio.gather(*itertools.starmap(client.delete, redis_iterators.chunk_values(role_ids)))

    async def delete_role(
        self, role_id: hikari.Snowflakeish, /, *, guild_id: typing.Optional[hikari.Snowflakeish] = None
    ) -> None:
        # <<Inherited docstring from sake.abc.RoleCache>>
        str_role_id = str(role_id)
        if guild_id is None:
            payload = await self.get_connection(ResourceIndex.ROLE).get(str_role_id)
            if not payload:
                return

            guild_id = int(self.load(payload)["guild_id"])

        client = self.get_connection(ResourceIndex.ROLE)
        await self._delete_ids(ResourceIndex.GUILD, str(guild_id), ResourceIndex.ROLE, str_role_id)
        await client.delete(str_role_id)

    async def get_role(self, role_id: hikari.Snowflakeish, /) -> hikari.Role:
        # <<Inherited docstring from sake.abc.RoleCache>>
        if payload := await self.get_connection(ResourceIndex.ROLE).get(str(role_id)):
            return self.__deserialize_role(self.load(payload))

        raise errors.EntryNotFound("Role not found")

    def __deserialize_role(self, payload: ObjectT, /) -> hikari.Role:
        guild_id = hikari.Snowflake(payload["guild_id"])
        return self.rest.entity_factory.deserialize_role(payload, guild_id=guild_id)

    def iter_roles(self, *, window_size: int = WINDOW_SIZE) -> sake_abc.CacheIterator[hikari.Role]:
        # <<Inherited docstring from sake.abc.RoleCache>>
        return redis_iterators.Iterator(self, ResourceIndex.ROLE, self.__deserialize_role, window_size=window_size)

    def iter_roles_for_guild(
        self, guild_id: hikari.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.Role]:
        # <<Inherited docstring from sake.abc.RoleCache>>
        key = self._generate_reference_key(ResourceIndex.GUILD, str(guild_id), ResourceIndex.ROLE)
        return redis_iterators.ReferenceIterator(
            self, key, ResourceIndex.ROLE, self.__deserialize_role, window_size=window_size
        )

    async def set_role(self, guild_id: hikari.Snowflakeish, payload: ObjectT, /) -> None:
        # <<Inherited docstring from sake.abc.RoleCache>>
        str_guild_id = str(guild_id)
        role_id = str(int(payload["id"]))
        payload["guild_id"] = str_guild_id
        await self.get_connection(ResourceIndex.ROLE).set(role_id, self.dump(payload))
        await self._add_ids(ResourceIndex.GUILD, str_guild_id, ResourceIndex.ROLE, role_id)


def _add_voice_fields(payload: ObjectT, /, guild_id: str, member: ObjectT) -> ObjectT:
    payload["guild_id"] = guild_id
    payload["member"] = member
    return payload


def _get_user_id(payload: ObjectT, /) -> str:
    return str(int(payload["user_id"]))


class VoiceStateCache(_Reference, sake_abc.VoiceStateCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from sake.abc.Resource>>
        return (ResourceIndex.VOICE_STATE,)

    @classmethod
    def intents(cls) -> hikari.Intents:
        # <<Inherited docstring from ResourceClient>>
        return hikari.Intents.GUILDS | hikari.Intents.GUILD_VOICE_STATES

    @utility.as_listener(hikari.GuildChannelDeleteEvent)
    async def __on_channel_delete_event(self, event: hikari.GuildChannelDeleteEvent, /) -> None:
        await self.clear_voice_states_for_channel(event.channel_id)

    @staticmethod
    def __generate_references(
        voice_states: typing.Iterable[ObjectT], /, *, guild_id: typing.Optional[str] = None
    ) -> typing.Dict[str, typing.Set[str]]:
        all_references: typing.Dict[str, typing.Set[str]] = {}
        for payload in voice_states:
            channel_id = int(payload["channel_id"])
            user_id = int(payload["user_id"])
            try:
                references = all_references[str(channel_id)]

            except KeyError:
                references = all_references[str(channel_id)] = set[str]()

            if guild_id is not None:
                references.add(redis_iterators.HashReferenceIterator.hash_key(guild_id))

            references.add(str(user_id))

        return all_references

    @utility.as_raw_listener("GUILD_CREATE")  # voice states aren't included in GUILD_UPDATE
    async def __on_guild_create(self, event: hikari.ShardPayloadEvent, /) -> None:
        client = self.get_connection(ResourceIndex.VOICE_STATE)
        guild_id = int(event.payload["id"])
        str_guild_id = str(guild_id)
        await self.clear_voice_states_for_guild(guild_id)

        voice_states = event.payload["voice_states"]
        members = {int(payload["user"]["id"]): payload for payload in event.payload["members"]}

        windows = redis_iterators.chunk_values(
            _add_voice_fields(payload, str_guild_id, members[int(payload["user_id"])]) for payload in voice_states
        )
        setters = (client.hset(str_guild_id, mapping=_to_map(window, _get_user_id, self.dump)) for window in windows)

        references = self.__generate_references(voice_states, guild_id=str_guild_id)
        reference_setters = (
            self._add_ids(ResourceIndex.CHANNEL, channel_id, ResourceIndex.VOICE_STATE, *state_ids)
            for channel_id, state_ids in references.items()
            if state_ids
        )
        await asyncio.gather(*setters, *reference_setters)

    @utility.as_listener(hikari.GuildLeaveEvent)  # TODO: should this also clear when it goes unavailable?
    async def __on_guild_leave_event(self, event: hikari.GuildLeaveEvent, /) -> None:
        await self.clear_voice_states_for_guild(event.guild_id)

    @utility.as_raw_listener("VOICE_STATE_UPDATE")
    async def __on_voice_state_update(self, event: hikari.ShardPayloadEvent, /) -> None:
        guild_id = int(event.payload["guild_id"])
        if event.payload.get("channel_id") is None:
            await self.delete_voice_state(guild_id, int(event.payload["user_id"]))
        else:
            await self.set_voice_state(guild_id, dict(event.payload))

    @staticmethod
    def __pop_reference(keys: typing.List[bytes], /) -> typing.Tuple[bytes, typing.Sequence[bytes]]:
        for key in keys:
            if key.startswith(b"KEY."):
                keys.remove(key)
                return key[4:], keys

        raise ValueError("Couldn't find reference key")

    async def clear_voice_states(self) -> None:
        # <<Inherited docstring from sake.abc.VoiceStateCache>>
        references = await self._dump_relationship(ResourceIndex.CHANNEL, ResourceIndex.VOICE_STATE)
        client = self.get_connection(ResourceIndex.VOICE_STATE)
        await asyncio.gather(
            *(client.hdel(key, *values) for key, values in map(self.__pop_reference, references.values()) if values)
        )

    async def clear_voice_states_for_guild(self, guild_id: hikari.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.abc.VoiceStateCache>>
        str_guild_id = str(guild_id)
        client = self.get_connection(ResourceIndex.VOICE_STATE)
        states = list(
            itertools.chain.from_iterable([v async for v in redis_iterators.iter_hash_values(client, str_guild_id)])
        )
        references = self.__generate_references(states)
        id_deleters = (
            self._delete_ids(ResourceIndex.CHANNEL, key, ResourceIndex.VOICE_STATE, *values, reference_key=True)
            for key, values in references.items()
            if values
        )
        entry_deleters = (
            client.hdel(str_guild_id, *values)
            for values in redis_iterators.chunk_values(str(int(state["user_id"])) for state in states)
        )
        # Gather is awaited here to ensure that when internally bulk setting entries after triggering a bulk deletion
        # (based on events) we don't risk deleting entries after we've re-added them.
        await asyncio.gather(*id_deleters, *entry_deleters)

    async def clear_voice_states_for_channel(
        self, channel_id: hikari.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> None:
        # <<Inherited docstring from sake.abc.VoiceStateCache>>
        str_channel_id = str(channel_id)
        ids = await self._get_ids(ResourceIndex.CHANNEL, str_channel_id, ResourceIndex.VOICE_STATE, cast=bytes)
        if not ids:
            return

        await self._delete_ids(
            ResourceIndex.CHANNEL, str_channel_id, ResourceIndex.VOICE_STATE, *ids, reference_key=True
        )
        client = self.get_connection(ResourceIndex.VOICE_STATE)
        await asyncio.gather(
            *itertools.starmap(client.delete, redis_iterators.chunk_values(ids, window_size=window_size))
        )

    # We don't accept channel_id here to avoid the relationship lookup as channel_id isn't a static value and what we
    # want is the value stored rather than the current or "last" value.
    async def delete_voice_state(self, guild_id: hikari.Snowflakeish, user_id: hikari.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.abc.VoiceStateCache>>
        str_guild_id = str(guild_id)
        str_user_id = str(user_id)
        client = self.get_connection(ResourceIndex.VOICE_STATE)

        if payload := await self.get_connection(ResourceIndex.VOICE_STATE).hget(str_guild_id, str_user_id):
            await client.hdel(str_guild_id, str_user_id)
            await self._delete_ids(
                ResourceIndex.CHANNEL,
                str(int(self.load(payload)["channel_id"])),
                ResourceIndex.VOICE_STATE,
                str_user_id,
                reference_key=True,
            )

    async def get_voice_state(
        self, guild_id: hikari.Snowflakeish, user_id: hikari.Snowflakeish, /
    ) -> hikari.VoiceState:
        # <<Inherited docstring from sake.abc.VoiceStateCache>>
        if payload := await self.get_connection(ResourceIndex.VOICE_STATE).hget(str(guild_id), str(user_id)):
            return self.rest.entity_factory.deserialize_voice_state(self.load(payload))

        raise errors.EntryNotFound("Voice state not found")

    def iter_voice_states(self, *, window_size: int = WINDOW_SIZE) -> sake_abc.CacheIterator[hikari.VoiceState]:
        # <<Inherited docstring from sake.abc.VoiceStateCache>>
        return redis_iterators.MultiMapIterator(
            self, ResourceIndex.VOICE_STATE, self.rest.entity_factory.deserialize_voice_state, window_size=window_size
        )

    def iter_voice_states_for_channel(
        self, channel_id: hikari.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.VoiceState]:
        # <<Inherited docstring from sake.abc.VoiceStateCache>>
        key = self._generate_reference_key(ResourceIndex.CHANNEL, str(channel_id), ResourceIndex.VOICE_STATE)
        return redis_iterators.HashReferenceIterator(
            self,
            key,
            index=ResourceIndex.VOICE_STATE,
            builder=self.rest.entity_factory.deserialize_voice_state,
            window_size=window_size,
        )

    def iter_voice_states_for_guild(
        self, guild_id: hikari.Snowflakeish, /, *, window_size: int = WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.VoiceState]:
        # <<Inherited docstring from sake.abc.VoiceStateCache>>
        return redis_iterators.SpecificMapIterator(
            self,
            str(guild_id),
            ResourceIndex.VOICE_STATE,
            self.rest.entity_factory.deserialize_voice_state,
            window_size=window_size,
        )

    async def set_voice_state(self, guild_id: hikari.Snowflakeish, payload: ObjectT, /) -> None:
        # <<Inherited docstring from sake.abc.VoiceStateCache>>
        channel_id = payload.get("channel_id")
        if not channel_id:
            raise ValueError("Cannot set a voice state which isn't bound to a channel")

        channel_id = int(channel_id)
        str_guild_id = str(guild_id)
        payload["guild_id"] = str_guild_id
        user_id = int(payload["user_id"])
        # We have to ensure this is deleted first to make sure previous references are removed.
        await self.delete_voice_state(guild_id, channel_id)
        await self.get_connection(ResourceIndex.VOICE_STATE).hset(str_guild_id, user_id, self.dump(payload))
        await self._add_ids(
            ResourceIndex.CHANNEL,
            str(channel_id),
            ResourceIndex.VOICE_STATE,
            user_id,
            redis_iterators.HashReferenceIterator.hash_key(str_guild_id),
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
    sake_abc.Cache,
):
    """A Redis implementation of all the defined cache resources."""

    __slots__: typing.Sequence[str] = ()
