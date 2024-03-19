# -*- coding: utf-8 -*-
# BSD 3-Clause License
#
# Copyright (c) 2020-2024, Faster Speeding
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
"""Utilities used for iteration in the redis implementation."""

from __future__ import annotations

__all__: list[str] = ["HashReferenceIterator", "Iterator", "MultiMapIterator", "SpecificMapIterator"]

import itertools
import typing

import hikari

from . import abc
from . import redis

if typing.TYPE_CHECKING:
    from collections import abc as collections

    from redis import asyncio as aioredis
    from typing_extensions import Self

    from . import _internal


_ObjectT = typing.TypeVar("_ObjectT")
_RedisKeyT = typing.Union[str, bytes]
_T = typing.TypeVar("_T")


DEFAULT_WINDOW_SIZE: typing.Final[int] = 1_000
"""The default size used for request chunking during iteration."""


def _chunk_values(
    values: collections.Iterable[_T], window_size: int = DEFAULT_WINDOW_SIZE
) -> collections.Iterator[collections.Sequence[_T]]:
    """Iterate over slices of the values in an iterator."""
    iterator = iter(values)

    while result := list(itertools.islice(iterator, window_size)):
        yield result


async def _iter_keys(  # noqa: ASYNC900 Async generator without `@asynccontextmanager` not allowed.
    client: aioredis.Redis[bytes], *, window_size: int = DEFAULT_WINDOW_SIZE, match: typing.Optional[str] = None
) -> collections.AsyncIterator[list[bytes]]:
    """Asynchronously iterate over slices of the top level keys in a redis resource."""
    cursor = 0

    while True:
        cursor, window = await client.scan(cursor=cursor, count=window_size, match=match)

        if window:
            yield window

        if not cursor:
            break


async def _iter_values(  # noqa: ASYNC900 Async generator without `@asynccontextmanager` not allowed.
    client: aioredis.Redis[bytes], *, window_size: int = DEFAULT_WINDOW_SIZE, match: typing.Optional[str] = None
) -> collections.AsyncIterator[list[typing.Optional[bytes]]]:
    """Asynchronously iterate over slices of the values in a key to string datastore."""
    async for window in _iter_keys(client, window_size=window_size, match=match):
        yield await client.mget(*window)


async def _iter_hash_values(  # noqa: ASYNC900 Async generator without `@asynccontextmanager` not allowed.
    client: aioredis.Redis[bytes],
    key: _RedisKeyT,
    *,
    window_size: int = DEFAULT_WINDOW_SIZE,
    match: typing.Optional[str] = None,
) -> collections.AsyncIterator[collections.Iterable[bytes]]:
    """Asynchronously iterate over slices of the values in a redis hash."""
    cursor = 0

    while True:
        cursor, window = await client.hscan(key, cursor=cursor, count=window_size, match=match)

        if window:
            yield window.values()

        if not cursor:
            break


async def _iter_reference_keys(  # noqa: ASYNC900 Async generator without `@asynccontextmanager` not allowed.
    get_connection: collections.Callable[[redis.ResourceIndex], aioredis.Redis[bytes]],
    key: _RedisKeyT,
    *,
    window_size: int = DEFAULT_WINDOW_SIZE,
    match: typing.Optional[str] = None,
) -> collections.AsyncIterator[list[bytes]]:
    """Asynchronously iterate over slices of the keys in a REFERENCE set."""
    reference_client = get_connection(redis.ResourceIndex.REFERENCE)
    cursor = 0

    while True:
        cursor, window = await reference_client.sscan(key, cursor=cursor, count=window_size, match=match)

        if window:
            yield window

        if not cursor:
            break


async def _iter_reference_values(  # noqa: ASYNC900 Async generator without `@asynccontextmanager` not allowed.
    client: aioredis.Redis[bytes],
    get_connection: collections.Callable[[redis.ResourceIndex], aioredis.Redis[bytes]],
    key: _RedisKeyT,
    *,
    window_size: int = DEFAULT_WINDOW_SIZE,
    match: typing.Optional[str] = None,
) -> collections.AsyncIterator[list[typing.Optional[bytes]]]:
    """Asynchronously iterate over slices of the values referenced by a REFERENCE set."""
    async for window in _iter_reference_keys(get_connection, key, window_size=window_size, match=match):
        yield await client.mget(*window)


