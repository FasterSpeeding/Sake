# -*- coding: utf-8 -*-
# cython: language_level=3
# BSD 3-Clause License
#
# Copyright (c) 2020-2022, Faster Speeding
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
    "EmojiCache",
    "GuildCache",
    "GuildChannelCache",
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
    import types

    import tanjun

_ObjectT = typing.Dict[str, typing.Any]
_RedisValueT = typing.Union[int, str, bytes]
_ResourceT = typing.TypeVar("_ResourceT", bound="ResourceClient")

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
    # REFERENCE is a special case database solely used for linking other entries to other master entities.
    MESSAGE = 10


_T = typing.TypeVar("_T")
_KeyT = typing.TypeVar("_KeyT")
_ValueT = typing.TypeVar("_ValueT")


def _to_map(
    iterator: typing.Iterable[_T], key_cast: typing.Callable[[_T], _KeyT], value_cast: typing.Callable[[_T], _ValueT]
) -> dict[_KeyT, _ValueT]:
    return {key_cast(entry): value_cast(entry) for entry in iterator}


_TanjunLoaderSigT = typing.TypeVar(
    "_TanjunLoaderSigT",
    bound="typing.Callable[[typing.Any, tanjun.InjectorClient, typing.Set[typing.Type[sake_abc.Resource]]], None]",
)


@typing.runtime_checkable
class _TanjunLoader(typing.Protocol):
    def __call__(
        self, client: tanjun.InjectorClient, trust_get_for: typing.Set[typing.Type[sake_abc.Resource]], /
    ) -> None:
        raise NotImplementedError

    @property
    def __tanjun_loader__(self) -> typing.Literal[True]:
        raise NotImplementedError


def _as_tanjun_loader(callback: _TanjunLoaderSigT, /) -> _TanjunLoaderSigT:
    callback.__tanjun_loader__ = True
    assert isinstance(callback, _TanjunLoader)
    return typing.cast(_TanjunLoaderSigT, callback)


