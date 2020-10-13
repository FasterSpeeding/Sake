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


KeyT = typing.TypeVar("KeyT")
ValueT = typing.TypeVar("ValueT")
WINDOW_SIZE: typing.Final[int] = 100


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

        self._buffer = []
        self._builder = builder
        self._client = client
        self._index = index
        self._len: typing.Optional[int] = None
        self._windows: typing.Optional[typing.AsyncIterator[bytes]] = None
        self._window_size = int(window_size)

    def __aiter__(self) -> RedisIterator[ValueT]:
        return self

    async def __anext__(self) -> ValueT:
        if self._windows is None:
            client = await self._client.get_connection(self._index)
            self._windows = redis.global_iter_get(client, self._window_size)

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
        self._windows: typing.Optional[typing.AsyncIterator[bytes]] = None
        self._window_size = int(window_size)

    async def __anext__(self) -> ValueT:
        if not self._windows:
            self._windows = redis.reference_iter_get(self._client, self._index, self._key, self._window_size)

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
        yield


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

    def __init__(  # TODO: chunk requests
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
        self._windows: typing.AsyncIterator[ValueT] = _empty_async_iterator()
        self._window_size = int(window_size)

    def __aiter__(self) -> MultiMapIterator[ValueT]:
        return self

    async def __anext__(self) -> ValueT:
        client = await self._client.get_connection(self._index)

        if not self._top_level_keys:
            self._top_level_keys = client.iscan()

        while not self._buffer:
            async for window in self._windows:
                if not window:
                    continue

                self._buffer.extend(window)
                break

            if self._buffer:
                break

            async for key in self._top_level_keys:
                self._windows = redis.iter_hget(client, key, self._window_size)
                break

            else:
                raise StopAsyncIteration from None

        return self._builder(self._buffer.pop(0))

    async def len(self) -> int:
        if self._len is None:
            client = await self._client.get_connection(self._index)
            keys = await client.keys("*")
            self._len = sum([await client.hlen(key) for key in keys])

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
        self._windows: typing.Optional[typing.AsyncIterator[bytes]] = None
        self._window_size = window_size

    async def __anext__(self) -> ValueT:
        if not self._windows:
            client = await self._client.get_connection(self._index)
            self._windows = redis.iter_hget(client, self._key, self._window_size)

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
            self._len = await client.hlen(self._key)  # TODO: key

        return self._len
