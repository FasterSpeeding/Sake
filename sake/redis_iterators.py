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
"""Utilities used for iteration in the redis implementation."""

from __future__ import annotations

__all__: typing.Final[typing.Sequence[str]] = [
    "Iterator",
    "HashReferenceIterator",
    "MultiMapIterator",
    "SpecificMapIterator",
]

import itertools
import typing

from sake import redis
from sake import traits

if typing.TYPE_CHECKING:
    import aioredis  # type: ignore[import]
    from hikari import snowflakes


_ValueT = typing.TypeVar("_ValueT")
_OtherValueT = typing.TypeVar("_OtherValueT")
WINDOW_SIZE: typing.Final[int] = 1_000
"""The default size used for "windowed" chunking in this client."""


def chunk_values(
    values: typing.Iterable[_ValueT], window_size: int = WINDOW_SIZE
) -> typing.Iterator[typing.Sequence[_ValueT]]:
    """Iterate over slices of the values in an iterator.

    Parameters
    ----------
    values : typing.Iterable[ValueT]
        The iterator to iterate over slices of.
    window_size : int
        The maximum amount of values that should be yielded per chunk.
        Defaults to `WINDOW_SIZE`.

    Yields
    ------
    typing.Sequence[ValueT]
        Slices of the values within the provided `values` iterable.

    Raises
    ------
    ValueError
        If an invalid `window_size` is passed (e.g. negative) or if
        `values` isn't iterable.
    """
    iterator = iter(values)

    while result := list(itertools.islice(iterator, window_size)):
        yield result


async def iter_keys(
    client: aioredis.Redis, *, window_size: int = WINDOW_SIZE, match: typing.Optional[str] = None
) -> typing.AsyncIterator[typing.MutableSequence[bytes]]:
    """Asynchronously iterate over slices of the top level keys in a redis resource.

    Parameters
    ----------
    client : aioredis.Redis
        The underlying redis client for the relevant resource to iterate over.
    window_size : int
        The maximum amount of values that should be yielded per chunk.
        Defaults to `WINDOW_SIZE`.
    match : typing.Optional[str]
        A pattern to match keys by or `builtins.None` to yield all the keys.
        Defaults to `builtins.None`.
        See https://redis.io/commands/keys for more information.

    Yields
    ------
    typing.MutableSequence[bytes]
        Slices of the top level keys in a redis resource.

    Raises
    ------
    ValueError
        If any of the values provided are invalid.
    """
    cursor = 0

    while True:
        cursor, window = await client.scan(cursor, count=window_size, match=match)

        if window:
            yield window

        if not cursor:
            break


async def iter_values(
    client: aioredis.Redis, *, window_size: int = WINDOW_SIZE, match: typing.Optional[str] = None
) -> typing.AsyncIterator[typing.MutableSequence[bytes]]:
    """Asynchronously iterate over slices of the values in a key to string datastore.

    Parameters
    ----------
    client : aioredis.Redis
        The underlying redis client for the relevant resource to iterate over.
    window_size : int
        The maximum amount of values that should be yielded per chunk.
        Defaults to `WINDOW_SIZE`.
    match : typing.Optional[str]
        A pattern to match keys by or `builtins.None` to yield all the keys.
        Defaults to `builtins.None`.
        See https://redis.io/commands/keys for more information.

    Yields
    ------
    typing.MutableSequence[bytes]
        Slices of the byte values in a redis resource.

    Raises
    ------
    ValueError
        If any of the values provided are invalid.
    """
    async for window in iter_keys(client, window_size=window_size, match=match):
        yield await client.mget(*window)


async def iter_hash_keys(
    client: aioredis.Redis,
    key: redis.RedisValueT,
    *,
    window_size: int = WINDOW_SIZE,
    match: typing.Optional[str] = None,
) -> typing.AsyncIterator[typing.MutableSequence[bytes]]:
    """Asynchronously iterate over slices of the keys in a redis hash.

    Parameters
    ----------
    client : aioredis.Redis
        The underlying redis client for the relevant resource to iterate over a
        specific hash map in.
    key : sake.redis.RedisValueT
        The top level key of the hash map to iterate over it's keys.
    window_size : int
        The maximum amount of values that should be yielded per chunk.
        Defaults to `WINDOW_SIZE`.
    match : typing.Optional[str]
        A pattern to match keys by or `builtins.None` to yield all the keys.
        Defaults to `builtins.None`.
        See https://redis.io/commands/keys for more information.

    Yields
    ------
    typing.MutableSequence[bytes]
        Slices of the keys in a hash map.

    Raises
    ------
    ValueError
        If any of the values provided are invalid.
    """
    cursor = 0

    while True:
        cursor, window = await client.hscan(key, cursor, count=window_size, match=match)

        if window:
            yield [key for key, _ in window]

        if not cursor:
            break


