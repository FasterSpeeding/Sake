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
"""Protocols and abstract classes for the cache resources defined by this standard.

.. note::
    Unlike the abstract classes defined here, there is no guarantee that the
    protocols defined here will be included in the MRO of the classes which
    implement them.
"""

from __future__ import annotations

__all__: typing.Sequence[str] = [
    "Cache",
    "CacheIterator",
    "EmojiCache",
    "GuildCache",
    "GuildChannelCache",
    "InviteCache",
    "MeCache",
    "MemberCache",
    "MessageCache",
    "PresenceCache",
    "Resource",
    "RefCache",
    "RefEmojiCache",
    "RefGuildCache",
    "RefGuildChannelCache",
    "RefInviteCache",
    "RefMeCache",
    "RefMemberCache",
    "RefMessageCache",
    "RefPresenceCache",
    "RefRoleCache",
    "RefUserCache",
    "RefVoiceStateCache",
    "RoleCache",
    "UserCache",
    "VoiceStateCache",
]

import abc
import typing

import hikari

ValueT = typing.TypeVar("ValueT")


class CacheIterator(hikari.LazyIterator[ValueT], abc.ABC):
    """A asynchronous iterator of entries within a defined cache store."""

    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    async def len(self) -> int:
        """Get the count of entries that this iterator covers.

        .. note::
            Unlike `hikari.iterators.LazyIterator.count`, this will not exhaust
            the iterator and may return different values as entries are added
            and removed from the cache.

        Returns
        -------
        int
            The count of entries that this iterator covers as of the call.
        """


class Resource(abc.ABC):
    """The basic interface which all cache resources should implement."""

    __slots__: typing.Sequence[str] = ()

    @property
    @abc.abstractmethod
    def is_alive(self) -> bool:
        """Whether this client is alive."""

    @abc.abstractmethod
    async def open(self) -> None:
        """Startup the resource(s) and allow them to connect to their relevant backend(s).

        .. note::
            This should pass without raising if called on an already opened
            resource.
        """

    @abc.abstractmethod
    async def close(self) -> None:
        """Close the resource(s) and allow them to disconnect from their relevant backend(s).

        .. note::
            This should pass without raising if called on an already closed
            resource.
        """


class EmojiCache(Resource, abc.ABC):
    """The traits of a implementation which supports a emoji cache."""

    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    async def clear_emojis(self) -> None:
        """Empty the emoji cache store.

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """

    @abc.abstractmethod
    async def delete_emoji(self, emoji_id: hikari.Snowflakeish, /) -> None:
        """Remove an emoji from the cache.

        .. note::
            Delete methods do not raise `sake.errors.CacheMissError` when the
            targeted entity doesn't exist.

        Parameters
        ----------
        emoji_id : hikari.snowflakes.Snowflakeish
            The ID of the emoji to remove from the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """

    @abc.abstractmethod
    async def get_emoji(self, emoji_id: hikari.Snowflakeish, /) -> hikari.KnownCustomEmoji:
        """Get an emoji from the cache.

        Parameters
        ----------
        emoji_id : hikari.snowflakes.Snowflakeish
            The ID of the emoji to get from the cache.

        Returns
        -------
        hikari.emojis.KnownCustomEmoji
            The object of the emoji fetched from the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.EntryNotFound
            Raised when the targeted entity wasn't found.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """

    @abc.abstractmethod
    def iter_emojis(self) -> CacheIterator[hikari.KnownCustomEmoji]:
        """Iterate over the emojis stored in the cache.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Returns
        -------
        CacheIterator[hikari.emojis.KnownCustomEmoji]
            An async iterator of the emojis stored in the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """


class RefEmojiCache(EmojiCache, abc.ABC):
    """The traits of a implementation which supports a referential emoji cache."""

    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    async def clear_emojis_for_guild(self, guild_id: hikari.Snowflakeish, /) -> None:
        """Remove emojis belonging to a specific guild from the cache.

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove the emojis cached for.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """

    @abc.abstractmethod
    def iter_emojis_for_guild(self, guild_id: hikari.Snowflakeish, /) -> CacheIterator[hikari.KnownCustomEmoji]:
        """Iterate over the emojis stored in the cache for a specific guild.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to iterate over the emojis cached for.

        Returns
        -------
        CacheIterator[hikari.emojis.KnownCustomEmoji]
            An async iterator of the emojis stored in the cache for the
            specified guild.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """


class GuildCache(Resource, abc.ABC):
    """The traits of a implementation which supports a guild cache."""

    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    async def clear_guilds(self) -> None:
        """Empty the guild cache store.

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """

    @abc.abstractmethod
    async def delete_guild(self, guild_id: hikari.Snowflakeish, /) -> None:
        """Remove a guild from the cache.

        .. note::
            Delete methods do not raise `sake.errors.CacheMissError` when the
            targeted entity doesn't exist.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove from the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """

    @abc.abstractmethod
    async def get_guild(self, guild_id: hikari.Snowflakeish, /) -> hikari.GatewayGuild:
        """Get a guild from the cache.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to get from the cache.

        Returns
        -------
        hikari.guilds.GatewayGuild
            The object of the guild fetched from the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.EntryNotFound
            Raised when the targeted entity wasn't found.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """

    @abc.abstractmethod
    def iter_guilds(self) -> CacheIterator[hikari.GatewayGuild]:
        """Iterate over the guilds stored in the cache.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Returns
        -------
        CacheIterator[hikari.guilds.GatewayGuild]
            An async iterator of the guilds stored in the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """


RefGuildCache = GuildCache


class GuildChannelCache(Resource, abc.ABC):
    """The traits of a implementation which supports a guild channel cache."""

    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    async def clear_guild_channels(self) -> None:
        """Empty the guild channel cache store.

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """

    @abc.abstractmethod
    async def delete_guild_channel(self, channel_id: hikari.Snowflakeish, /) -> None:
        """Remove a guild guild channel from the cache.

        .. note::
            Delete methods do not raise `sake.errors.CacheMissError` when the
            targeted entity doesn't exist.

        Parameters
        ----------
        channel_id : hikari.snowflakes.Snowflakeish
            The ID of the guild channel to remove from the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """

    @abc.abstractmethod
    async def get_guild_channel(self, channel_id: hikari.Snowflakeish, /) -> hikari.GuildChannel:
        """Get a guild channel from the cache.

        Parameters
        ----------
        channel_id : hikari.snowflakes.Snowflakeish
            The ID of the guild channel to get from the cache.

        Returns
        -------
        hikari.channels.GuildChannel
            The object of the guild channel fetched from the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.EntryNotFound
            Raised when the targeted entity wasn't found.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """

    @abc.abstractmethod
    def iter_guild_channels(self) -> CacheIterator[hikari.GuildChannel]:
        """Iterate over the guild channels stored in the cache.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Returns
        -------
        CacheIterator[hikari.channels.GuildChannel]
            An async iterator of the guild channels stored in the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """


class RefGuildChannelCache(GuildChannelCache, abc.ABC):
    """The traits of a implementation which supports a referential guild channel cache."""

    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    async def clear_guild_channels_for_guild(self, guild_id: hikari.Snowflakeish, /) -> None:
        """Remove the guild channel cache store for the specified guild.

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove the cached channels for.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """

    @abc.abstractmethod
    def iter_guild_channels_for_guild(self, guild_id: hikari.Snowflakeish, /) -> CacheIterator[hikari.GuildChannel]:
        """Iterate over the guild channels stored in the cache for a specific guild.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to iterate over the guild channels cached for it.

        Returns
        -------
        CacheIterator[hikari.channels.GuildChannel]
            An async iterator of the guild channels stored in the cache for the
            specified guild.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """


class InviteCache(Resource, abc.ABC):
    """The traits of a implementation which supports a invite cache."""

    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    async def clear_invites(self) -> None:
        """Empty the invites cache store.

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """

    @abc.abstractmethod
    async def delete_invite(self, invite_code: str, /) -> None:
        """Remove an invite channel from the cache.

        .. note::
            Delete methods do not raise `sake.errors.CacheMissError` when the
            targeted entity doesn't exist.

        Parameters
        ----------
        invite_code : str
            The code of the invite to remove from the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """

    @abc.abstractmethod
    async def get_invite(self, invite_code: str, /) -> hikari.InviteWithMetadata:
        """Get an invite from the cache.

        Parameters
        ----------
        invite_code : str
            The code of the invite to get from the cache.

        Returns
        -------
        hikari.invites.InviteWithMetadata
            The object of the invite fetched from the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.EntryNotFound
            Raised when the targeted entity wasn't found.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """

    @abc.abstractmethod
    def iter_invites(self) -> CacheIterator[hikari.InviteWithMetadata]:
        """Iterate over the invites stored in the cache.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Returns
        -------
        CacheIterator[hikari.invites.InviteWithMetadata]
            An async iterator of the invites stored in the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """


class RefInviteCache(InviteCache, abc.ABC):
    """The traits of a implementation which supports a referential invite cache."""

    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    async def clear_invites_for_channel(self, channel_id: hikari.Snowflakeish, /) -> None:
        """Remove invites cached for a specific channel..

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Parameters
        ----------
        channel_id : hikari.snowflakes.Snowflakeish
            The ID of the channel to remove the invites cached for it.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """

    @abc.abstractmethod
    async def clear_invites_for_guild(self, guild_id: hikari.Snowflakeish, /) -> None:
        """Remove invites cached for a specific guild.

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove the invites cached for it.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """

    @abc.abstractmethod
    def iter_invites_for_channel(self, channel_id: hikari.Snowflakeish, /) -> CacheIterator[hikari.InviteWithMetadata]:
        """Iterate over the invites stored in the cache for a specific channel.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Parameters
        ----------
        channel_id : hikari.snowflakes.Snowflakeish
            The ID of the channel to iterate over the invites cached for.

        Returns
        -------
        CacheIterator[hikari.invites.InviteWithMetadata]
            An async iterator of the invites stored in the cache for the
            specified channel.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """

    @abc.abstractmethod
    def iter_invites_for_guild(self, guild_id: hikari.Snowflakeish, /) -> CacheIterator[hikari.InviteWithMetadata]:
        """Iterate over the invites stored in the cache for a specific guild.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to iterate over the invites cached for.

        Returns
        -------
        CacheIterator[hikari.invites.InviteWithMetadata]
            An async iterator of the invites stored in the cache for the
            specified guild.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """


class MeCache(Resource, abc.ABC):
    """The traits of a implementation which supports a own user cache."""

    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    async def delete_me(self) -> None:
        """Remove the cached own user entry.

        .. note::
            Delete methods do not raise `sake.errors.CacheMissError` when the
            targeted entity doesn't exist.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """

    @abc.abstractmethod
    async def get_me(self) -> hikari.OwnUser:
        """Get the own user cache entry.

        Returns
        -------
        hikari.users.OwnUser
            The object of the own user fetched from the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.EntryNotFound
            Raised when the targeted entity wasn't found.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """


RefMeCache = MeCache


class MemberCache(Resource, abc.ABC):
    """The traits of a implementation which supports a member cache."""

    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    async def clear_members(self) -> None:
        """Empty the members cache store.

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """

    @abc.abstractmethod
    async def delete_member(self, guild_id: hikari.Snowflakeish, user_id: hikari.Snowflakeish, /) -> None:
        """Remove a member from the cache.

        .. note::
            Delete methods do not raise `sake.errors.CacheMissError` when the
            targeted entity doesn't exist.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove a cached member for.
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the user to remove a cached member for.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """

    @abc.abstractmethod
    async def get_member(self, guild_id: hikari.Snowflakeish, user_id: hikari.Snowflakeish, /) -> hikari.Member:
        """Get a member from the cache.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to get a cached member for.
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the user to get a cached member for.

        Returns
        -------
        hikari.guilds.Member
            The object of the member fetched from the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.EntryNotFound
            Raised when the targeted entity wasn't found.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """

    @abc.abstractmethod
    def iter_members(
        self,
    ) -> CacheIterator[hikari.Member]:
        """Iterate over the members stored in the cache.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Returns
        -------
        CacheIterator[hikari.guilds.Member]
            An async iterator of the members stored in the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """


class RefMemberCache(MemberCache, abc.ABC):
    """The traits of a implementation which supports a referential member cache."""

    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    async def clear_members_for_guild(self, guild_id: hikari.Snowflakeish, /) -> None:
        """Remove the members cached for a specific guild.

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove the cached members for.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """

    @abc.abstractmethod
    async def clear_members_for_user(self, user_id: hikari.Snowflakeish, /) -> None:
        """Remove the members cached for a specific user.

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Parameters
        ----------
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the user to remove the cached members for.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """

    @abc.abstractmethod
    def iter_members_for_guild(self, guild_id: hikari.Snowflakeish, /) -> CacheIterator[hikari.Member]:
        """Iterate over the members stored in the cache for a specific guild.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to get the members cached for.

        Returns
        -------
        CacheIterator[hikari.guilds.Member]
            An async iterator of the members stored in the cache for the
            specified guild.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """

    @abc.abstractmethod
    def iter_members_for_user(self, user_id: hikari.Snowflakeish, /) -> CacheIterator[hikari.Member]:
        """Iterate over the members stored in the cache for a specific user.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Parameters
        ----------
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to get the user cached for.

        Returns
        -------
        CacheIterator[hikari.guilds.Member]
            An async iterator of the members stored in the cache for the
            specified user.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """


class MessageCache(Resource, abc.ABC):
    """The traits of a implementation which supports a message cache."""

    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    async def clear_messages(self) -> None:
        """Empty the messages cache store.

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """

    @abc.abstractmethod
    async def delete_message(self, message_id: hikari.Snowflakeish, /) -> None:
        """Remove a message from the cache.

        .. note::
            Delete methods do not raise `sake.errors.CacheMissError` when the
            targeted entity doesn't exist.

        Parameters
        ----------
        message_id : hikari.snowflakes.Snowflakeish
            The ID of the message to remove from the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """

    @abc.abstractmethod
    async def get_message(self, message_id: hikari.Snowflakeish, /) -> hikari.Message:
        """Get a message from the cache.

        Parameters
        ----------
        message_id : hikari.snowflakes.Snowflakeish
            The ID of the message to get from the cache.

        Returns
        -------
        hikari.messages.Message
            The object of the message fetched from the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.EntryNotFound
            Raised when the targeted entity wasn't found.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """

    @abc.abstractmethod
    def iter_messages(self) -> CacheIterator[hikari.Message]:
        """Iterate over the messages stored in the cache.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Returns
        -------
        CacheIterator[hikari.messages.Message]
            An async iterator of the messages stored in the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """


class RefMessageCache(MessageCache, abc.ABC):
    """The traits of a implementation which supports a referential message cache."""

    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    async def clear_messages_for_author(self, user_id: hikari.Snowflakeish, /) -> None:
        """Remove the messages cached for a specific author.

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Parameters
        ----------
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the user to remove the messages cached for.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """

    @abc.abstractmethod
    async def clear_messages_for_channel(self, channel_id: hikari.Snowflakeish, /) -> None:
        """Remove the messages cached for a specific channel.

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Parameters
        ----------
        channel_id : hikari.snowflakes.Snowflakeish
            The ID of the channel to remove the messages cached for.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """

    @abc.abstractmethod
    async def clear_messages_for_guild(self, guild_id: hikari.Snowflakeish, /) -> None:
        """Remove the messages cached for a specific guild.

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove the messages cached for.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """

    @abc.abstractmethod
    def iter_messages_for_author(self, user_id: hikari.Snowflakeish, /) -> CacheIterator[hikari.Message]:
        """Iterate over the messages stored in the cache for a specific author.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Parameters
        ----------
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the user to iterate over the messages cached for them.

        Returns
        -------
        CacheIterator[hikari.messages.Message]
            An async iterator of the messages stored in the cache for the
            specified user.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """

    @abc.abstractmethod
    def iter_message_for_channel(self, channel_id: hikari.Snowflakeish, /) -> CacheIterator[hikari.Message]:
        """Iterate over the messages stored in the cache for a specific channel.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Parameters
        ----------
        channel_id : hikari.snowflakes.Snowflakeish
            The ID of the channel to iterate over the messages cached for them.

        Returns
        -------
        CacheIterator[hikari.messages.Message]
            An async iterator of the messages stored in the cache for the
            specified channel.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """

    @abc.abstractmethod
    def iter_messages_for_guild(self, guild_id: hikari.Snowflakeish, /) -> CacheIterator[hikari.Message]:
        """Iterate over the messages stored in the cache for a specific guild.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to iterate over the messages cached for them.

        Returns
        -------
        CacheIterator[hikari.messages.Message]
            An async iterator of the messages stored in the cache for the
            specified guild.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """


class PresenceCache(Resource, abc.ABC):
    """The traits of a implementation which supports a presence cache."""

    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    async def clear_presences(self) -> None:
        """Empty the presences cache store.

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """

    @abc.abstractmethod
    async def delete_presence(self, guild_id: hikari.Snowflakeish, user_id: hikari.Snowflakeish, /) -> None:
        """Remove a presence from the cache.

        .. note::
            Delete methods do not raise `sake.errors.CacheMissError` when the
            targeted entity doesn't exist.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove a cached presence for.
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the user to remove a cached presence for.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """

    @abc.abstractmethod
    async def get_presence(
        self, guild_id: hikari.Snowflakeish, user_id: hikari.Snowflakeish, /
    ) -> hikari.MemberPresence:
        """Get a presence from the cache.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to get a cached presence for.
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the user to get a cached presence for.

        Returns
        -------
        hikari.presences.MemberPresence
            The object of the presence fetched from the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.EntryNotFound
            Raised when the targeted entity wasn't found.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """

    @abc.abstractmethod
    def iter_presences(
        self,
    ) -> CacheIterator[hikari.MemberPresence]:
        """Iterate over the presences stored in the cache.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Returns
        -------
        CacheIterator[hikari.presences.MemberPresence]
            An async iterator of the presences stored in the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """


