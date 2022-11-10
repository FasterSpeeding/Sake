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
from __future__ import annotations

__all__: typing.Sequence[str] = [
    "AsyncCacheAdapter",
    "CacheIteratorAdapter",
    "GuildAndGlobalCacheAdapter",
    "GuildBoundCacheAdapter",
    "SingleStoreAdapter",
]

import typing

import hikari
from tanjun.dependencies import async_cache

from . import abc
from . import errors

_DefaultT = typing.TypeVar("_DefaultT")
_KeyT = typing.TypeVar("_KeyT")
_ValueT = typing.TypeVar("_ValueT")


class CacheIteratorAdapter(async_cache.CacheIterator[_ValueT]):
    """Tanjun adapter for a cache iterator."""

    __slots__ = ("_iterator",)

    def __init__(self, iterator: abc.CacheIterator[_ValueT], /) -> None:
        """Initialise a cache iterator adapter.

        Parameters
        ----------
        iterator
            The iterator to adapt.
        """
        self._iterator = iterator

    async def __anext__(self) -> _ValueT:
        try:
            return await self._iterator.__anext__()

        except errors.ClosedClient:
            raise StopAsyncIteration from None

    def len(self) -> typing.Coroutine[typing.Any, typing.Any, int]:
        return self._iterator.len()


class EmptyCacheIterator(async_cache.CacheIterator[typing.Any]):
    """An empty Tanjun cache iterator."""

    __slots__ = ()

    async def __anext__(self) -> typing.NoReturn:
        raise StopAsyncIteration

    async def len(self) -> int:
        return 0


class SingleStoreAdapter(async_cache.SingleStoreCache[_ValueT]):
    """Tanjun adapter for a single store cache."""

    __slots__ = ("_get", "_trust_get")

    def __init__(self, get: typing.Callable[[], typing.Awaitable[_ValueT]], trust_get: bool) -> None:
        """Initialise a single store adapter.

        Parameters
        ----------
        get
            Async method used to get this cache's resource.
        trust_get
            Whether this should raise [tanjun.async_cache.EntryNotFound][] if
            the entry isn't found to indicate that it doesn't exist, rather
            than just [tanjun.async_cache.CacheMissError][]
        """
        self._get = get
        self._trust_get = trust_get

    async def get(self, *, default: _DefaultT = ...) -> typing.Union[_ValueT, _DefaultT]:
        try:
            return await self._get()

        except errors.ClosedClient:
            if default is not ...:
                return default

            raise async_cache.CacheMissError from None

        except errors.EntryNotFound:
            if default is not ...:
                return default

            if self._trust_get:
                raise async_cache.EntryNotFound from None

            raise async_cache.CacheMissError from None


class AsyncCacheAdapter(async_cache.AsyncCache[_KeyT, _ValueT]):
    """Tanjun adapter for a global key-value async cache store."""

    __slots__ = ("_get", "_iterate_all", "_trust_get")

    def __init__(
        self,
        get: typing.Callable[[_KeyT], typing.Awaitable[_ValueT]],
        iterate_all: typing.Callable[[], abc.CacheIterator[_ValueT]],
        trust_get: bool,
    ) -> None:
        """Initialise an async cache adapter.

        Parameters
        ----------
        get
            Callback used to get an entry in this cache store.
        iterate_all
            Callback used to iterate over all the entries in this cache store.
        trust_get
            Whether this should raise [tanjun.async_cache.EntryNotFound][] if
            the entry isn't found to indicate that it doesn't exist, rather
            than just [tanjun.async_cache.CacheMissError][]
        """
        self._get = get
        self._iterate_all = iterate_all
        self._trust_get = trust_get

    async def get(self, key: _KeyT, /, *, default: _DefaultT = ...) -> typing.Union[_ValueT, _DefaultT]:
        try:
            return await self._get(key)

        except errors.ClosedClient:
            if default is not ...:
                return default

            raise async_cache.CacheMissError from None

        except errors.EntryNotFound:
            if default is not ...:
                return default

            if self._trust_get:
                raise async_cache.EntryNotFound from None

            raise async_cache.CacheMissError from None

    def iter_all(self) -> async_cache.CacheIterator[_ValueT]:
        try:
            return CacheIteratorAdapter(self._iterate_all())

        except errors.ClosedClient:
            return EmptyCacheIterator()


def _not_implemented(*args: typing.Any, **kwargs: typing.Any) -> typing.NoReturn:
    raise NotImplementedError