async def iter_hash_values(
    client: aioredis.Redis,
    key: redis.RedisValueT,
    *,
    window_size: int = WINDOW_SIZE,
    match: typing.Optional[str] = None,
) -> typing.AsyncIterator[typing.MutableSequence[bytes]]:
    """Asynchronously iterate over slices of the keys in a redis hash.

    Parameters
    ----------
    client : aioredis.Redis
        The underlying redis client for the relevant resource to iterate over a
        specific hash map in.
    key : sake.redis.RedisValueT
        The top level key of the hash map to iterate over it's keys.
    window_size : int
        Defaults to `WINDOW_SIZE`.
        The maximum amount of values that should be yielded per chunk.
    match : typing.Optional[str]
        A pattern to match keys by or `builtins.None` to yield all the keys.
        Defaults to `builtins.None`.
        See https://redis.io/commands/keys for more information.

    Yields
    ------
    typing.MutableSequence[bytes]
        Slices of the byte values in a hash map.

    Raises
    ------
    ValueError
        If any of the values provided are invalid.
    """
    cursor = 0

    while True:
        cursor, window = await client.hscan(key, cursor, count=window_size, match=match)

        if window:
            yield [value for _, value in window]

        if not cursor:
            break


async def iter_reference_keys(
    resource_client: redis.ResourceClient,
    key: redis.RedisValueT,
    *,
    window_size: int = WINDOW_SIZE,
    match: typing.Optional[str] = None,
) -> typing.AsyncIterator[typing.MutableSequence[bytes]]:
    """Asynchronously iterate over slices of the keys in a REFERENCE set.

    Parameters
    ----------
    resource_client : sake.redis.ResourceClient
        The redis implementation resource client to use.
    key : sake.redis.RedisValue
        The reference resource key to get the relevant keys from.
        This defaults to `WINDOW_SIZE`.
    window_size : int
        The maximum amount of values that should be yielded per chunk.
        Defaults to `WINDOW_SIZE`.
    match : typing.Optional[str]
        A pattern to match keys by or `builtins.None` to yield all the keys.
        Defaults to `builtins.None`.
        See https://redis.io/commands/keys for more information.

    Yields
    ------
    typing.MutableSequence[bytes]
        Slices of the keys in a redis resource.

    Raises
    ------
    ValueError
        If any of the values provided are invalid.
    """
    reference_client = await resource_client.get_connection(redis.ResourceIndex.REFERENCE)
    cursor = 0

    while True:
        cursor, window = await reference_client.sscan(key, cursor, count=window_size, match=match)

        if window:
            yield window

        if not cursor:
            break


async def iter_reference_values(
    resource_client: redis.ResourceClient,
    index: redis.ResourceIndex,
    key: redis.RedisValueT,
    *,
    window_size: int = WINDOW_SIZE,
    match: typing.Optional[str] = None,
) -> typing.AsyncIterator[typing.MutableSequence[bytes]]:
    """Asynchronously iterate over slices of the values referenced by a REFERENCE set.

    Parameters
    ----------
    resource_client : sake.redis.ResourceClient
        The redis implementation resource client to use.
    index : sake.redis.ResourceIndex
        The resource to get referenced values from.
    key : sake.redis.RedisValue
        The reference resource key to get the relevant keys from.
    window_size : int
        The maximum amount of values that should be yielded per chunk.
        Defaults to `WINDOW_SIZE`.
    match : typing.Optional[str]
        A pattern to match keys by or `builtins.None` to yield all the keys.
        Defaults to `builtins.None`.
        See https://redis.io/commands/keys for more information.

    Yields
    ------
    typing.MutableSequence[bytes]
        Slices of the referenced bytes in the `index` reference store.

    Raises
    ------
    ValueError
        If any of the values provided are invalid.
    """
    client = await resource_client.get_connection(index)

    async for window in iter_reference_keys(resource_client, key, window_size=window_size, match=match):
        yield await client.mget(*window)