class Iterator(abc.CacheIterator[_T]):
    """Redis DB iterator."""

    __slots__ = ("_buffer", "_builder", "_client", "_len", "_load", "_windows", "_window_size")

    def __init__(
        self,
        client: aioredis.Redis[bytes],
        builder: collections.Callable[[_ObjectT], _T],
        load: collections.Callable[[bytes], _ObjectT],
        *,
        window_size: int = DEFAULT_WINDOW_SIZE,
    ) -> None:
        """Initialise an iterator.

        Parameters
        ----------
        client
            The aioredis client this should use to get data.
        builder
            Callback used for building the values from dicts.
        load
            Callback used to parse the stored bytes into dicts.
        window_size
            How many entries should request at once.
        """
        if window_size <= 0:
            raise ValueError("Window size must be a positive integer")

        self._buffer: list[bytes] = []
        self._builder = builder
        self._client = client
        self._len: typing.Optional[int] = None
        self._load = load
        self._windows: typing.Optional[collections.AsyncIterator[list[typing.Optional[bytes]]]] = None
        self._window_size = int(window_size)

    def __aiter__(self) -> Iterator[_T]:
        return self

    async def __anext__(self) -> _T:
        if self._windows is None:
            self._windows = _iter_values(self._client, window_size=self._window_size)

        # A window might be empty due to none of the requested keys being found, hence the while not self._buffer
        while not self._buffer:
            async for window in self._windows:
                self._buffer.extend(filter(None, window))
                break

            else:
                raise StopAsyncIteration from None

        data = self._load(self._buffer.pop(0))

        try:
            return self._builder(data)

        except hikari.UnrecognisedEntityError:
            return await self.__anext__()

    async def len(self) -> int:
        # <<Inherited docstring from sake.traits.CacheIterator>>
        if self._len is None:
            self._len = await self._client.dbsize()

        return self._len


class GuildIterator(Iterator[_T], typing.Generic[_ObjectT, _T]):
    """Redis DB iterator for guilds."""

    __slots__ = ("_actual_builder", "_guild_id", "_own_id_store")

    def __init__(
        self,
        client: aioredis.Redis[bytes],
        builder: collections.Callable[[_ObjectT, hikari.Snowflake], _T],
        load: collections.Callable[[bytes], _ObjectT],
        *,
        own_id_store: _internal.OwnIDStore,
        window_size: int = DEFAULT_WINDOW_SIZE,
    ) -> None:
        self._actual_builder = builder
        self._guild_id = own_id_store.value
        self._own_id_store = own_id_store
        super().__init__(client=client, builder=self._build, load=load, window_size=window_size)

    def _build(self, data: _ObjectT, /) -> _T:
        assert self._guild_id is not None
        return self._actual_builder(data, self._guild_id)

    async def __anext__(self) -> _T:
        if self._guild_id is None:
            self._guild_id = await self._own_id_store.await_value()

        return await super().__anext__()


class ReferenceIterator(abc.CacheIterator[_T]):
    """Cache iterator of the values referenced by a set."""

    __slots__ = (
        "_buffer",
        "_client",
        "_builder",
        "_get_connection",
        "_key",
        "_len",
        "_load",
        "_windows",
        "_window_size",
    )

    def __init__(
        self,
        client: aioredis.Redis[bytes],
        get_connection: collections.Callable[[redis.ResourceIndex], aioredis.Redis[bytes]],
        key: _RedisKeyT,
        builder: collections.Callable[[_ObjectT], _T],
        load: collections.Callable[[bytes], _ObjectT],
        *,
        window_size: int = DEFAULT_WINDOW_SIZE,
    ) -> None:
        """Initialise a reference iterator.

        Parameters
        ----------
        client
            The redis client this should use to get data.
        get_connection
            Callback used to get the underlying aioredis connection of the referenced data.
        key
            Key of the reference set entry.
        builder
            Callback used for building the values from dicts.
        load
            Callback used to parse the stored bytes into dicts.
        window_size
            How many entries should request at once.
        """
        if window_size <= 0:
            raise ValueError("Window size must be a positive integer")

        self._buffer: list[bytes] = []
        self._builder = builder
        self._client = client
        self._get_connection = get_connection
        self._key = key
        self._len: typing.Optional[int] = None
        self._load = load
        self._windows: typing.Optional[collections.AsyncIterator[list[typing.Optional[bytes]]]] = None
        self._window_size = int(window_size)

    def __aiter__(self) -> ReferenceIterator[_T]:
        return self

    async def __anext__(self) -> _T:
        if not self._windows:
            self._windows = _iter_reference_values(
                self._client, self._get_connection, self._key, window_size=self._window_size
            )

        # Skip None/empty values as this indicates that the entry cannot be accessed anymore.
        while not self._buffer:
            async for window in self._windows:
                self._buffer.extend(filter(None, window))
                break

            else:
                raise StopAsyncIteration from None

        data = self._load(self._buffer.pop(0))
        try:
            return self._builder(data)

        except hikari.UnrecognisedEntityError:
            return await self.__anext__()

    async def len(self) -> int:
        # <<Inherited docstring from sake.traits.CacheIterator>>
        if self._len is None:
            self._len = await self._get_connection(redis.ResourceIndex.REFERENCE).scard(self._key)

        return self._len


