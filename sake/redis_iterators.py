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
"""Utilities used for iteration in the redis implementation."""

from __future__ import annotations

__all__: typing.Sequence[str] = [
    "Iterator",
    "HashReferenceIterator",
    "MultiMapIterator",
    "SpecificMapIterator",
]

import itertools
import typing

from . import abc
from . import redis

if typing.TYPE_CHECKING:
    import aioredis


_ObjectT = typing.TypeVar("_ObjectT")
_RedisKeyT = typing.Union[str, bytes]
_ValueT = typing.TypeVar("_ValueT")


DEFAULT_WINDOW_SIZE: typing.Final[int] = 1_000
"""The default size used for request chunking during iteraton."""


def _chunk_values(
    values: typing.Iterable[_ValueT], window_size: int = DEFAULT_WINDOW_SIZE
) -> typing.Iterator[typing.Sequence[_ValueT]]:
    """Iterate over slices of the values in an iterator."""
    iterator = iter(values)

    while result := list(itertools.islice(iterator, window_size)):
        yield result


async def _iter_keys(
    client: aioredis.Redis,
    *,
    window_size: int = DEFAULT_WINDOW_SIZE,
    match: typing.Optional[str] = None,
) -> typing.AsyncIterator[typing.List[bytes]]:
    """Asynchronously iterate over slices of the top level keys in a redis resource."""
    cursor = 0

    while True:
        cursor, window = await client.scan(cursor=cursor, count=window_size, match=match)

        if window:
            yield window

        if not cursor:
            break


async def _iter_values(
    client: aioredis.Redis,
    *,
    window_size: int = DEFAULT_WINDOW_SIZE,
    match: typing.Optional[str] = None,
) -> typing.AsyncIterator[typing.Iterator[typing.Optional[bytes]]]:
    """Asynchronously iterate over slices of the values in a key to string datastore."""
    async for window in _iter_keys(client, window_size=window_size, match=match):
        yield await client.mget(*window)


async def _iter_hash_values(
    client: aioredis.Redis,
    key: _RedisKeyT,
    *,
    window_size: int = DEFAULT_WINDOW_SIZE,
    match: typing.Optional[str] = None,
) -> typing.AsyncIterator[typing.Iterator[bytes]]:
    """Asynchronously iterate over slices of the values in a redis hash."""
    cursor = 0

    while True:
        cursor, window = await client.hscan(key, cursor=cursor, count=window_size, match=match)

        if window:
            yield window.values()

        if not cursor:
            break


async def _iter_reference_keys(
    client: redis.ResourceClient,
    key: _RedisKeyT,
    *,
    window_size: int = DEFAULT_WINDOW_SIZE,
    match: typing.Optional[str] = None,
) -> typing.AsyncIterator[typing.List[bytes]]:
    """Asynchronously iterate over slices of the keys in a REFERENCE set."""
    reference_client = client.get_connection(redis.ResourceIndex.REFERENCE)
    cursor = 0

    while True:
        cursor, window = await reference_client.sscan(key, cursor=cursor, count=window_size, match=match)

        if window:
            yield window

        if not cursor:
            break


async def _iter_reference_values(
    client: redis.ResourceClient,
    index: redis.ResourceIndex,
    key: _RedisKeyT,
    *,
    window_size: int = DEFAULT_WINDOW_SIZE,
    match: typing.Optional[str] = None,
) -> typing.AsyncIterator[typing.Iterator[typing.Optional[bytes]]]:
    """Asynchronously iterate over slices of the values referenced by a REFERENCE set."""
    connection = client.get_connection(index)
    async for window in _iter_reference_keys(client, key, window_size=window_size, match=match):
        yield await connection.mget(*window)