class RefPresenceCache(PresenceCache, abc.ABC):
    """The traits of a implementation which supports a referential presence cache."""

    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    async def clear_presences_for_guild(self, guild_id: hikari.Snowflakeish, /) -> None:
        """Remove the presences cached for a specific guild.

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove the cached presences for.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """

    @abc.abstractmethod
    async def clear_presences_for_user(self, user_id: hikari.Snowflakeish, /) -> None:
        """Remove the presences cached for a specific user.

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Parameters
        ----------
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the user to remove the cached presences for.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """

    @abc.abstractmethod
    def iter_presences_for_guild(self, guild_id: hikari.Snowflakeish, /) -> CacheIterator[hikari.MemberPresence]:
        """Iterate over the presences stored in the cache for a specific guild.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to iterate over the cached presences for.

        Returns
        -------
        CacheIterator[hikari.presences.MemberPresence]
            An async iterator of the presences stored in the cache for the
            specified guild.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """

    @abc.abstractmethod
    def iter_presences_for_user(self, user_id: hikari.Snowflakeish, /) -> CacheIterator[hikari.MemberPresence]:
        """Iterate over the presences stored in the cache for a specific user.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Parameters
        ----------
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the user to iterate over the cached presences for.

        Returns
        -------
        CacheIterator[hikari.presences.MemberPresence]
            An async iterator of the presences stored in the cache for the
            specified user.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """


class RoleCache(Resource, abc.ABC):
    """The traits of a implementation which supports a role cache."""

    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    async def clear_roles(self) -> None:
        """Empty the roles cache store.

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """

    @abc.abstractmethod
    async def delete_role(self, role_id: hikari.Snowflakeish, /) -> None:
        """Remove a role from the cache.

        .. note::
            Delete methods do not raise `sake.errors.CacheMissError` when the
            targeted entity doesn't exist.

        Parameters
        ----------
        role_id : hikari.snowflakes.Snowflakeish
            The ID of the role to remove from the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """

    @abc.abstractmethod
    async def get_role(self, role_id: hikari.Snowflakeish, /) -> hikari.Role:
        """Get a role from the cache.

        Parameters
        ----------
        role_id : hikari.snowflakes.Snowflakeish
            The ID of the role to get from the cache.

        Returns
        -------
        hikari.guilds.Role
            The object of the role fetched from the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.EntryNotFound
            Raised when the targeted entity wasn't found.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """

    @abc.abstractmethod
    def iter_roles(self) -> CacheIterator[hikari.Role]:
        """Iterate over the roles stored in the cache.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Returns
        -------
        CacheIterator[hikari.guilds.Role]
            An async iterator of the roles stored in the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """


class RefRoleCache(RoleCache, abc.ABC):
    """The traits of a implementation which supports a referential role cache."""

    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    async def clear_roles_for_guild(self, guild_id: hikari.Snowflakeish, /) -> None:
        """Remove the roles cached for a specific guild.

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove the cached roles for.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """

    @abc.abstractmethod
    def iter_roles_for_guild(self, guild_id: hikari.Snowflakeish, /) -> CacheIterator[hikari.Role]:
        """Iterate over the roles stored in the cache.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to get the roles cached for.

        Returns
        -------
        CacheIterator[hikari.guilds.Role]
            An async iterator of the roles stored in the cache for the
            specified guild.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """


class UserCache(Resource, abc.ABC):
    """The traits of a cache implementation which supports a user cache.

    .. note::
        Unlike other resources, user doesn't have any events which
        directly update it and may only be updated through event
        listeners when resources which reference it are also included.
    """

    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    async def clear_users(self) -> None:
        """Empty the users cache store.

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """

    @abc.abstractmethod
    async def delete_user(self, user_id: hikari.Snowflakeish, /) -> None:
        """Remove a user from the cache.

        .. note::
            Delete methods do not raise `sake.errors.CacheMissError` when the
            targeted entity doesn't exist.

        Parameters
        ----------
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the user to remove from the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """

    @abc.abstractmethod
    async def get_user(self, user_id: hikari.Snowflakeish, /) -> hikari.User:
        """Get a user from the cache.

        Parameters
        ----------
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the user to get from the cache.

        Returns
        -------
        hikari.users.User
            The object of the user fetched from the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.EntryNotFound
            Raised when the targeted entity wasn't found.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """

    @abc.abstractmethod
    def iter_users(self) -> CacheIterator[hikari.User]:
        """Iterate over the users stored in the cache.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Returns
        -------
        CacheIterator[hikari.users.User]
            An async iterator of the users stored in the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """


RefUserCache = UserCache


class VoiceStateCache(Resource, abc.ABC):
    """The traits of a implementation which supports a voice state cache."""

    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    async def clear_voice_states(self) -> None:
        """Empty the voice states cache store.

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """

    @abc.abstractmethod
    async def delete_voice_state(self, guild_id: hikari.Snowflakeish, user_id: hikari.Snowflakeish, /) -> None:
        """Remove a voice state from the cache.

        .. note::
            Delete methods do not raise `sake.errors.CacheMissError` when the
            targeted entity doesn't exist.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove a cached voice state for.
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the user to remove a cached voice state for.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """

    @abc.abstractmethod
    async def get_voice_state(
        self, guild_id: hikari.Snowflakeish, user_id: hikari.Snowflakeish, /
    ) -> hikari.VoiceState:
        """Get a voice state from the cache.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to get a cached voice state for.
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the user to get a cached voice state for.

        Returns
        -------
        hikari.voices.VoiceState
            The object of the voice state fetched from the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.EntryNotFound
            Raised when the targeted entity wasn't found.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """

    @abc.abstractmethod
    def iter_voice_states(self) -> CacheIterator[hikari.VoiceState]:
        """Iterate over the voice states stored in the cache.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Returns
        -------
        CacheIterator[hikari.voices.VoiceState]
            An async iterator of the voice states stored in the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """


class RefVoiceStateCache(VoiceStateCache, abc.ABC):
    """The traits of a implementation which supports a referential voice state cache."""

    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    async def clear_voice_states_for_channel(self, channel_id: hikari.Snowflakeish, /) -> None:
        """Remove the voice states cached for a specified channel.

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Parameters
        ----------
        channel_id : hikari.snowflakes.Snowflakeish
            The ID of the channel to remove the voice states cached for.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """

    @abc.abstractmethod
    async def clear_voice_states_for_guild(self, guild_id: hikari.Snowflakeish, /) -> None:
        """Remove the voice states cached for a specified guild.

        .. note::
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove the voice states cached for.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """

    @abc.abstractmethod
    def iter_voice_states_for_channel(self, channel_id: hikari.Snowflakeish, /) -> CacheIterator[hikari.VoiceState]:
        """Iterate over the voice states stored in the cache for a specific channel.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Parameters
        ----------
        channel_id : hikari.snowflakes.Snowflakeish
            The ID of the channel to iterate over the voice states cached for.

        Returns
        -------
        CacheIterator[hikari.voices.VoiceState]
            An async iterator of the voice states stored in the cache for the
            specified channel.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """

    @abc.abstractmethod
    def iter_voice_states_for_guild(self, guild_id: hikari.Snowflakeish, /) -> CacheIterator[hikari.VoiceState]:
        """Iterate over the voice states stored in the cache for a specific guild.

        .. note::
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to iterate over the voice states cached for.

        Returns
        -------
        CacheIterator[hikari.voices.VoiceState]
            An async iterator of the voice states stored in the cache for the
            specified guild.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        sake.errors.InvalidDataFound
            Raised when the data retrieved from the backend datastore was
            either invalid for this implementation or corrupt.
            This may be a sign of multiple sake versions or implementations
            being used with the same backend store.
        """


class Cache(
    GuildCache,
    EmojiCache,
    GuildChannelCache,
    InviteCache,
    MeCache,
    MemberCache,
    MessageCache,
    PresenceCache,
    RoleCache,
    UserCache,
    VoiceStateCache,
    abc.ABC,
):
    """Protocol of a cache which implements all the defined resources."""

    __slots__: typing.Sequence[str] = ()


class RefCache(
    Cache,
    RefGuildCache,
    RefEmojiCache,
    RefGuildChannelCache,
    RefInviteCache,
    RefMeCache,
    RefMemberCache,
    RefMessageCache,
    RefPresenceCache,
    RefRoleCache,
    RefUserCache,
    RefVoiceStateCache,
    abc.ABC,
):
    """Protocol of a cache which implements all the defined reference resources."""

    __slots__: typing.Sequence[str] = ()