class HashReferenceIterator(abc.CacheIterator[_T]):
    """Cache iterator of the hash map values referenced by a set."""

    __slots__ = (
        "_buffer",
        "_builder",
        "_client",
        "_get_connection",
        "_key",
        "_len",
        "_load",
        "_windows",
        "_window_size",
    )

    def __init__(
        self,
        client: aioredis.Redis[bytes],
        get_connection: collections.Callable[[redis.ResourceIndex], aioredis.Redis[bytes]],
        key: _RedisKeyT,
        builder: collections.Callable[[_ObjectT], _T],
        load: collections.Callable[[bytes], _ObjectT],
        *,
        window_size: int = DEFAULT_WINDOW_SIZE,
    ) -> None:
        """Initialise a hash reference iterator.

        Parameters
        ----------
        client
            The redis client this should use to get data.
        get_connection
            Callback used to get the underlying aioredis connection of the referenced data.
        key
            Key of the reference set entry.
        builder
            Callback used for building the values from dicts.
        load
            Callback used to parse the stored bytes into dicts.
        window_size
            How many entries should request at once.
        """
        if window_size <= 0:
            raise ValueError("Window size must be a positive integer")

        self._buffer: list[bytes] = []
        self._builder = builder
        self._client = client
        self._get_connection = get_connection
        self._key = key
        self._len: typing.Optional[int] = None
        self._load = load
        self._windows: typing.Optional[collections.AsyncIterator[list[typing.Optional[bytes]]]] = None
        self._window_size = int(window_size)

    @staticmethod
    def hash_key(hash_key: str) -> str:
        return f"KEY.{hash_key!s}"

    def __aiter__(self) -> HashReferenceIterator[_T]:
        return self

    async def __anext__(self) -> _T:
        if self._windows is None:
            reference_client = self._get_connection(redis.ResourceIndex.REFERENCE)
            # Should always be bytes in this context.
            keys = await reference_client.smembers(self._key)

            for key in filter(lambda k: k.startswith(b"KEY."), keys):
                hash_key = key[4:]
                keys.remove(key)
                break

            else:
                raise LookupError("Couldn't find reference key")

            windows = (
                await self._client.hmget(hash_key, *window)
                for window in _chunk_values(keys, window_size=self._window_size)
            )
            self._windows = windows

        # A window might be empty due to none of the requested keys being found, hence the while not self._buffer
        while not self._buffer:
            async for window in self._windows:
                self._buffer.extend(filter(None, window))
                break

            else:
                raise StopAsyncIteration from None

        data = self._load(self._buffer.pop(0))

        try:
            return self._builder(data)

        except hikari.UnrecognisedEntityError:
            return await self.__anext__()

    async def len(self) -> int:
        # <<Inherited docstring from sake.traits.CacheIterator>>
        if self._len is None:
            client = self._get_connection(redis.ResourceIndex.REFERENCE)
            self._len = (await client.scard(self._key)) - 1

        return self._len


class _EmptyAsyncIterator:
    __slots__ = ()

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> typing.NoReturn:
        raise StopAsyncIteration


