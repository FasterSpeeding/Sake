# -*- coding: utf-8 -*-
# cython: language_level=3
# BSD 3-Clause License
#
# Copyright (c) 2020-2021, Faster Speeding
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
    "AsyncCacheAdaptor",
    "CacheIteratorAdaptor",
    "GuildAndGlobalCacheAdaptor",
    "GuildBoundCacheAdaptor",
    "SingleStoreAdaptor",
]

import typing

import hikari
import tanjun

from . import abc
from . import errors

_KeyT = typing.TypeVar("_KeyT")
_ValueT = typing.TypeVar("_ValueT")


class CacheIteratorAdaptor(tanjun.dependencies.CacheIterator[_ValueT]):
    __slots__ = ("_iterator",)

    def __init__(self, iterator: abc.CacheIterator[_ValueT], /) -> None:
        self._iterator = iterator

    def __anext__(self) -> typing.Coroutine[typing.Any, typing.Any, _ValueT]:
        return self._iterator.__anext__()

    def len(self) -> typing.Coroutine[typing.Any, typing.Any, int]:
        return self._iterator.len()


class SingleStoreAdaptor(tanjun.dependencies.SingleStoreCache[_ValueT]):
    __slots__ = ("_get", "_trust_get")

    def __init__(self, get: typing.Callable[[], typing.Awaitable[_ValueT]], trust_get: bool):
        self._get = get
        self._trust_get = trust_get

    async def get(self) -> _ValueT:
        try:
            return await self._get()

        except errors.EntryNotFound:
            if self._trust_get:
                raise tanjun.dependencies.EntryNotFound from None

            raise tanjun.dependencies.CacheMissError from None


class AsyncCacheAdaptor(tanjun.dependencies.AsyncCache[_KeyT, _ValueT]):
    __slots__ = ("_get", "_iterate_all", "_trust_get")

    def __init__(
        self,
        get: typing.Callable[[_KeyT], typing.Awaitable[_ValueT]],
        iterate_all: typing.Callable[[], abc.CacheIterator[_ValueT]],
        trust_get: bool,
    ) -> None:
        self._get = get
        self._iterate_all = iterate_all
        self._trust_get = trust_get

    async def get(self, key: _KeyT, /) -> _ValueT:
        try:
            return await self._get(key)

        except errors.EntryNotFound:
            if self._trust_get:
                raise tanjun.dependencies.EntryNotFound from None

            raise tanjun.dependencies.CacheMissError from None

    def iter_all(self) -> tanjun.dependencies.CacheIterator[_ValueT]:
        return CacheIteratorAdaptor(self._iterate_all())


class GuildBoundCacheAdaptor(AsyncCacheAdaptor, tanjun.dependencies.GuildBoundCache[_KeyT, _ValueT]):
    __slots__ = ("_get_from_guild", "_iterate_all", "_iterate_for_guild")

    async def __init__(
        self,
        get_from_guild: typing.Callable[[hikari.Snowflakeish, _KeyT], typing.Awaitable[_ValueT]],
        iterate_all: typing.Callable[[], abc.CacheIterator[_ValueT]],
        iterate_for_guild: typing.Callable[[hikari.Snowflakeish], abc.CacheIterator[_ValueT]],
        trust_get: bool,
    ) -> None:
        self._get_from_guild = get_from_guild
        self._iterate_all = iterate_all
        self._iterate_for_guild = iterate_for_guild
        self._trust_get = trust_get

    async def get_from_guild(self, guild_id: hikari.Snowflakeish, key: _KeyT, /) -> _ValueT:
        try:
            return await self._get_from_guild(guild_id, key)

        except errors.EntryNotFound:
            if self._trust_get:
                raise tanjun.dependencies.EntryNotFound from None

            raise tanjun.dependencies.CacheMissError from None

    def iter_all(self) -> tanjun.dependencies.CacheIterator[_ValueT]:
        return CacheIteratorAdaptor(self._iterate_all())

    def iter_for_guild(self, guild_id: hikari.Snowflakeish, /) -> tanjun.dependencies.CacheIterator[_ValueT]:
        return CacheIteratorAdaptor(self._iterate_for_guild(guild_id))


class GuildAndGlobalCacheAdaptor(
    AsyncCacheAdaptor[_KeyT, _ValueT], tanjun.dependencies.GuildBoundCache[_KeyT, _ValueT]
):
    __slots__ = ("_iterate_for_guild", "_verify_guild")

    async def __init__(
        self,
        get: typing.Callable[[_KeyT], typing.Awaitable[_ValueT]],
        iterate_all: typing.Callable[[], abc.CacheIterator[_ValueT]],
        iterate_for_guild: typing.Callable[[hikari.Snowflakeish], abc.CacheIterator[_ValueT]],
        verify_guild: typing.Callable[[hikari.Snowflakeish, _ValueT], bool],
        trust_get: bool,
    ) -> None:
        super().__init__(get, iterate_all, trust_get)
        self._iterate_for_guild = iterate_for_guild
        self._verify_guild = verify_guild

    async def get_from_guild(self, guild_id: hikari.Snowflakeish, key: _KeyT, /) -> _ValueT:
        result = await self.get(key)
        if self._verify_guild(guild_id, result):
            return result

        raise tanjun.dependencies.EntryNotFound

    def iter_for_guild(self, guild_id: hikari.Snowflakeish, /) -> tanjun.dependencies.CacheIterator[_ValueT]:
        return CacheIteratorAdaptor(self._iterate_for_guild(guild_id))