class Iterator(traits.CacheIterator[_ValueT]):
    __slots__: typing.Sequence[str] = ("_buffer", "_builder", "_client", "_index", "_len", "_windows", "_window_size")

    def __init__(
        self,
        client: redis.ResourceClient,
        index: redis.ResourceIndex,
        builder: typing.Callable[[bytes], _ValueT],
        *,
        window_size: int = WINDOW_SIZE,
    ) -> None:
        if window_size <= 0:
            raise ValueError("Window size must be a positive integer")

        self._buffer: typing.MutableSequence[bytes] = []
        self._builder = builder
        self._client = client
        self._index = index
        self._len: typing.Optional[int] = None
        self._windows: typing.Optional[typing.AsyncIterator[typing.Sequence[bytes]]] = None
        self._window_size = int(window_size)

    def __aiter__(self) -> Iterator[_ValueT]:
        return self

    async def __anext__(self) -> _ValueT:
        if self._windows is None:
            client = await self._client.get_connection(self._index)
            self._windows = iter_values(client, window_size=self._window_size)

        while not self._buffer:
            async for window in self._windows:
                # Skip None/empty values as this indicates that the entry cannot be accessed anymore.
                self._buffer.extend(filter(bool, window))
                break

            else:
                raise StopAsyncIteration from None

        return self._builder(self._buffer.pop(0))

    async def len(self) -> int:
        # <<Inherited docstring from sake.traits.CacheIterator>>
        if self._len is None:
            client = await self._client.get_connection(self._index)
            self._len = int(await client.dbsize())

        return self._len


class ReferenceIterator(traits.CacheIterator[_ValueT]):
    __slots__ = ("_buffer", "_builder", "_client", "_index", "_key", "_len", "_windows", "_window_size")

    def __init__(
        self,
        client: redis.ResourceClient,
        key: redis.RedisValueT,
        index: redis.ResourceIndex,
        builder: typing.Callable[[bytes], _ValueT],
        *,
        window_size: int = WINDOW_SIZE,
    ) -> None:
        if window_size <= 0:
            raise ValueError("Window size must be a positive integer")

        self._buffer: typing.MutableSequence[bytes] = []
        self._builder = builder
        self._client = client
        self._index = index
        self._key = key
        self._len: typing.Optional[int] = None
        self._windows: typing.Optional[typing.AsyncIterator[typing.Sequence[bytes]]] = None
        self._window_size = int(window_size)

    def __aiter__(self) -> ReferenceIterator[_ValueT]:
        return self

    async def __anext__(self) -> _ValueT:
        if not self._windows:
            self._windows = iter_reference_values(self._client, self._index, self._key, window_size=self._window_size)

        while not self._buffer:
            async for window in self._windows:
                # Skip None/empty values as this indicates that the entry cannot be accessed anymore.
                self._buffer.extend(filter(bool, window))
                break

            else:
                raise StopAsyncIteration from None

        return self._builder(self._buffer.pop(0))

    async def len(self) -> int:
        # <<Inherited docstring from sake.traits.CacheIterator>>
        if self._len is None:
            client = await self._client.get_connection(redis.ResourceIndex.REFERENCE)
            self._len = int(await client.scard(self._key))

        return self._len


class HashReferenceIterator(traits.CacheIterator[_ValueT]):
    __slots__ = ("_buffer", "_builder", "_client", "_index", "_key", "_len", "_windows", "_window_size")

    def __init__(
        self,
        client: redis.ResourceClient,
        key: redis.RedisValueT,
        index: redis.ResourceIndex,
        builder: typing.Callable[[bytes], _ValueT],
        *,
        window_size: int = WINDOW_SIZE,
    ) -> None:
        if window_size <= 0:
            raise ValueError("Window size must be a positive integer")

        self._buffer: typing.MutableSequence[bytes] = []
        self._builder = builder
        self._client = client
        self._index = index
        self._key = key
        self._len: typing.Optional[int] = None
        self._windows: typing.Optional[typing.AsyncIterator[typing.Sequence[bytes]]] = None
        self._window_size = int(window_size)

    @staticmethod
    def hash_key(hash_key: snowflakes.Snowflakeish) -> str:
        return f"KEY.{int(hash_key)}"

    def __aiter__(self) -> HashReferenceIterator[_ValueT]:
        return self

    async def __anext__(self) -> _ValueT:
        if self._windows is None:
            reference_client = await self._client.get_connection(redis.ResourceIndex.REFERENCE)
            keys: typing.MutableSequence[bytes] = await reference_client.smembers(self._key)

            for key in filter(lambda k: k.startswith(b"KEY."), keys):
                hash_key = key[4:]
                keys.remove(key)
                break

            else:
                raise LookupError("Couldn't find reference key")

            client = await self._client.get_connection(self._index)
            windows = (
                await client.hmget(hash_key, *window) for window in chunk_values(keys, window_size=self._window_size)
            )
            # For some reason mypy reads that window async generator as returning None
            self._windows = typing.cast("typing.AsyncIterator[typing.Sequence[bytes]]", windows)

        while not self._buffer:
            async for window in self._windows:
                # Skip None/empty values as this indicates that the entry cannot be accessed anymore.
                self._buffer.extend(filter(bool, window))
                break

            else:
                raise StopAsyncIteration from None

        return self._builder(self._buffer.pop(0))

    async def len(self) -> int:
        # <<Inherited docstring from sake.traits.CacheIterator>>
        if self._len is None:
            client = await self._client.get_connection(redis.ResourceIndex.REFERENCE)
            self._len = int(await client.scard(self._key)) - 1

        return self._len