class MultiMapIterator(abc.CacheIterator[_T]):
    """Cache iterator of the nested values of hash map entries."""

    __slots__ = ("_buffer", "_builder", "_client", "_len", "_load", "_top_level_keys", "_windows", "_window_size")

    def __init__(
        self,
        client: aioredis.Redis[bytes],
        builder: collections.Callable[[_ObjectT], _T],
        load: collections.Callable[[bytes], _ObjectT],
        *,
        window_size: int = DEFAULT_WINDOW_SIZE,
    ) -> None:
        """Initialise a map iterator.

        Parameters
        ----------
        client
            The aioredis client this should use to get data.
        builder
            Callback used for building the values from dicts.
        load
            Callback used to parse the stored bytes into dicts.
        window_size
            How many entries should request at once.
        """
        if window_size <= 0:
            raise ValueError("Window size must be a positive integer")

        self._buffer: list[bytes] = []
        self._builder = builder
        self._client = client
        self._len: typing.Optional[int] = None
        self._load = load
        self._top_level_keys: typing.Optional[collections.AsyncIterator[bytes]] = None
        self._windows: collections.AsyncIterator[collections.Iterable[bytes]] = _EmptyAsyncIterator()
        self._window_size = int(window_size)

    def __aiter__(self) -> MultiMapIterator[_T]:
        return self

    async def __anext__(self) -> _T:
        if not self._top_level_keys:
            self._top_level_keys = self._client.scan_iter()

        while not self._buffer:
            async for window in self._windows:
                self._buffer.extend(window)
                break

            else:
                async for key in self._top_level_keys:
                    assert isinstance(key, bytes)
                    self._windows = _iter_hash_values(self._client, key, window_size=self._window_size)
                    break

                else:
                    raise StopAsyncIteration from None

        data = self._load(self._buffer.pop(0))

        try:
            return self._builder(data)

        except hikari.UnrecognisedEntityError:
            return await self.__anext__()

    async def len(self) -> int:
        # <<Inherited docstring from sake.traits.CacheIterator>>
        if self._len is None:
            keys = await self._client.keys()
            self._len = sum([await self._client.hlen(key) for key in keys])
            assert self._len is not None

        return self._len


class SpecificMapIterator(abc.CacheIterator[_T]):
    """Cache iterator of a specific hash map's values."""

    __slots__ = ("_buffer", "_builder", "_client", "_key", "_len", "_load", "_windows", "_window_size")

    def __init__(
        self,
        client: aioredis.Redis[bytes],
        key: _RedisKeyT,
        builder: collections.Callable[[_ObjectT], _T],
        load: collections.Callable[[bytes], _ObjectT],
        *,
        window_size: int = DEFAULT_WINDOW_SIZE,
    ) -> None:
        """Initialise a specific map iterator.

        Parameters
        ----------
        client
            The redis client this should use to get data.
        key
            Key of hash map entry this should iterate over the values of.
        index
            Index of the Redis DB this targets.
        builder
            Callback used for building the values from dicts.
        load
            Callback used to parse the stored bytes into dicts.
        window_size
            How many entries should request at once.
        """
        if window_size <= 0:
            raise ValueError("Window size must be a positive integer")

        self._buffer: list[bytes] = []
        self._builder = builder
        self._client = client
        self._key = key
        self._len: typing.Optional[int] = None
        self._load = load
        self._windows: typing.Optional[collections.AsyncIterator[collections.Iterable[bytes]]] = None
        self._window_size = window_size

    def __aiter__(self) -> SpecificMapIterator[_T]:
        return self

    async def __anext__(self) -> _T:
        if not self._windows:
            self._windows = _iter_hash_values(self._client, self._key, window_size=self._window_size)

        while not self._buffer:
            async for window in self._windows:
                # Skip None/empty values as this indicates that the entry cannot be accessed anymore.
                self._buffer.extend(window)
                break

            else:
                raise StopAsyncIteration from None

        data = self._load(self._buffer.pop(0))

        try:
            return self._builder(data)

        except hikari.UnrecognisedEntityError:
            return await self.__anext__()

    async def len(self) -> int:
        # <<Inherited docstring from sake.traits.CacheIterator>>
        if self._len is None:
            self._len = int(await self._client.hlen(self._key))

        return self._len