class Iterator(abc.CacheIterator[_ValueT]):
    __slots__: typing.Sequence[str] = (
        "_buffer",
        "_builder",
        "_client",
        "_index",
        "_len",
        "_load",
        "_windows",
        "_window_size",
    )

    def __init__(
        self,
        client: redis.ResourceClient,
        index: redis.ResourceIndex,
        builder: typing.Callable[[_ObjectT], _ValueT],
        load: typing.Callable[[bytes], _ObjectT],
        *,
        window_size: int = DEFAULT_WINDOW_SIZE,
    ) -> None:
        if window_size <= 0:
            raise ValueError("Window size must be a positive integer")

        self._buffer: typing.List[bytes] = []
        self._builder = builder
        self._client = client
        self._index = index
        self._len: typing.Optional[int] = None
        self._load = load
        self._windows: typing.Optional[typing.AsyncIterator[typing.Iterator[typing.Optional[bytes]]]] = None
        self._window_size = int(window_size)

    def __aiter__(self) -> Iterator[_ValueT]:
        return self

    async def __anext__(self) -> _ValueT:
        if self._windows is None:
            self._windows = _iter_values(self._client.get_connection(self._index), window_size=self._window_size)

        # A window might be empty due to none of the requested keys being found, hence the while not self._buffer
        while not self._buffer:
            async for window in self._windows:
                self._buffer.extend(filter(None, window))
                break

            else:
                raise StopAsyncIteration from None

        return self._builder(self._load(self._buffer.pop(0)))

    async def len(self) -> int:
        # <<Inherited docstring from sake.traits.CacheIterator>>
        if self._len is None:
            self._len = await self._client.get_connection(self._index).dbsize()

        return self._len


class ReferenceIterator(abc.CacheIterator[_ValueT]):
    __slots__ = ("_buffer", "_builder", "_client", "_index", "_key", "_len", "_load", "_windows", "_window_size")

    def __init__(
        self,
        client: redis.ResourceClient,
        key: _RedisKeyT,
        index: redis.ResourceIndex,
        builder: typing.Callable[[_ObjectT], _ValueT],
        load: typing.Callable[[bytes], _ObjectT],
        *,
        window_size: int = DEFAULT_WINDOW_SIZE,
    ) -> None:
        if window_size <= 0:
            raise ValueError("Window size must be a positive integer")

        self._buffer: typing.List[bytes] = []
        self._builder = builder
        self._client = client
        self._index = index
        self._key = key
        self._len: typing.Optional[int] = None
        self._load = load
        self._windows: typing.Optional[typing.AsyncIterator[typing.Iterator[typing.Optional[bytes]]]] = None
        self._window_size = int(window_size)

    def __aiter__(self) -> ReferenceIterator[_ValueT]:
        return self

    async def __anext__(self) -> _ValueT:
        if not self._windows:
            self._windows = _iter_reference_values(self._client, self._index, self._key, window_size=self._window_size)

        # Skip None/empty values as this indicates that the entry cannot be accessed anymore.
        while not self._buffer:
            async for window in self._windows:
                self._buffer.extend(filter(None, window))
                break

            else:
                raise StopAsyncIteration from None

        return self._builder(self._load(self._buffer.pop(0)))

    async def len(self) -> int:
        # <<Inherited docstring from sake.traits.CacheIterator>>
        if self._len is None:
            self._len = await self._client.get_connection(redis.ResourceIndex.REFERENCE).scard(self._key)

        return self._len


class HashReferenceIterator(abc.CacheIterator[_ValueT]):
    __slots__ = ("_buffer", "_builder", "_client", "_index", "_key", "_len", "_load", "_windows", "_window_size")

    def __init__(
        self,
        client: redis.ResourceClient,
        key: _RedisKeyT,
        index: redis.ResourceIndex,
        builder: typing.Callable[[_ObjectT], _ValueT],
        load: typing.Callable[[bytes], _ObjectT],
        *,
        window_size: int = DEFAULT_WINDOW_SIZE,
    ) -> None:
        if window_size <= 0:
            raise ValueError("Window size must be a positive integer")

        self._buffer: typing.List[bytes] = []
        self._builder = builder
        self._client = client
        self._index = index
        self._key = key
        self._len: typing.Optional[int] = None
        self._load = load
        self._windows: typing.Optional[typing.AsyncIterator[typing.Iterator[bytes]]] = None
        self._window_size = int(window_size)

    @staticmethod
    def hash_key(hash_key: str) -> str:
        return f"KEY.{hash_key!s}"

    def __aiter__(self) -> HashReferenceIterator[_ValueT]:
        return self

    async def __anext__(self) -> _ValueT:
        if self._windows is None:
            reference_client = self._client.get_connection(redis.ResourceIndex.REFERENCE)
            # Should always be bytes in this context.
            keys = await reference_client.smembers(self._key)

            for key in filter(lambda k: k.startswith(b"KEY."), keys):
                hash_key = key[4:]
                keys.remove(key)
                break

            else:
                raise LookupError("Couldn't find reference key")

            client = self._client.get_connection(self._index)
            windows = (
                await client.hmget(hash_key, *window) for window in _chunk_values(keys, window_size=self._window_size)
            )
            self._windows = windows

        # A window might be empty due to none of the requested keys being found, hence the while not self._buffer
        while not self._buffer:
            async for window in self._windows:
                self._buffer.extend(window)
                break

            else:
                raise StopAsyncIteration from None

        return self._builder(self._load(self._buffer.pop(0)))

    async def len(self) -> int:
        # <<Inherited docstring from sake.traits.CacheIterator>>
        if self._len is None:
            client = self._client.get_connection(redis.ResourceIndex.REFERENCE)
            self._len = (await client.scard(self._key)) - 1

        return self._len


