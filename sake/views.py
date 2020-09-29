from __future__ import annotations

__all__: typing.Final[typing.Sequence[str]] = ("CacheView",)

import abc
import typing

from hikari import iterators


KeyT = typing.TypeVar("KeyT")
ValueT = typing.TypeVar("ValueT")


class CacheView(iterators.LazyIterator[ValueT], typing.Generic[KeyT, ValueT], abc.ABC):
    @abc.abstractmethod
    async def get(self, key: KeyT) -> ValueT:
        raise NotImplementedError

    @abc.abstractmethod
    async def len(self) -> int:
        raise NotImplementedError

    # @abc.abstractmethod
    # async def search(self):  # TODO: search technique
    #     raise NotImplementedError