# TODO: document that it isn't guaranteed that deletion will be finished before clear command coroutines finish.
# TODO: may go back to approach where client logic and interface are separate classes
class ResourceClient(sake_abc.Resource, abc.ABC):
    """A base client which all resources in this implementation will implement.

    .. note::
        This cannot be initialised by itself and is useless alone.

    Parameters
    ----------
    app : hikari.traits.RESTAware
        The Hikari client all the models returned by this client should be
        bound to.
    address : str
        The address to use to connect to the Redis backend server this
        resource is linked to.

        E.g:
        - `"redis://[[username]:[password]]@localhost:6379"`
        - `"rediss://[[username]:[password]]@localhost:6379"`
        - `"unix://[[username]:[password]]@/path/to/socket.sock"`

        Three URL schemes are supported:
        - `redis://`: creates a TCP socket connection. See more at:
          <https://www.iana.org/assignments/uri-schemes/prov/redis>
        - `rediss://`: creates a SSL wrapped TCP socket connection. See more at:
          <https://www.iana.org/assignments/uri-schemes/prov/rediss>
        - `unix://`: creates a Unix Domain Socket connection.

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
        "__app",
        "__clients",
        "__config",
        "__default_expire",
        "__dump",
        "__event_manager",
        "__index_overrides",
        "__listeners",
        "__load",
        "__max_connections_per_db",
        "__password",
        "__raw_listeners",
        "__started",
    )

    def __init__(
        self,
        address: str,
        app: traits.RESTAware,
        event_manager: typing.Optional[hikari.api.EventManager] = None,
        *,
        config: typing.Optional[typing.MutableMapping[str, typing.Any]] = None,
        default_expire: utility.ExpireT = DEFAULT_SLOW_EXPIRE,
        event_managed: bool = False,
        password: typing.Optional[str] = None,
        max_connections_per_db: int = 5,
        dumps: typing.Callable[[_ObjectT], bytes] = lambda obj: json.dumps(obj).encode(),
        loads: typing.Callable[[bytes], _ObjectT] = json.loads,
    ) -> None:
        self.__address = address
        self.__app = app
        self.__clients: typing.Dict[int, aioredis.Redis] = {}
        self.__config = config or {}
        self.__default_expire = utility.convert_expire_time(default_expire)
        self.__dump = dumps
        self.__event_manager = event_manager
        self.__index_overrides: typing.Dict[ResourceIndex, int] = {}
        self.__listeners, self.__raw_listeners = utility.find_listeners(self)
        self.__load = loads
        self.__max_connections_per_db = max_connections_per_db
        self.__password = password
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
    def app(self) -> traits.RESTAware:
        """The Hikari client this resource client is tied to.

        This is used to build models with a `app` attribute.
        """
        return self.__app

    @property
    def config(self) -> typing.MutableMapping[str, typing.Any]:
        """This client's settings."""
        return self.__config

    @property
    def default_expire(self) -> typing.Optional[int]:
        """The default expire time used for fields with no actual lifetime.

        If this is `None` then these cases will have no set expire after.
        """
        return self.__default_expire  # TODO: , pexpire=self.default_expire

    @property
    def event_manager(self) -> typing.Optional[hikari.api.EventManager]:
        """The event manager this resource client is using for managing state."""
        return self.__event_manager

    @classmethod
    @abc.abstractmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        """The index for the resource which this class is linked to.

        .. note::
            This should be called on specific base classes and will not be
            accurate after inheritance.

        .. warning::
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

        .. note::
            This should be called on specific base classes and will not be
            accurate after inheritance.

        Returns
        -------
        hikari.intents.Intents
            The intents the resource requires to function properly.
        """
        return hikari.Intents.NONE

    @property
    def is_alive(self) -> bool:
        # <<Inherited docstring from sake.abc.Resource>>
        return self.__started

    def all_indexes(self) -> typing.MutableSet[typing.Union[ResourceIndex, int]]:
        """Get a set of all the Redis client indexes this is using.

        .. note::
            This accounts for index overrides.

        Returns
        -------
        typing.MutableSet[typing.Union[ResourceIndex, int]]]
            A set of all the Redis client indexes this is using.
        """
        results: typing.Set[int] = set()
        for sub_class in type(self).mro():
            if issubclass(sub_class, ResourceClient):
                results.update((self.__index_overrides.get(index, index) for index in sub_class.index()))

        return results

    @classmethod
    def all_intents(cls) -> hikari.Intents:
        """The intents required for a client to be sufficient event managed.

        If not all these intents are present in the linked event manager
        then this client won't be able to reliably fill and manage the
        linked redis database(s).
        """
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

    def add_to_tanjun(
        self,
        client: tanjun.abc.Client,
        /,
        *,
        trust_get_for: typing.Optional[typing.Collection[typing.Type[sake_abc.Resource]]] = None,
        tanjun_managed: bool = False,
    ) -> None:
        """Add this Redis client to a Tanjun client.

        This method will register type dependencies for the resources
        implemented by it (including `tanjun.dependencies.async_cache` compatible
        adapters which will allow Tanjun extensions and standard utilitiy such as
        converters to be aware of this cache).

        The type dependencies this will register depend on which resources are
        implemented but are as follows for each standard resource implementation:

        * `EmojiCache`
            - `sake.abc.EmojiCache`
            - `sake.abc.RefEmojiCache`
            - `sake.redis.EmojiCache`
            - `tanjun.dependencies.async_cache.SfCache[hikari.KnownCustomEmoji]`
            - `tanjun.dependencies.async_cache.SfGuildBound[hikari.KnownCustomEmoji]`
        * `GuildCache`
            - `sake.abc.GuildCache`
            - `sake.redis.RedisCache`
            - `tanjun.dependencies.async_cache.SfCache[hikari.Guild]`
            - `tanjun.depepdencies.async_cache.SfCache[hikari.GatewayGuild]`
        * `GuildChannelCache`
            - `sake.abc.GuildChannelCache`
            - `sake.abc.RefGuildChannelCache`
            - `sake.redis.GuildChannelCache`
            - `tanjun.dependencies.async_cache.SfCache[hikari.GuildChannel]`
            - `tanjun.dependencies.async_cache.SfGuildBound[hikari.GuildChannel]`
        * `InviteCache`
            - `sake.abc.InviteCache`
            - `sake.redis.InviteCache`
            - `tanjun.dependencies.async_cache.SfCache[hikari.Invite]`
            - `tanjun.dependencies.async_cache.SfCache[hikari.InviteWithMetadata]`
        * `MemberCache`
            - `sake.abc.MemberCache`
            - `sake.redis.MemberCache`
            - `tanjun.dependencies.async_cache.SfGuildBound[hikari.Member]`
        * `MessageCache`
            - `sake.abc.MessageCache`
            - `sake.redis.MessageCache`
            - `tanjun.dependencies.async_cache.SfCache[hikari.Message]`
        * `PresenceCache`
            - `sake.abc.PresenceCache`
            - `sake.redis.PresenceCache`
            - `tanjun.dependencies.async_cache.SfGuildBound[hikari.MemberPresence]`
        * `RoleCache`
            - `sake.abc.RoleCache`
            - `sake.redis.RoleCache`
            - `tanjun.dependencies.async_cache.SfCache[hikari.Role]`
            - `tanjun.dependencies.async_cache.SfGuildBound[hikari.Role]`
        * `UserCache`
            - `sake.abc.MeCache`
            - `sake.abc.UserCache`
            - `sake.redis.UserCache`
            - `tanjun.dependencies.async_cache.SingleStoreCache[hikari.OwnUser]`
            - `tanjun.dependencies.async_cache.SfCache[hikari.User]`
        * `VoiceStateCache`
            - `sake.abc.VoiceStateCache`
            - `sake.redis.VoiceStateCache`
            - `tanjun.dependencies.async_cache.SfGuildBound[hikari.VoiceState]`
        * `RedisCache`
            - All of the previously listed types
            - `sake.abc.Cache`
            - `sake.redis.RedisCache`

        Parameters
        ----------
        client : tanjun.abc.Client
            The Tanjun client to add this client to.
        trust_get_for : typing.Optional[typing.Collection[type[sake_abc.Resource]]]
            A collection of resource types which the `tanjun.dependencies.async_cache`
            adapter "get" methods should raise `tanjun.dependencies.async_cache.EntryNotFound`
            if the entry isn't found rather than just `tanjun.dependencies.async_cache.CacheMissError`.

            .. note::
                `EntryNotFound` is a specialisation of `CacheMissError` which indicates
                that the entry doesn't exist rather than just that it wasn't cached
                and is used to avoid falling back to a REST request in places where this
                would be applicable.

            If not passed then this will default to the following resources:

            - `sake.abc.EmojiCache`
            - `sake.abc.GuildCache`
            - `sake.abc.GuildChannelCache`
            - `sake.abc.MemberCache`
            - `sake.abc.PresenceCache`
            - `sake.abc.RoleCache`
            - `sake.abc.VoiceStateCache`
            - `sake.abc.UserCache` (if `sake.abc.MemberCache` is also implemented).

        tanjun_managed : bool
            Whether the client should be started and stopped based on the tanjun
            client's lifecycle.

            This is useful if the client isn't being event managed.

            Defaults to `False`.

        Raises
        ------
        RuntimeError
            If this is called in an environment without Tanjun.
        """
        if trust_get_for is None:
            trust_get_for_: typing.Set[typing.Type[sake_abc.Resource]] = {
                sake_abc.EmojiCache,
                sake_abc.GuildCache,
                sake_abc.GuildChannelCache,
                sake_abc.MemberCache,
                sake_abc.PresenceCache,
                sake_abc.RoleCache,
                sake_abc.VoiceStateCache,
            }
            if isinstance(self, sake_abc.MemberCache):
                trust_get_for_.add(sake_abc.UserCache)

            trust_get_for = trust_get_for_

        else:
            trust_get_for = set(trust_get_for)

        try:
            import tanjun

        except ImportError as exc:
            raise RuntimeError("This can only be called in an environment with Tanjun") from exc

        # This is gonna be upgraded to the standard interface for tanjun.abc.Client
        assert isinstance(client, tanjun.InjectorClient)

        for _, member in inspect.getmembers(self):
            if isinstance(member, _TanjunLoader):
                member(client, trust_get_for)

        for cls in type(self).mro():
            if not cls.__name__.startswith("_") and issubclass(cls, sake_abc.Resource):
                client.set_type_dependency(cls, self)

        if tanjun_managed:
            (
                client.add_client_callback(tanjun.ClientCallbackNames.STARTING, self.open).add_client_callback(
                    tanjun.ClientCallbackNames.CLOSED, self.close
                )
            )

            if client.is_alive and client.loop:
                client.loop.create_task(self.open())

    def dump(self, data: _ObjectT, /) -> bytes:
        """Serialize a dict object representation into the form to be stored.

        Parameters
        ----------
        data : dict[str, typing.Any]
            The dict object to serialize.

        Returns
        -------
        bytes
            The object serialized as bytes.
        """
        return self.__dump(data)

    def load(self, data: bytes, /) -> _ObjectT:
        """Deserialize a bytes representation to a dict object.

        Parameters
        ----------
        data : dict[str, typing.Any]
            The bytes representation from the database to a dict object.

        Returns
        -------
        dict[str, typing.Any]
            The deserialized dict object.
        """
        return self.__load(data)

    def get_index_override(self, index: ResourceIndex, /) -> typing.Optional[int]:
        """Get the override set for an index.

        Parameters
        ----------
        index : ResourceIndex
            The index to get the override for.

        Returns
        -------
        typing.Optional[int]
            The found override if set, else `None`.
        """
        return self.__index_overrides.get(index)

    def with_index_override(
        self: _ResourceT, index: ResourceIndex, /, *, override: typing.Optional[int] = None
    ) -> _ResourceT:
        """Add an index override.

        Parameters
        ----------
        index : ResourceIndex
            The index to override.

        Other Parameters
        ----------------
        override : typing.Optional[int]
            The override to set.

            If this is left at `None` then any previous override is unset.
            This will decide which Redis database is targeted for a resource.
        """
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

    async def _try_bulk_set_users(self, users: typing.Iterable[_ObjectT], /) -> None:
        if not (client := self.__clients.get(ResourceIndex.USER)) or not (users := list(users)):
            return

        expire_time = int(self.config.get("expire_user", DEFAULT_EXPIRE))

        async with client.pipeline() as pipeline:
            pipeline.mset(_to_map(users, _get_id, self.dump))
            for user_id in map(_get_id, users):
                pipeline.pexpire(user_id, expire_time)

            await pipeline.execute()

    async def _try_set_user(self, payload: _ObjectT, /) -> None:
        if not (client := self.__clients.get(ResourceIndex.USER)):
            return

        expire_time = int(self.config.get("expire_user", DEFAULT_EXPIRE))
        await client.set(str(int(payload["id"])), self.dump(payload), px=expire_time)

    async def __on_shard_payload_event(self, event: hikari.ShardPayloadEvent, /) -> None:
        if listeners := self.__raw_listeners.get(event.name.upper()):
            # TODO: return_exceptions and handle each exception explicitly
            await asyncio.gather(*(listener(event) for listener in listeners))

    async def __spawn_connection(self, resource: int, /) -> None:
        # On startup this can try to open thousands of connections dependent on the scale of the bot
        # regardless of current-pipelining optimisations which isn't favourable so we have to
        # limit the connection limit using a BlockingConnectionPool.
        client = await aioredis.Redis(
            connection_pool=aioredis.BlockingConnectionPool.from_url(
                self.__address,
                db=int(resource),
                password=self.__password,
                max_connections=self.__max_connections_per_db,
            )
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
            await asyncio.gather(*(client.close() for client in clients.values()), return_exceptions=True)
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
        identifier: _RedisValueT,
        /,
        *identifiers: _RedisValueT,
    ) -> None:
        client = self.get_connection(ResourceIndex.REFERENCE)
        key = self._generate_reference_key(master, master_id, slave)
        await client.sadd(key, identifier, *identifiers)

    # To ensure at least 1 ID is always provided we have a required arg directly before the variable-length argument.
    async def _delete_ids(
        self,
        master: ResourceIndex,
        master_id: str,
        slave: ResourceIndex,
        identifier: _RedisValueT,
        /,
        *identifiers: _RedisValueT,
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
        keys: typing.List[bytes] = [k async for k in client.scan_iter(match=f"{master}:*:{slave}")]

        async with client.pipeline() as pipeline:
            for key in keys:
                pipeline.smembers(key)

            values = await pipeline.execute()
            references = {keys[index]: value for index, value in enumerate(values)}

            for key, members in references.items():
                if members:
                    pipeline.srem(key, *members)

            await pipeline.execute()

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
        client = self.get_connection(ResourceIndex.REFERENCE)
        key = self._generate_reference_key(master, master_id, slave)
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

    @_as_tanjun_loader
    def __add_to_tanjun(
        self, client: tanjun.InjectorClient, trust_get_for: typing.Set[typing.Type[sake_abc.Resource]], /
    ) -> None:
        from tanjun.dependencies import async_cache

        from . import _tanjun_adapter

        adapter = _tanjun_adapter.SingleStoreAdapter(self.get_me, trust_get=sake_abc.MeCache in trust_get_for)
        client.set_type_dependency(async_cache.SingleStoreCache[hikari.OwnUser], adapter)

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
            return self.app.entity_factory.deserialize_my_user(self.load(payload))

        raise errors.EntryNotFound("Own user not found")

    async def set_me(self, payload: _ObjectT, /) -> None:
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

    @_as_tanjun_loader
    def __add_to_tanjun(
        self, client: tanjun.InjectorClient, trust_get_for: typing.Set[typing.Type[sake_abc.Resource]], /
    ) -> None:
        from tanjun.dependencies import async_cache

        from . import _tanjun_adapter

        adapter = _tanjun_adapter.AsyncCacheAdapter(
            self.get_user, self.iter_users, trust_get=bool(trust_get_for.union((UserCache, sake_abc.UserCache)))
        )
        client.set_type_dependency(async_cache.SfCache[hikari.User], adapter)

    def with_user_expire(self: _ResourceT, expire: typing.Optional[utility.ExpireT], /) -> _ResourceT:
        """Set the default expire time for user entries added with this client.

        Parameters
        ----------
        expire : typing.Union[datetime.timedelta, int, float]
            The default expire time to add for users in this cache or `None`
            to set back to the default behaviour.
            This may either be the number of seconds as an int or float (where
            millisecond precision is supported) or a timedelta.

        Returns
        -------
        _ResourceT
            The client this is being called on to enable chained calls.
        """
        if expire is not None:
            self.config["expire_user"] = utility.convert_expire_time(expire)

        elif "expire_user" in self.config:
            del self.config["expire_user"]

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
            return self.app.entity_factory.deserialize_user(self.load(payload))

        raise errors.EntryNotFound("User not found")

    def iter_users(
        self, *, window_size: int = redis_iterators.DEFAULT_WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.User]:
        # <<Inherited docstring from sake.abc.UserCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.USER, self.app.entity_factory.deserialize_user, self.load, window_size=window_size
        )

    async def set_user(self, payload: _ObjectT, /, *, expire_time: typing.Optional[utility.ExpireT] = None) -> None:
        # <<Inherited docstring from sake.abc.UserCache>>
        client = self.get_connection(ResourceIndex.USER)
        if expire_time is None:
            expire_time = int(self.config.get("expire_user", DEFAULT_EXPIRE))

        else:
            expire_time = utility.convert_expire_time(expire_time)

        await client.set(str(int(payload["id"])), self.dump(payload), px=expire_time)