async def _empty_async_iterator() -> typing.AsyncIterator[typing.Any]:
    if False:
        yield  # type: ignore[unreachable]


class MultiMapIterator(traits.CacheIterator[_ValueT]):
    __slots__: typing.Sequence[str] = (
        "_buffer",
        "_builder",
        "_client",
        "_index",
        "_len",
        "_top_level_keys",
        "_windows",
        "_window_size",
    )

    def __init__(
        self,
        client: redis.ResourceClient,
        index: redis.ResourceIndex,
        builder: typing.Callable[[bytes], _ValueT],
        *,
        window_size: int = WINDOW_SIZE,
    ) -> None:
        if window_size <= 0:
            raise ValueError("Window size must be a positive integer")

        self._buffer: typing.MutableSequence[bytes] = []
        self._builder = builder
        self._client = client
        self._index = index
        self._len: typing.Optional[int] = None
        self._top_level_keys: typing.Optional[typing.AsyncIterator[bytes]] = None
        self._windows: typing.AsyncIterator[typing.Sequence[bytes]] = _empty_async_iterator()
        self._window_size = int(window_size)

    def __aiter__(self) -> MultiMapIterator[_ValueT]:
        return self

    async def __anext__(self) -> _ValueT:
        client = await self._client.get_connection(self._index)

        if not self._top_level_keys:
            keys: typing.AsyncIterator[bytes] = client.iscan()
            self._top_level_keys = keys

        while not self._buffer:
            async for window in self._windows:
                # Skip None/empty values as this indicates that the entry cannot be accessed anymore.
                self._buffer.extend(filter(bool, window))
                break

            else:
                async for key in self._top_level_keys:
                    self._windows = iter_hash_values(client, key, window_size=self._window_size)
                    break

                else:
                    raise StopAsyncIteration from None

        return self._builder(self._buffer.pop(0))

    async def len(self) -> int:
        # <<Inherited docstring from sake.traits.CacheIterator>>
        if self._len is None:
            client = await self._client.get_connection(self._index)
            keys = await client.keys("*")
            # For some reason mypy thinks this is still optional without the int cast
            self._len = int(sum([int(await client.hlen(key)) for key in keys]))

        return self._len


class SpecificMapIterator(traits.CacheIterator[_ValueT]):
    __slots__ = ("_buffer", "_builder", "_client", "_index", "_key", "_len", "_windows", "_window_size")

    def __init__(
        self,
        client: redis.ResourceClient,
        key: redis.RedisValueT,
        index: redis.ResourceIndex,
        builder: typing.Callable[[bytes], _ValueT],
        *,
        window_size: int = WINDOW_SIZE,
    ) -> None:
        if window_size <= 0:
            raise ValueError("Window size must be a positive integer")

        self._buffer: typing.MutableSequence[bytes] = []
        self._builder = builder
        self._client = client
        self._index = index
        self._key = key
        self._len: typing.Optional[int] = None
        self._windows: typing.Optional[typing.AsyncIterator[typing.Sequence[bytes]]] = None
        self._window_size = window_size

    def __aiter__(self) -> SpecificMapIterator[_ValueT]:
        return self

    async def __anext__(self) -> _ValueT:
        if not self._windows:
            client = await self._client.get_connection(self._index)
            self._windows = iter_hash_values(client, self._key, window_size=self._window_size)

        while not self._buffer:
            async for window in self._windows:
                # Skip None/empty values as this indicates that the entry cannot be accessed anymore.
                self._buffer.extend(filter(bool, window))
                break

            else:
                raise StopAsyncIteration from None

        return self._builder(self._buffer.pop(0))

    async def len(self) -> int:
        # <<Inherited docstring from sake.traits.CacheIterator>>
        # TODO: override "count" method?
        if self._len is None:
            client = await self._client.get_connection(self._index)
            self._len = int(await client.hlen(self._key))

        return self._len
