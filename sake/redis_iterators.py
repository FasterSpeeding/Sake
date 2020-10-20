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
    import aioredis

    from hikari import snowflakes


KeyT = typing.TypeVar("KeyT")
ValueT = typing.TypeVar("ValueT")
OtherKeyT = typing.TypeVar("OtherKeyT")
OtherValueT = typing.TypeVar("OtherValueT")
WINDOW_SIZE: typing.Final[int] = 1000


def chunk_values(
    values: typing.Iterable[ValueT], window_size: int = WINDOW_SIZE
) -> typing.Iterator[typing.Sequence[ValueT]]:
    iterator = iter(values)

    while result := list(itertools.islice(iterator, window_size)):
        yield result


async def iter_keys(
    client: aioredis.Redis, *, window_size: int = WINDOW_SIZE, match: typing.Optional[str] = None
) -> typing.AsyncIterator[typing.MutableSequence[bytes]]:
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
    async for window in iter_keys(client, window_size=window_size, match=match):
        yield await client.mget(*window)


async def iter_hash_keys(
    client: aioredis.Redis,
    key: redis.RedisValueT,
    *,
    window_size: int = WINDOW_SIZE,
    match: typing.Optional[str] = None,
) -> typing.AsyncIterator[typing.MutableSequence[bytes]]:
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
    client = await resource_client.get_connection(index)

    async for window in iter_reference_keys(resource_client, key, window_size=window_size, match=match):
        yield await client.mget(*window)


class Iterator(traits.CacheIterator[ValueT]):
    __slots__: typing.Sequence[str] = ("_buffer", "_builder", "_client", "_index", "_len", "_windows", "_window_size")

    def __init__(
        self,
        client: redis.ResourceClient,
        index: redis.ResourceIndex,
        builder: typing.Callable[[bytes], ValueT],
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

    def __aiter__(self) -> Iterator[ValueT]:
        return self

    async def __anext__(self) -> ValueT:
        if self._windows is None:
            client = await self._client.get_connection(self._index)
            self._windows = iter_values(client, window_size=self._window_size)

        while not self._buffer:
            async for window in self._windows:
                self._buffer.extend(window)
                break

            else:
                raise StopAsyncIteration from None

        return self._builder(self._buffer.pop(0))

    async def len(self) -> int:
        if self._len is None:
            client = await self._client.get_connection(self._index)
            self._len = int(await client.dbsize())

        return self._len


class ReferenceIterator(traits.CacheIterator[ValueT]):
    __slots__ = ("_buffer", "_builder", "_client", "_index", "_key", "_len", "_windows", "_window_size")

    def __init__(
        self,
        client: redis.ResourceClient,
        key: bytes,
        index: redis.ResourceIndex,
        builder: typing.Callable[[bytes], ValueT],
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

    def __aiter__(self) -> ReferenceIterator[ValueT]:
        return self

    async def __anext__(self) -> ValueT:
        if not self._windows:
            self._windows = iter_reference_values(self._client, self._index, self._key, window_size=self._window_size)

        while not self._buffer:
            async for window in self._windows:
                self._buffer.extend(window)
                break

            else:
                raise StopAsyncIteration from None

        return self._builder(self._buffer.pop(0))

    async def len(self) -> int:
        if self._len is None:
            client = await self._client.get_connection(redis.ResourceIndex.REFERENCE)
            self._len = int(await client.scard(self._key))

        return self._len


class HashReferenceIterator(traits.CacheIterator[ValueT]):
    __slots__ = ("_buffer", "_builder", "_client", "_index", "_key", "_len", "_windows", "_window_size")

    def __init__(
        self,
        client: redis.ResourceClient,
        key: bytes,
        index: redis.ResourceIndex,
        builder: typing.Callable[[bytes], ValueT],
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

    def __aiter__(self) -> HashReferenceIterator[ValueT]:
        return self

    async def __anext__(self) -> ValueT:
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
                self._buffer.extend(window)
                break

            else:
                raise StopAsyncIteration from None

        return self._builder(self._buffer.pop(0))

    async def len(self) -> int:
        if self._len is None:
            client = await self._client.get_connection(redis.ResourceIndex.REFERENCE)
            self._len = int(await client.scard(self._key)) - 1

        return self._len


async def _empty_async_iterator() -> typing.AsyncIterator[typing.Any]:
    if False:
        yield  # type: ignore[unreachable]


class MultiMapIterator(traits.CacheIterator[ValueT]):
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
        builder: typing.Callable[[bytes], ValueT],
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

    def __aiter__(self) -> MultiMapIterator[ValueT]:
        return self

    async def __anext__(self) -> ValueT:
        client = await self._client.get_connection(self._index)

        if not self._top_level_keys:
            keys: typing.AsyncIterator[bytes] = client.iscan()
            self._top_level_keys = keys

        while not self._buffer:
            async for window in self._windows:
                if not window:
                    continue

                self._buffer.extend(window)
                break

            if self._buffer:
                break

            async for key in self._top_level_keys:
                self._windows = iter_hash_values(client, key, window_size=self._window_size)
                break

            else:
                raise StopAsyncIteration from None

        return self._builder(self._buffer.pop(0))

    async def len(self) -> int:
        if self._len is None:
            client = await self._client.get_connection(self._index)
            keys = await client.keys("*")
            # For some reason mypy thinks this is optional without the int cast
            self._len = int(sum([int(await client.hlen(key)) for key in keys]))

        return self._len


class SpecificMapIterator(traits.CacheIterator[ValueT]):
    __slots__ = ("_buffer", "_builder", "_client", "_index", "key", "_len", "_windows", "_window_size")

    def __init__(
        self,
        client: redis.ResourceClient,
        key: bytes,
        index: redis.ResourceIndex,
        builder: typing.Callable[[bytes], ValueT],
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

    def __aiter__(self) -> SpecificMapIterator[ValueT]:
        return self

    async def __anext__(self) -> ValueT:
        if not self._windows:
            client = await self._client.get_connection(self._index)
            self._windows = iter_hash_values(client, self._key, window_size=self._window_size)

        while not self._buffer:
            async for window in self._windows:
                self._buffer.extend(window)
                break

            else:
                raise StopAsyncIteration from None

        return self._builder(self._buffer.pop(0))

    async def len(self) -> int:
        # TODO: override "count" method?
        if self._len is None:
            client = await self._client.get_connection(self._index)
            self._len = int(await client.hlen(self._key))

        return self._len
