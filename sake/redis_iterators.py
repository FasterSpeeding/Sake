from __future__ import annotations

__all__: typing.Final[typing.Sequence[str]] = [
    "RedisIterator",
    "SpecificRedisIterator",
    "MultiMapIterator",
    "SpecificMapIterator",
]

import typing

from sake import redis
from sake import traits

if typing.TYPE_CHECKING:
    import aioredis


KeyT = typing.TypeVar("KeyT")
ValueT = typing.TypeVar("ValueT")
OtherKeyT = typing.TypeVar("OtherKeyT")
OtherValueT = typing.TypeVar("OtherValueT")
WINDOW_SIZE: typing.Final[int] = 1000


async def global_iter_get(
    client: aioredis.Redis, window_size: int
) -> typing.AsyncIterator[typing.MutableSequence[bytes]]:
    cursor = 0
    while True:
        cursor, results = await client.scan(cursor, count=window_size)

        if results:
            yield await client.mget(*results)

        if not cursor:
            break


async def iter_hget(
    client: aioredis.Redis, key: redis.RedisValueT, window_size: int
) -> typing.AsyncIterator[typing.MutableSequence[bytes]]:
    cursor = 0
    while True:
        cursor, results = await client.hscan(key, cursor, count=window_size)

        if results:
            yield [result for _, result in results]

        if not cursor:
            break


async def reference_iter_get(
    resource_client: redis.ResourceClient, index: redis.ResourceIndex, key: redis.RedisValueT, window_size: int
) -> typing.AsyncIterator[typing.MutableSequence[bytes]]:
    client = await resource_client.get_connection(index)
    reference_client = await resource_client.get_connection(redis.ResourceIndex.GUILD_REFERENCE)
    cursor = 0

    while True:
        cursor, results = await reference_client.sscan(key, cursor, count=window_size)

        if results:
            yield await client.mget(*results)

        if not cursor:
            break


class RedisIterator(traits.CacheIterator[ValueT]):
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

    def __aiter__(self) -> RedisIterator[ValueT]:
        return self

    async def __anext__(self) -> ValueT:
        if self._windows is None:
            client = await self._client.get_connection(self._index)
            self._windows = global_iter_get(client, self._window_size)

        while not self._buffer:
            async for window in self._windows:
                self._buffer.extend(window)
                break

            else:
                raise StopAsyncIteration from None

        return self._builder(self._buffer.pop(0))

    async def len(self) -> int:
        if self._len is not None:
            return self._len

        client = await self._client.get_connection(self._index)
        count = await client.dbsize()
        assert isinstance(count, int), f"Aioredis returned a {type(count)} when an integer was expected"
        self._len = count
        return count


class SpecificRedisIterator(traits.CacheIterator[ValueT]):
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

    def __aiter__(self) -> SpecificRedisIterator[ValueT]:
        return self

    async def __anext__(self) -> ValueT:
        if not self._windows:
            self._windows = reference_iter_get(self._client, self._index, self._key, self._window_size)

        while not self._buffer:
            async for window in self._windows:
                self._buffer.extend(window)
                break

            else:
                raise StopAsyncIteration from None

        return self._builder(self._buffer.pop(0))

    async def len(self) -> int:
        if self._len is None:
            client = await self._client.get_connection(redis.ResourceIndex.GUILD_REFERENCE)
            self._len = len(await client.get(self._key))

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
                self._windows = iter_hget(client, key, self._window_size)
                break

            else:
                raise StopAsyncIteration from None

        return self._builder(self._buffer.pop(0))

    async def len(self) -> int:
        if self._len is None:
            client = await self._client.get_connection(self._index)
            keys = await client.keys("*")
            # For some reason mypy thinks this is optional without the int cast
            self._len = int(sum([await client.hlen(key) for key in keys]))

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
            self._windows = iter_hget(client, self._key, self._window_size)

        assert self._windows is not None
        while not self._buffer and self._windows:
            async for window in self._windows:
                self._buffer.extend(window)
                break

            else:
                raise StopAsyncIteration from None

        return self._builder(self._buffer.pop(0))

    async def len(self) -> int:
        if self._len is None:
            client = await self._client.get_connection(self._index)
            self._len = await client.hlen(self._key)

        return self._len
