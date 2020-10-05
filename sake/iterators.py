from __future__ import annotations

__all__: typing.Final[typing.Sequence[str]] = ["RedisIterator"]

import typing

from sake import traits

if typing.TYPE_CHECKING:
    from sake import cache
    from sake import conversion


ValueT = typing.TypeVar("ValueT")


class RedisIterator(traits.CacheIterator[ValueT]):
    __slots__: typing.Sequence[str] = ("_builder", "_client", "_get_method", "_index", "_iterator")

    def __init__(
        self,
        client: cache.ResourceClient,
        index: cache.ResourceIndex,
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

        self._iterator = None
        raise StopAsyncIteration

    async def len(self) -> int:
        client = await self._client.get_connection(self._index)
        count = await client.dbsize()
        assert isinstance(count, int), f"Aioredis returned a {type(count)} when an integer was expected"
        return count