def _add_guild_id(data: _ObjectT, /, guild_id: str) -> _ObjectT:
    data["guild_id"] = guild_id
    return data


def _get_id(data: _ObjectT, /) -> str:
    return str(int(data["id"]))


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

    async def __bulk_add_emojis(self, emojis: typing.Iterable[_ObjectT], /, guild_id: int) -> None:
        client = self.get_connection(ResourceIndex.EMOJI)
        str_guild_id = str(guild_id)
        user_setter = self._try_bulk_set_users(user for payload in emojis if (user := payload.get("user")))
        reference_setter = self._add_ids(
            ResourceIndex.GUILD, str_guild_id, ResourceIndex.EMOJI, *(int(payload["id"]) for payload in emojis)
        )
        other_calls = asyncio.gather(user_setter, reference_setter)

        await client.mset(_to_map((_add_guild_id(emoji, str_guild_id) for emoji in emojis), _get_id, self.dump))
        await other_calls

    @_as_tanjun_loader
    def __add_to_tanjun(
        self, client: tanjun.InjectorClient, trust_get_for: typing.Set[typing.Type[sake_abc.Resource]], /
    ) -> None:
        from tanjun.dependencies import async_cache

        from . import _tanjun_adapter

        adapter = _tanjun_adapter.GuildAndGlobalCacheAdapter(
            self.get_emoji,
            self.iter_emojis,
            self.iter_emojis_for_guild,
            lambda guild_id, e: e.guild_id == guild_id,
            trust_get=bool(trust_get_for.union((EmojiCache, sake_abc.EmojiCache))),
        )
        client.set_type_dependency(async_cache.SfCache[hikari.KnownCustomEmoji], adapter).set_type_dependency(
            async_cache.SfGuildBound[hikari.KnownCustomEmoji], adapter
        )

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
        client = self.get_connection(ResourceIndex.EMOJI)
        if emoji_ids := await self._dump_relationship(ResourceIndex.GUILD, ResourceIndex.EMOJI):
            await client.delete(*emoji_ids)

    async def clear_emojis_for_guild(self, guild_id: hikari.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.abc.EmojiCache>>
        client = self.get_connection(ResourceIndex.EMOJI)
        str_guild_id = str(guild_id)
        emoji_ids = await self._get_ids(ResourceIndex.GUILD, str_guild_id, ResourceIndex.EMOJI, cast=bytes)
        if not emoji_ids:
            return

        await self._delete_ids(ResourceIndex.GUILD, str_guild_id, ResourceIndex.EMOJI, *emoji_ids)
        await client.delete(*emoji_ids)

    async def delete_emoji(
        self, emoji_id: hikari.Snowflakeish, /, *, guild_id: typing.Optional[hikari.Snowflakeish] = None
    ) -> None:
        # <<Inherited docstring from sake.abc.EmojiCache>>
        client = self.get_connection(ResourceIndex.EMOJI)
        str_emoji_id = str(emoji_id)
        if guild_id is None:
            payload = await client.get(str_emoji_id)
            if not payload:
                return

            guild_id = int(self.load(payload)["guild_id"])

        await self._delete_ids(ResourceIndex.GUILD, str(guild_id), ResourceIndex.EMOJI, str_emoji_id)
        await client.delete(str_emoji_id)

    async def get_emoji(self, emoji_id: hikari.Snowflakeish, /) -> hikari.KnownCustomEmoji:
        # <<Inherited docstring from sake.abc.EmojiCache>>
        if payload := await self.get_connection(ResourceIndex.EMOJI).get(str(emoji_id)):
            return self.__deserialize_known_custom_emoji(self.load(payload))

        raise errors.EntryNotFound("Emoji not found")

    def __deserialize_known_custom_emoji(self, payload: _ObjectT, /) -> hikari.KnownCustomEmoji:
        guild_id = hikari.Snowflake(payload["guild_id"])
        return self.app.entity_factory.deserialize_known_custom_emoji(payload, guild_id=guild_id)

    def iter_emojis(
        self, *, window_size: int = redis_iterators.DEFAULT_WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.KnownCustomEmoji]:
        # <<Inherited docstring from sake.abc.EmojiCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.EMOJI, self.__deserialize_known_custom_emoji, self.load, window_size=window_size
        )

    def iter_emojis_for_guild(
        self, guild_id: hikari.Snowflakeish, /, *, window_size: int = redis_iterators.DEFAULT_WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.KnownCustomEmoji]:
        # <<Inherited docstring from sake.abc.EmojiCache>>
        key = self._generate_reference_key(ResourceIndex.GUILD, str(guild_id), ResourceIndex.EMOJI)
        return redis_iterators.ReferenceIterator(
            self, key, ResourceIndex.EMOJI, self.__deserialize_known_custom_emoji, self.load, window_size=window_size
        )

    async def set_emoji(self, guild_id: hikari.Snowflakeish, payload: _ObjectT, /) -> None:
        # <<Inherited docstring from sake.abc.EmojiCache>>
        client = self.get_connection(ResourceIndex.EMOJI)
        str_guild_id = str(guild_id)
        emoji_id = str(int(payload["id"]))
        payload["guild_id"] = str_guild_id  # TODO: is this necessary
        await client.set(emoji_id, self.dump(payload))
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

    @_as_tanjun_loader
    def __add_to_tanjun(
        self, client: tanjun.InjectorClient, trust_get_for: typing.Set[typing.Type[sake_abc.Resource]], /
    ) -> None:
        from tanjun.dependencies import async_cache

        from . import _tanjun_adapter

        adapter = _tanjun_adapter.AsyncCacheAdapter(
            self.get_guild, self.iter_guilds, trust_get=bool(trust_get_for.union((GuildCache, sake_abc.GuildCache)))
        )
        client.set_type_dependency(async_cache.SfCache[hikari.Guild], adapter).set_type_dependency(
            async_cache.SfCache[hikari.GatewayGuild], adapter
        )

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

    def __deserialize_guild(self, payload: _ObjectT, /) -> hikari.GatewayGuild:
        # Hikari's deserialization logic expcets these fields to be present.
        payload["roles"] = []
        payload["emojis"] = []
        return self.app.entity_factory.deserialize_gateway_guild(payload).guild

    async def get_guild(self, guild_id: hikari.Snowflakeish, /) -> hikari.GatewayGuild:
        # <<Inherited docstring from sake.abc.GuildCache>>
        if payload := await self.get_connection(ResourceIndex.GUILD).get(str(guild_id)):
            return self.__deserialize_guild(self.load(payload))

        raise errors.EntryNotFound("Guild not found")

    def iter_guilds(
        self, *, window_size: int = redis_iterators.DEFAULT_WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.GatewayGuild]:
        # <<Inherited docstring from sake.abc.GuildCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.GUILD, self.__deserialize_guild, self.load, window_size=window_size
        )

    async def set_guild(self, payload: _ObjectT, /) -> None:
        # <<Inherited docstring from sake.abc.GuildCache>>
        client = self.get_connection(ResourceIndex.GUILD)
        # These entries are cached in separate stores.
        payload.pop("channels", None)
        payload.pop("emojis", None)
        payload.pop("members", None)
        payload.pop("presences", None)
        payload.pop("roles", None)
        payload.pop("threads", None)
        payload.pop("voice_states", None)
        await client.set(str(int(payload["id"])), self.dump(payload))


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

    @_as_tanjun_loader
    def __add_to_tanjun(
        self, client: tanjun.InjectorClient, trust_get_for: typing.Set[typing.Type[sake_abc.Resource]], /
    ) -> None:
        from tanjun.dependencies import async_cache

        from . import _tanjun_adapter

        adapter = _tanjun_adapter.GuildAndGlobalCacheAdapter(
            self.get_guild_channel,
            self.iter_guild_channels,
            self.iter_guild_channels_for_guild,
            lambda guild_id, c: c.guild_id == guild_id,
            trust_get=bool(trust_get_for.union((GuildChannelCache, sake_abc.GuildChannelCache))),
        )
        client.set_type_dependency(async_cache.SfCache[hikari.GuildChannel], adapter).set_type_dependency(
            async_cache.SfGuildBound[hikari.GuildChannel], adapter
        )

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
        client = self.get_connection(ResourceIndex.CHANNEL)
        guild_id = int(event.payload["id"])
        str_guild_id = str(guild_id)
        await self.clear_guild_channels_for_guild(guild_id)

        raw_channels = event.payload["channels"]
        coro = client.mset(
            _to_map((_add_guild_id(payload, str_guild_id) for payload in raw_channels), _get_id, self.dump)
        )
        ids = (int(payload["id"]) for payload in raw_channels)
        relationship_coro = self._add_ids(ResourceIndex.GUILD, str_guild_id, ResourceIndex.CHANNEL, *ids)
        await asyncio.gather(coro, relationship_coro)

    @utility.as_listener(hikari.GuildLeaveEvent)  # TODO: can we also use member remove events here?
    async def __on_guild_leave_event(self, event: hikari.GuildLeaveEvent, /) -> None:
        await self.clear_guild_channels_for_guild(event.guild_id)

    async def clear_guild_channels(self) -> None:
        # <<Inherited docstring from sake.abc.GuildChannelCache>>
        client = self.get_connection(ResourceIndex.CHANNEL)
        if channel_ids := await self._dump_relationship(ResourceIndex.GUILD, ResourceIndex.CHANNEL):
            await client.delete(*channel_ids)

    async def clear_guild_channels_for_guild(self, guild_id: hikari.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.abc.GuildChannelCache>>
        client = self.get_connection(ResourceIndex.CHANNEL)
        str_guild_id = str(guild_id)
        channel_ids = await self._get_ids(ResourceIndex.GUILD, str_guild_id, ResourceIndex.CHANNEL, cast=bytes)
        if not channel_ids:
            return

        await self._delete_ids(ResourceIndex.GUILD, str_guild_id, ResourceIndex.CHANNEL, *channel_ids)
        await client.delete(*channel_ids)

    async def delete_guild_channel(
        self, channel_id: hikari.Snowflakeish, /, *, guild_id: typing.Optional[hikari.Snowflakeish] = None
    ) -> None:
        # <<Inherited docstring from sake.abc.GuildChannelCache>>
        client = self.get_connection(ResourceIndex.CHANNEL)
        str_channel_id = str(channel_id)
        if guild_id is None:
            payload = await client.get(str_channel_id)
            if not payload:
                return

            guild_id = int(self.load(payload)["guild_id"])

        await self._delete_ids(ResourceIndex.GUILD, str(guild_id), ResourceIndex.CHANNEL, str_channel_id)
        await client.delete(str_channel_id)

    async def get_guild_channel(self, channel_id: hikari.Snowflakeish, /) -> hikari.GuildChannel:
        # <<Inherited docstring from sake.abc.GuildChannelCache>>
        if payload := await self.get_connection(ResourceIndex.CHANNEL).get(str(channel_id)):
            return self.__deserialize_guild_channel(self.load(payload))

        raise errors.EntryNotFound("Guild channel not found")

    def __deserialize_guild_channel(self, payload: _ObjectT, /) -> hikari.GuildChannel:
        channel = self.app.entity_factory.deserialize_channel(payload)
        assert isinstance(channel, hikari.GuildChannel)
        return channel

    def iter_guild_channels(
        self, *, window_size: int = redis_iterators.DEFAULT_WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.GuildChannel]:
        # <<Inherited docstring from sake.abc.GuildChannelCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.CHANNEL, self.__deserialize_guild_channel, self.load, window_size=window_size
        )

    def iter_guild_channels_for_guild(
        self, guild_id: hikari.Snowflakeish, /, *, window_size: int = redis_iterators.DEFAULT_WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.GuildChannel]:
        # <<Inherited docstring from sake.abc.GuildChannelCache>>
        key = self._generate_reference_key(ResourceIndex.GUILD, str(guild_id), ResourceIndex.CHANNEL)
        return redis_iterators.ReferenceIterator(
            self, key, ResourceIndex.CHANNEL, self.__deserialize_guild_channel, self.load, window_size=window_size
        )

    async def set_guild_channel(self, payload: _ObjectT, /) -> None:
        # <<Inherited docstring from sake.abc.GuildChannelCache>>
        client = self.get_connection(ResourceIndex.CHANNEL)
        str_channel_id = str(int(payload["id"]))
        guild_id = str(int(payload["guild_id"]))
        await client.set(str_channel_id, self.dump(payload))
        await self._add_ids(ResourceIndex.GUILD, guild_id, ResourceIndex.CHANNEL, str_channel_id)


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

    @_as_tanjun_loader
    def __add_to_tanjun(
        self, client: tanjun.InjectorClient, trust_get_for: typing.Set[typing.Type[sake_abc.Resource]], /
    ) -> None:
        from tanjun.dependencies import async_cache

        from . import _tanjun_adapter

        adapter = _tanjun_adapter.AsyncCacheAdapter(
            self.get_invite, self.iter_invites, trust_get=bool(trust_get_for.union((InviteCache, sake_abc.InviteCache)))
        )
        client.set_type_dependency(async_cache.SfCache[hikari.InviteWithMetadata], adapter).set_type_dependency(
            async_cache.SfCache[hikari.Invite], adapter
        )

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
        expire : typing.Union[datetime.timedelta, int, float]
            The default expire time to add for invites in this cache or `None`
            to set back to the default behaviour.
            This may either be the number of seconds as an int or float (where
            millisecond precision is supported) or a timedelta.

        Returns
        -------
        _ResourceT
            The client this is being called on to enable chained calls.
        """
        if expire is not None:
            self.config["expire_invite"] = utility.convert_expire_time(expire)

        elif "expire_invite" in self.config:
            del self.config["expire_invite"]

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
            return self.app.entity_factory.deserialize_invite_with_metadata(self.load(payload))

        raise errors.EntryNotFound("Invite not found")

    def iter_invites(
        self, *, window_size: int = redis_iterators.DEFAULT_WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.InviteWithMetadata]:
        # <<Inherited docstring from sake.abc.InviteCache>>
        return redis_iterators.Iterator(
            self,
            ResourceIndex.INVITE,
            self.app.entity_factory.deserialize_invite_with_metadata,
            self.load,
            window_size=window_size,
        )

    async def set_invite(self, payload: _ObjectT, /, *, expire_time: typing.Optional[utility.ExpireT] = None) -> None:
        # <<Inherited docstring from sake.abc.InviteCache>>
        client = self.get_connection(ResourceIndex.INVITE)
        if expire_time is None and (raw_max_age := payload.get("max_age")):
            max_age = datetime.timedelta(seconds=int(raw_max_age))
            expires_at = time.iso8601_datetime_string_to_datetime(payload["created_at"]) + max_age
            expire_time = round((expires_at - time.utc_datetime()).total_seconds() * 1000)

            # If this invite has already expired or the system clock is direly out of sync then we cannot
            # use the expire time we just calculated.
            if expire_time <= 0:
                expire_time = None

        if expire_time is None:
            expire_time = int(self.config.get("expire_invite", DEFAULT_INVITE_EXPIRE))

        else:
            expire_time = utility.convert_expire_time(expire_time)

        # Aioredis treats keys and values as type invariant so we want to ensure this is a str and not a class which
        # subclasses str.
        await client.set(str(payload["code"]), self.dump(payload), px=expire_time)

        if target_user := payload.get("target_user"):
            await self._try_set_user(target_user)

        if inviter := payload.get("inviter"):
            await self._try_set_user(inviter)


class _OwnIDStore:
    __slots__: typing.Sequence[str] = ("_event", "_is_waiting", "_lock", "_app", "value")

    KEY: typing.Final[str] = "OWN_ID"

    def __init__(self, app: traits.RESTAware, /) -> None:
        self._lock: typing.Optional[asyncio.Lock] = None
        self._is_waiting = False
        self._app = app
        self.value: typing.Optional[hikari.Snowflake] = None

    async def await_value(self) -> int:
        if self._is_waiting:
            assert self._lock
            async with self._lock:
                assert self.value is not None
                return self.value

        self._lock = asyncio.Lock()
        async with self._lock:
            user = await self._app.rest.fetch_my_user()
            self.value = user.id
            return self.value


def _get_sub_user_id(payload: _ObjectT, /) -> str:
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

    @_as_tanjun_loader
    def __add_to_tanjun(
        self, client: tanjun.InjectorClient, trust_get_for: typing.Set[typing.Type[sake_abc.Resource]], /
    ) -> None:
        from tanjun.dependencies import async_cache

        from . import _tanjun_adapter

        adapter = _tanjun_adapter.GuildBoundCacheAdapter(
            self.get_member,
            self.iter_members,
            self.iter_members_for_guild,
            trust_get=bool(trust_get_for.union((MemberCache, sake_abc.MemberCache))),
        )
        client.set_type_dependency(async_cache.SfGuildBound[hikari.Member], adapter)

    @utility.as_raw_listener("GUILD_CREATE")  # members aren't included in GUILD_UPDATE
    async def __on_guild_create(self, event: hikari.ShardPayloadEvent, /) -> None:
        guild_id = int(event.payload["id"])
        await self.clear_members_for_guild(guild_id)
        await self.__bulk_set_members(event.payload["members"], guild_id)

        if shard_aware := self.config.get("chunk_on_create"):
            assert isinstance(shard_aware, traits.ShardAware)
            include_presences = bool(self.config.get("chunk_presences", False))
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
            own_id = self.config[_OwnIDStore.KEY]
            assert isinstance(own_id, _OwnIDStore)

        except KeyError:
            own_id = self.config[_OwnIDStore.KEY] = _OwnIDStore(self.app)

        if (own_id_value := own_id.value) is None:
            own_id_value = await own_id.await_value()

        if event.user_id == own_id_value:
            await self.clear_members_for_guild(event.guild_id)
        else:
            await self.delete_member(event.guild_id, event.user_id)

    @utility.as_raw_listener("GUILD_MEMBERS_CHUNK")
    async def __on_guild_members_chunk_event(self, event: hikari.ShardPayloadEvent, /) -> None:
        await self.__bulk_set_members(event.payload["members"], int(event.payload["guild_id"]))

    async def __bulk_set_members(self, members: typing.Iterator[_ObjectT], /, guild_id: int) -> None:
        client = self.get_connection(ResourceIndex.MEMBER)
        str_guild_id = str(guild_id)
        members_map = _to_map(
            (_add_guild_id(payload, str_guild_id) for payload in members), _get_sub_user_id, self.dump
        )
        if members_map:
            setter = client.hset(str_guild_id, mapping=members_map)
            user_setter = self._try_bulk_set_users(member["user"] for member in members)
            await asyncio.gather(setter, user_setter)

    def chunk_on_guild_create(self: _ResourceT, shard_aware: typing.Optional[traits.ShardAware], /) -> _ResourceT:
        if shard_aware:
            if not self.event_manager:
                raise ValueError("An event manager-less cache instance cannot request member chunk on guild create")

            if not (shard_aware.intents & hikari.Intents.GUILD_MEMBERS):
                raise ValueError("Cannot request guild member chunks without the GUILD_MEMBERS intents declared")

            presences = (shard_aware.intents & hikari.Intents.GUILD_PRESENCES) == hikari.Intents.GUILD_PRESENCES
            self.config["chunk_on_create"] = shard_aware
            self.config["chunk_presences"] = presences and isinstance(self, sake_abc.PresenceCache)

        else:
            self.config.pop("chunk_on_create", None)

        return self

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
            return self.app.entity_factory.deserialize_member(self.load(payload))

        raise errors.EntryNotFound("Member not found")

    def iter_members(
        self, *, window_size: int = redis_iterators.DEFAULT_WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.Member]:
        # <<Inherited docstring from sake.abc.MemberCache>>
        return redis_iterators.MultiMapIterator(
            self, ResourceIndex.MEMBER, self.app.entity_factory.deserialize_member, self.load, window_size=window_size
        )

    def iter_members_for_guild(
        self, guild_id: hikari.Snowflakeish, /, *, window_size: int = redis_iterators.DEFAULT_WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.Member]:
        # <<Inherited docstring from sake.abc.MemberCache>>
        return redis_iterators.SpecificMapIterator(
            self,
            str(guild_id),
            ResourceIndex.MEMBER,
            self.app.entity_factory.deserialize_member,
            self.load,
            window_size=window_size,
        )

    async def set_member(self, guild_id: hikari.Snowflakeish, payload: _ObjectT, /) -> None:
        # <<Inherited docstring from sake.abc.MemberCache>>
        client = self.get_connection(ResourceIndex.MEMBER)
        user_data = payload["user"]
        await client.hset(str(guild_id), str(int(user_data["id"])), self.dump(payload))
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

    @_as_tanjun_loader
    def __add_to_tanjun(
        self, client: tanjun.InjectorClient, trust_get_for: typing.Set[typing.Type[sake_abc.Resource]], /
    ) -> None:
        from tanjun.dependencies import async_cache

        from . import _tanjun_adapter

        adapter = _tanjun_adapter.AsyncCacheAdapter(
            self.get_message,
            self.iter_messages,
            trust_get=bool(trust_get_for.union((MessageCache, sake_abc.MessageCache))),
        )
        client.set_type_dependency(async_cache.SfCache[hikari.Message], adapter)

    @utility.as_raw_listener("MESSAGE_CREATE")
    async def __on_message_create(self, event: hikari.ShardPayloadEvent, /) -> None:
        await self.set_message(dict(event.payload))

    @utility.as_listener(hikari.MessageDeleteEvent)
    async def __on_message_delete_event(self, event: hikari.MessageDeleteEvent, /) -> None:
        await self.delete_message(event.message_id)

    @utility.as_listener(hikari.GuildBulkMessageDeleteEvent)
    async def __on_guild_message_bulk_delete_event(self, event: hikari.GuildBulkMessageDeleteEvent, /) -> None:
        await self.get_connection(ResourceIndex.MESSAGE).delete(*map(str, event.message_ids))

    @utility.as_raw_listener("MESSAGE_UPDATE")
    async def __on_message_update(self, event: hikari.ShardPayloadEvent, /) -> None:
        await self.update_message(dict(event.payload))

    def with_message_expire(self: _ResourceT, expire: typing.Optional[utility.ExpireT], /) -> _ResourceT:
        """Set the default expire time for message entries added with this client.

        Parameters
        ----------
        expire : typing.Union[datetime.timedelta, int, float]
            The default expire time to add for messages in this cache or `None`
            to set back to the default behaviour.
            This may either be the number of seconds as an int or float (where
            millisecond precision is supported) or a timedelta.

        Returns
        -------
        _ResourceT
            The client this is being called on to enable chained calls.
        """
        if expire is not None:
            self.config["expire_message"] = utility.convert_expire_time(expire)

        elif "expire_message" in self.config:
            del self.config["expire_message"]

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
            return self.app.entity_factory.deserialize_message(self.load(payload))

        raise errors.EntryNotFound("Message not found")

    def iter_messages(
        self, *, window_size: int = redis_iterators.DEFAULT_WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.Message]:
        # <<Inherited docstring from sake.abc.MessageCache>>
        return redis_iterators.Iterator(
            self,
            ResourceIndex.MESSAGE,
            self.app.entity_factory.deserialize_message,
            self.load,
            window_size=window_size,
        )

    async def set_message(self, payload: _ObjectT, /, *, expire_time: typing.Optional[utility.ExpireT] = None) -> None:
        # <<Inherited docstring from sake.abc.MessageCache>>
        client = self.get_connection(ResourceIndex.MESSAGE)
        if expire_time is None:
            expire_time = int(self.config.get("expire_message", DEFAULT_FAST_EXPIRE))

        else:
            expire_time = utility.convert_expire_time(expire_time)

        await client.set(str(payload["id"]), self.dump(payload), px=expire_time)
        await self._try_set_user(payload["author"])

    async def update_message(
        self, payload: _ObjectT, /, *, expire_time: typing.Optional[utility.ExpireT] = None
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

    async def __bulk_add_presences(self, presences: typing.Iterator[_ObjectT], /, guild_id: int) -> None:
        client = self.get_connection(ResourceIndex.PRESENCE)
        str_guild_id = str(guild_id)
        presence_map = _to_map(
            (_add_guild_id(payload, str_guild_id) for payload in presences), _get_sub_user_id, self.dump
        )
        if presence_map:
            await client.hset(str_guild_id, mapping=presence_map)

    @_as_tanjun_loader
    def __add_to_tanjun(
        self, client: tanjun.InjectorClient, trust_get_for: typing.Set[typing.Type[sake_abc.Resource]], /
    ) -> None:
        from tanjun.dependencies import async_cache

        from . import _tanjun_adapter

        adapter = _tanjun_adapter.GuildBoundCacheAdapter(
            self.get_presence,
            self.iter_presences,
            self.iter_presences_for_guild,
            trust_get=bool(trust_get_for.union((PresenceCache, sake_abc.PresenceCache))),
        )
        client.set_type_dependency(async_cache.SfGuildBound[hikari.MemberPresence], adapter)

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
            return self.app.entity_factory.deserialize_member_presence(self.load(payload))

        raise errors.EntryNotFound("Presence not found")

    def iter_presences(
        self, *, window_size: int = redis_iterators.DEFAULT_WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.MemberPresence]:
        # <<Inherited docstring from sake.abc.PresenceCache>>
        return redis_iterators.MultiMapIterator(
            self,
            ResourceIndex.PRESENCE,
            self.app.entity_factory.deserialize_member_presence,
            self.load,
            window_size=window_size,
        )

    def iter_presences_for_guild(
        self, guild_id: hikari.Snowflakeish, /, *, window_size: int = redis_iterators.DEFAULT_WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.MemberPresence]:
        # <<Inherited docstring from sake.abc.PresenceCache>>
        return redis_iterators.SpecificMapIterator(
            self,
            str(guild_id),
            ResourceIndex.PRESENCE,
            self.app.entity_factory.deserialize_member_presence,
            self.load,
            window_size=window_size,
        )

    async def set_presence(self, payload: _ObjectT, /) -> None:
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

    @_as_tanjun_loader
    def __add_to_tanjun(
        self, client: tanjun.InjectorClient, trust_get_for: typing.Set[typing.Type[sake_abc.Resource]], /
    ) -> None:
        from tanjun.dependencies import async_cache

        from . import _tanjun_adapter

        adapter = _tanjun_adapter.GuildAndGlobalCacheAdapter(
            self.get_role,
            self.iter_roles,
            self.iter_roles_for_guild,
            lambda guild_id, r: r.guild_id == guild_id,
            trust_get=bool(trust_get_for.union((RoleCache, sake_abc.RoleCache))),
        )
        client.set_type_dependency(async_cache.SfGuildBound[hikari.Role], adapter).set_type_dependency(
            async_cache.SfCache[hikari.Role], adapter
        )

    @utility.as_raw_listener("GUILD_CREATE", "GUILD_UPDATE")
    async def __on_guild_create_update(self, event: hikari.ShardPayloadEvent, /) -> None:
        client = self.get_connection(ResourceIndex.ROLE)
        guild_id = int(event.payload["id"])
        str_guild_id = str(guild_id)
        await self.clear_roles_for_guild(guild_id)
        roles = event.payload["roles"]
        setter = client.mset(_to_map((_add_guild_id(payload, str_guild_id) for payload in roles), _get_id, self.dump))
        id_setter = self._add_ids(
            ResourceIndex.GUILD, str_guild_id, ResourceIndex.ROLE, *(int(payload["id"]) for payload in roles)
        )
        await asyncio.gather(setter, id_setter)

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
        client = self.get_connection(ResourceIndex.ROLE)
        if references := await self._dump_relationship(ResourceIndex.GUILD, ResourceIndex.ROLE):
            await client.delete(*itertools.chain.from_iterable(references.values()))

    async def clear_roles_for_guild(self, guild_id: hikari.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.abc.RoleCache>>
        client = self.get_connection(ResourceIndex.ROLE)
        str_guild_id = str(guild_id)
        role_ids = await self._get_ids(ResourceIndex.GUILD, str_guild_id, ResourceIndex.ROLE, cast=bytes)
        if not role_ids:
            return

        await self._delete_ids(ResourceIndex.GUILD, str_guild_id, ResourceIndex.ROLE, *role_ids)
        await client.delete(*role_ids)

    async def delete_role(
        self, role_id: hikari.Snowflakeish, /, *, guild_id: typing.Optional[hikari.Snowflakeish] = None
    ) -> None:
        # <<Inherited docstring from sake.abc.RoleCache>>
        client = self.get_connection(ResourceIndex.ROLE)
        str_role_id = str(role_id)
        if guild_id is None:
            payload = await client.get(str_role_id)
            if not payload:
                return

            guild_id = int(self.load(payload)["guild_id"])

        await self._delete_ids(ResourceIndex.GUILD, str(guild_id), ResourceIndex.ROLE, str_role_id)
        await client.delete(str_role_id)

    async def get_role(self, role_id: hikari.Snowflakeish, /) -> hikari.Role:
        # <<Inherited docstring from sake.abc.RoleCache>>
        if payload := await self.get_connection(ResourceIndex.ROLE).get(str(role_id)):
            return self.__deserialize_role(self.load(payload))

        raise errors.EntryNotFound("Role not found")

    def __deserialize_role(self, payload: _ObjectT, /) -> hikari.Role:
        guild_id = hikari.Snowflake(payload["guild_id"])
        return self.app.entity_factory.deserialize_role(payload, guild_id=guild_id)

    def iter_roles(
        self, *, window_size: int = redis_iterators.DEFAULT_WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.Role]:
        # <<Inherited docstring from sake.abc.RoleCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.ROLE, self.__deserialize_role, self.load, window_size=window_size
        )

    def iter_roles_for_guild(
        self, guild_id: hikari.Snowflakeish, /, *, window_size: int = redis_iterators.DEFAULT_WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.Role]:
        # <<Inherited docstring from sake.abc.RoleCache>>
        key = self._generate_reference_key(ResourceIndex.GUILD, str(guild_id), ResourceIndex.ROLE)
        return redis_iterators.ReferenceIterator(
            self, key, ResourceIndex.ROLE, self.__deserialize_role, self.load, window_size=window_size
        )

    async def set_role(self, guild_id: hikari.Snowflakeish, payload: _ObjectT, /) -> None:
        # <<Inherited docstring from sake.abc.RoleCache>>
        client = self.get_connection(ResourceIndex.ROLE)
        str_guild_id = str(guild_id)
        role_id = str(int(payload["id"]))
        payload["guild_id"] = str_guild_id
        await client.set(role_id, self.dump(payload))
        await self._add_ids(ResourceIndex.GUILD, str_guild_id, ResourceIndex.ROLE, role_id)


def _add_voice_fields(payload: _ObjectT, /, guild_id: str, member: _ObjectT) -> _ObjectT:
    payload["guild_id"] = guild_id
    payload["member"] = member
    return payload


def _get_user_id(payload: _ObjectT, /) -> str:
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
        voice_states: typing.Iterable[_ObjectT], /, *, guild_id: typing.Optional[str] = None
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

    @_as_tanjun_loader
    def __add_to_tanjun(
        self, client: tanjun.InjectorClient, trust_get_for: typing.Set[typing.Type[sake_abc.Resource]], /
    ) -> None:
        from tanjun.dependencies import async_cache

        from . import _tanjun_adapter

        adapter = _tanjun_adapter.GuildBoundCacheAdapter(
            self.get_voice_state,
            self.iter_voice_states,
            self.iter_voice_states_for_guild,
            trust_get=bool(trust_get_for.union((VoiceStateCache, sake_abc.VoiceStateCache))),
        )
        client.set_type_dependency(async_cache.SfGuildBound[hikari.VoiceState], adapter)

    @utility.as_raw_listener("GUILD_CREATE")  # voice states aren't included in GUILD_UPDATE
    async def __on_guild_create(self, event: hikari.ShardPayloadEvent, /) -> None:
        client = self.get_connection(ResourceIndex.VOICE_STATE)
        guild_id = int(event.payload["id"])
        str_guild_id = str(guild_id)
        await self.clear_voice_states_for_guild(guild_id)

        voice_states = event.payload["voice_states"]
        members = {int(payload["user"]["id"]): payload for payload in event.payload["members"]}
        voice_states_map = _to_map(
            (_add_voice_fields(payload, str_guild_id, members[int(payload["user_id"])]) for payload in voice_states),
            _get_user_id,
            self.dump,
        )
        if not voice_states_map:
            return

        setter = client.hset(str_guild_id, mapping=voice_states_map)

        references = self.__generate_references(voice_states, guild_id=str_guild_id)
        reference_setters = (
            self._add_ids(ResourceIndex.CHANNEL, channel_id, ResourceIndex.VOICE_STATE, *state_ids)
            for channel_id, state_ids in references.items()
            if state_ids
        )
        await asyncio.gather(setter, *reference_setters)

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
        client = self.get_connection(ResourceIndex.VOICE_STATE)
        references = await self._dump_relationship(ResourceIndex.CHANNEL, ResourceIndex.VOICE_STATE)
        async with client.pipeline() as pipeline:
            for key, values in map(self.__pop_reference, references.values()):
                if values:
                    pipeline.hdel(key, *values)

            await pipeline.execute()

    async def clear_voice_states_for_guild(self, guild_id: hikari.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.abc.VoiceStateCache>>
        client = self.get_connection(ResourceIndex.VOICE_STATE)
        str_guild_id = str(guild_id)
        states = [self.load(v) async for _, v in client.hscan_iter(str_guild_id)]

        if not states:
            return

        references = self.__generate_references(states)
        id_deleters = (
            self._delete_ids(ResourceIndex.CHANNEL, key, ResourceIndex.VOICE_STATE, *values, reference_key=True)
            for key, values in references.items()
            if values
        )
        entry_deleter = client.hdel(str_guild_id, *(str(int(state["user_id"])) for state in states))
        # Gather is awaited here to ensure that when internally bulk setting entries after triggering a bulk deletion
        # (based on events) we don't risk deleting entries after we've re-added them.
        await asyncio.gather(*id_deleters, entry_deleter)

    async def clear_voice_states_for_channel(self, channel_id: hikari.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.abc.VoiceStateCache>>
        client = self.get_connection(ResourceIndex.VOICE_STATE)
        str_channel_id = str(channel_id)
        ids = await self._get_ids(ResourceIndex.CHANNEL, str_channel_id, ResourceIndex.VOICE_STATE, cast=bytes)
        if not ids:
            return

        await self._delete_ids(
            ResourceIndex.CHANNEL, str_channel_id, ResourceIndex.VOICE_STATE, *ids, reference_key=True
        )
        await client.delete(*ids)

    # We don't accept channel_id here to avoid the relationship lookup as channel_id isn't a static value and what we
    # want is the value stored rather than the current or "last" value.
    async def delete_voice_state(self, guild_id: hikari.Snowflakeish, user_id: hikari.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.abc.VoiceStateCache>>
        client = self.get_connection(ResourceIndex.VOICE_STATE)
        str_guild_id = str(guild_id)
        str_user_id = str(user_id)

        if payload := await client.hget(str_guild_id, str_user_id):
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
            return self.app.entity_factory.deserialize_voice_state(self.load(payload))

        raise errors.EntryNotFound("Voice state not found")

    def iter_voice_states(
        self, *, window_size: int = redis_iterators.DEFAULT_WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.VoiceState]:
        # <<Inherited docstring from sake.abc.VoiceStateCache>>
        return redis_iterators.MultiMapIterator(
            self,
            ResourceIndex.VOICE_STATE,
            self.app.entity_factory.deserialize_voice_state,
            self.load,
            window_size=window_size,
        )

    def iter_voice_states_for_channel(
        self, channel_id: hikari.Snowflakeish, /, *, window_size: int = redis_iterators.DEFAULT_WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.VoiceState]:
        # <<Inherited docstring from sake.abc.VoiceStateCache>>
        key = self._generate_reference_key(ResourceIndex.CHANNEL, str(channel_id), ResourceIndex.VOICE_STATE)
        return redis_iterators.HashReferenceIterator(
            self,
            key,
            ResourceIndex.VOICE_STATE,
            self.app.entity_factory.deserialize_voice_state,
            self.load,
            window_size=window_size,
        )

    def iter_voice_states_for_guild(
        self, guild_id: hikari.Snowflakeish, /, *, window_size: int = redis_iterators.DEFAULT_WINDOW_SIZE
    ) -> sake_abc.CacheIterator[hikari.VoiceState]:
        # <<Inherited docstring from sake.abc.VoiceStateCache>>
        return redis_iterators.SpecificMapIterator(
            self,
            str(guild_id),
            ResourceIndex.VOICE_STATE,
            self.app.entity_factory.deserialize_voice_state,
            self.load,
            window_size=window_size,
        )

    async def set_voice_state(self, guild_id: hikari.Snowflakeish, payload: _ObjectT, /) -> None:
        # <<Inherited docstring from sake.abc.VoiceStateCache>>
        client = self.get_connection(ResourceIndex.VOICE_STATE)
        channel_id = payload.get("channel_id")
        if not channel_id:
            raise ValueError("Cannot set a voice state which isn't bound to a channel")

        channel_id = int(channel_id)
        str_guild_id = str(guild_id)
        payload["guild_id"] = str_guild_id
        user_id = int(payload["user_id"])
        # We have to ensure this is deleted first to make sure previous references are removed.
        await self.delete_voice_state(guild_id, channel_id)
        await client.hset(str_guild_id, user_id, self.dump(payload))
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