class GuildBoundCacheAdapter(AsyncCacheAdapter[_KeyT, _ValueT], async_cache.GuildBoundCache[_KeyT, _ValueT]):
    """Tanjun adapter for a guild-bound key-value async cache store."""

    __slots__ = ("_get_from_guild", "_iterate_for_guild")

    def __init__(
        self,
        get_from_guild: typing.Callable[[hikari.Snowflakeish, _KeyT], typing.Awaitable[_ValueT]],
        iterate_all: typing.Callable[[], abc.CacheIterator[_ValueT]],
        iterate_for_guild: typing.Callable[[hikari.Snowflakeish], abc.CacheIterator[_ValueT]],
        trust_get: bool,
    ) -> None:
        """Initialise a guild-bound cache adapter.

        Parameters
        ----------
        get_from_guild
            Callback used to get an entry from this cache store.
        iterate_all
            Callback used to iterate over all the entries in this cache store
            (globally).
        iterate_for_guild
            Callback used to iterate over the entries in this cache store for a
            specific guild.
        trust_get
            Whether this should raise [tanjun.async_cache.EntryNotFound][] if
            the entry isn't found to indicate that it doesn't exist, rather
            than just [tanjun.async_cache.CacheMissError][]
        """
        super().__init__(get=_not_implemented, iterate_all=iterate_all, trust_get=trust_get)
        self._get_from_guild = get_from_guild
        self._iterate_for_guild = iterate_for_guild

    async def get_from_guild(
        self, guild_id: hikari.Snowflakeish, key: _KeyT, /, *, default: _DefaultT = ...
    ) -> typing.Union[_ValueT, _DefaultT]:
        try:
            return await self._get_from_guild(guild_id, key)

        except errors.ClosedClient:
            if default is not ...:
                return default

            raise async_cache.CacheMissError from None

        except errors.EntryNotFound:
            if default is not ...:
                return default

            if self._trust_get:
                raise async_cache.EntryNotFound from None

            raise async_cache.CacheMissError from None

    def iter_for_guild(self, guild_id: hikari.Snowflakeish, /) -> async_cache.CacheIterator[_ValueT]:
        try:
            return CacheIteratorAdapter(self._iterate_for_guild(guild_id))

        except errors.ClosedClient:
            return EmptyCacheIterator()


class GuildAndGlobalCacheAdapter(AsyncCacheAdapter[_KeyT, _ValueT], async_cache.GuildBoundCache[_KeyT, _ValueT]):
    """Tanjun adapter for a global key-value cache store with guild tracking."""

    __slots__ = ("_iterate_for_guild", "_verify_guild")

    def __init__(
        self,
        get: typing.Callable[[_KeyT], typing.Awaitable[_ValueT]],
        iterate_all: typing.Callable[[], abc.CacheIterator[_ValueT]],
        iterate_for_guild: typing.Callable[[hikari.Snowflakeish], abc.CacheIterator[_ValueT]],
        verify_guild: typing.Callable[[hikari.Snowflakeish, _ValueT], bool],
        trust_get: bool,
    ) -> None:
        """Initialise a guild and global cache adapter.

        get
            Async method used to get this cache's resource.
        iterate_all
            Callback used to iterate over all the entries in this cache store
            (globally).
        iterate_for_guild
            Callback used to iterate over the entries in this cache store for a
            specific guild.
        verify_guild
            Callback used to verify that an entry is in the target guild when
            getting the entry from a specific guild.
        trust_get
            Whether this should raise [tanjun.async_cache.EntryNotFound][] if
            the entry isn't found to indicate that it doesn't exist, rather
            than just [tanjun.async_cache.CacheMissError][]
        """
        super().__init__(get, iterate_all, trust_get)
        self._iterate_for_guild = iterate_for_guild
        self._verify_guild = verify_guild

    async def get_from_guild(
        self, guild_id: hikari.Snowflakeish, key: _KeyT, /, *, default: _DefaultT = ...
    ) -> typing.Union[_ValueT, _DefaultT]:
        result = await self.get(key, default=default)
        if result is default or self._verify_guild(guild_id, typing.cast(_ValueT, result)):
            return result

        if default is not ...:
            return default

        if self._trust_get or result is not default:
            raise async_cache.EntryNotFound

        raise async_cache.CacheMissError from None

    def iter_for_guild(self, guild_id: hikari.Snowflakeish, /) -> async_cache.CacheIterator[_ValueT]:
        try:
            return CacheIteratorAdapter(self._iterate_for_guild(guild_id))

        except errors.ClosedClient:
            return EmptyCacheIterator()
