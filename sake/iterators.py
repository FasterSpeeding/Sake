from __future__ import annotations

__all__: typing.Final[typing.Sequence[str]] = ["RedisIterator", "SpecificRedisIterator"]

import typing

from sake import traits

if typing.TYPE_CHECKING:
    from sake import redis
    from sake import conversion


KeyT = typing.TypeVar("KeyT")
ValueT = typing.TypeVar("ValueT")


class RedisIterator(traits.CacheIterator[ValueT]):
    __slots__: typing.Sequence[str] = ("_client", "_get_method", "_index", "_iterator")

    def __init__(
        self,
        client: redis.ResourceClient,
        index: redis.ResourceIndex,
        get_method: typing.Callable[[conversion.RedisValueT], typing.Coroutine[typing.Any, typing.Any, ValueT]],
    ) -> None:
        self._client = client
        self._get_method = get_method
        self._index = index
        self._iterator: typing.Optional[typing.AsyncIterator[conversion.RedisValueT]] = None

    def __aiter__(self) -> RedisIterator[ValueT]:
        return self

    async def __anext__(self) -> ValueT:
        client = await self._client.get_connection(self._index)
        if self._iterator is None:
            self._iterator = client.iscan()

        async for key in self._iterator:
            return await self._get_method(key)

        raise StopAsyncIteration

    async def len(self) -> int:
        client = await self._client.get_connection(self._index)
        count = await client.dbsize()
        assert isinstance(count, int), f"Aioredis returned a {type(count)} when an integer was expected"
        return count


class SpecificRedisIterator(traits.CacheIterator[ValueT]):
    __slots__: typing.Sequence[str] = ("_ids", "_get_ids_method", "_get_method", "_len")

    def __init__(
        self,
        get_ids_method: typing.Callable[[], typing.Coroutine[typing.Any, typing.Any, typing.Sequence[KeyT]]],
        get_method: typing.Callable[[KeyT], typing.Coroutine[typing.Any, typing.Any, ValueT]],
    ) -> None:
        self._ids: typing.Optional[typing.Iterator[KeyT]] = None
        self._get_ids_method = get_ids_method
        self._get_method = get_method
        self._len: typing.Optional[int] = None

    def __aiter__(self) -> SpecificRedisIterator[ValueT]:
        return self

    async def __anext__(self) -> ValueT:
        if self._ids is None:
            ids = await self._get_ids_method()
            self._ids = iter(ids)
            self._len = len(ids)

        try:
            current_id = next(self._ids)
        except StopIteration:
            raise StopAsyncIteration from None
        else:
            return await self._get_method(current_id)

    async def len(self) -> typing.Optional[int]:
        return self._len