async def _empty_async_iterator() -> typing.AsyncIterator[typing.Any]:
    if False:
        yield


class MultiMapIterator(abc.CacheIterator[_ValueT]):
    __slots__: typing.Sequence[str] = (
        "_buffer",
        "_builder",
        "_client",
        "_index",
        "_len",
        "_load",
        "_top_level_keys",
        "_windows",
        "_window_size",
    )

    def __init__(
        self,
        client: redis.ResourceClient,
        index: redis.ResourceIndex,
        builder: typing.Callable[[_ObjectT], _ValueT],
        load: typing.Callable[[bytes], _ObjectT],
        *,
        window_size: int = DEFAULT_WINDOW_SIZE,
    ) -> None:
        if window_size <= 0:
            raise ValueError("Window size must be a positive integer")

        self._buffer: typing.List[bytes] = []
        self._builder = builder
        self._client = client
        self._index = index
        self._len: typing.Optional[int] = None
        self._load = load
        self._top_level_keys: typing.Optional[typing.AsyncIterator[bytes]] = None
        self._windows: typing.AsyncIterator[typing.Iterator[bytes]] = _empty_async_iterator()
        self._window_size = int(window_size)

    def __aiter__(self) -> MultiMapIterator[_ValueT]:
        return self

    async def __anext__(self) -> _ValueT:
        client = self._client.get_connection(self._index)

        if not self._top_level_keys:
            self._top_level_keys = client.scan_iter()

        while not self._buffer:
            async for window in self._windows:
                self._buffer.extend(window)
                break

            else:
                async for key in self._top_level_keys:
                    assert isinstance(key, bytes)
                    self._windows = _iter_hash_values(client, key, window_size=self._window_size)
                    break

                else:
                    raise StopAsyncIteration from None

        return self._builder(self._load(self._buffer.pop(0)))

    async def len(self) -> int:
        # <<Inherited docstring from sake.traits.CacheIterator>>
        if self._len is None:
            client = self._client.get_connection(self._index)
            keys = await client.keys()
            self._len = sum([await client.hlen(key) for key in keys])
            assert self._len is not None

        return self._len


class SpecificMapIterator(abc.CacheIterator[_ValueT]):
    __slots__ = ("_buffer", "_builder", "_client", "_index", "_key", "_len", "_load", "_windows", "_window_size")

    def __init__(
        self,
        client: redis.ResourceClient,
        key: _RedisKeyT,
        index: redis.ResourceIndex,
        builder: typing.Callable[[_ObjectT], _ValueT],
        load: typing.Callable[[bytes], _ObjectT],
        *,
        window_size: int = DEFAULT_WINDOW_SIZE,
    ) -> None:
        if window_size <= 0:
            raise ValueError("Window size must be a positive integer")

        self._buffer: typing.List[bytes] = []
        self._builder = builder
        self._client = client
        self._index = index
        self._key = key
        self._len: typing.Optional[int] = None
        self._load = load
        self._windows: typing.Optional[typing.AsyncIterator[typing.Iterator[bytes]]] = None
        self._window_size = window_size

    def __aiter__(self) -> SpecificMapIterator[_ValueT]:
        return self

    async def __anext__(self) -> _ValueT:
        if not self._windows:
            client = self._client.get_connection(self._index)
            self._windows = _iter_hash_values(client, self._key, window_size=self._window_size)

        while not self._buffer:
            async for window in self._windows:
                # Skip None/empty values as this indicates that the entry cannot be accessed anymore.
                self._buffer.extend(window)
                break

            else:
                raise StopAsyncIteration from None

        return self._builder(self._load(self._buffer.pop(0)))

    async def len(self) -> int:
        # <<Inherited docstring from sake.traits.CacheIterator>>
        if self._len is None:
            self._len = int(await self._client.get_connection(self._index).hlen(self._key))

        return self._len
