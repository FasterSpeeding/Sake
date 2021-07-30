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
"""Protocols and abstract classes for the cache resources defined by this standard.

!!! note
    Unlike the abstract classes defined here, there is no guarantee that the
    protocols defined here will be included in the MRO of the classes which
    implement them.
"""

from __future__ import annotations

__all__: typing.Final[typing.Sequence[str]] = [
    "Cache",
    "CacheIterator",
    "PrefixCache",
    "EmojiCache",
    "GuildCache",
    "GuildChannelCache",
    "IntegrationCache",
    "InviteCache",
    "MeCache",
    "MemberCache",
    "MessageCache",
    "PresenceCache",
    "Resource",
    "RefCache",
    "RefPrefixCache",
    "RefEmojiCache",
    "RefGuildCache",
    "RefGuildChannelCache",
    "RefIntegrationCache",
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

from hikari import iterators

if typing.TYPE_CHECKING:
    from hikari import channels
    from hikari import emojis
    from hikari import guilds
    from hikari import invites
    from hikari import messages
    from hikari import presences
    from hikari import snowflakes
    from hikari import users
    from hikari import voices


ValueT = typing.TypeVar("ValueT")


# TODO: should we explicitly raise a ValueError if the wrong object is passed to a set method?
class CacheIterator(iterators.LazyIterator[ValueT], abc.ABC):  # TODO: cascade arguments on referential traits?
    """A asynchronous iterator of entries within a defined cache store."""

    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    async def len(self) -> typing.Optional[int]:
        """Get the count of entries that this iterator covers.

        !!! note
            Unlike `hikari.iterators.LazyIterator.count`, this will not exhaust
            the iterator and may return different values as entries are added
            and removed from the cache.

        Returns
        -------
        int
            The count of entries that this iterator covers as of the call.
        """
        raise NotImplementedError


@typing.runtime_checkable
class Resource(typing.Protocol):
    """The basic interface which all cache resources should implement."""

    __slots__: typing.Sequence[str] = ()

    def subscribe_listeners(self) -> None:
        """Register this resource's internal listener to a dispatcher aware app.

        !!! note
            Dependent on the implementation, this may be called by
            `Resource.open` and may raise a `builtins.TypeError`if called
            when this resource's listeners have already been registered.

        !!! note
            If the event dispatcher isn't provided during initialisation then
            this method will do nothing.
        """
        raise NotImplementedError

    def unsubscribe_listeners(self) -> None:
        """Unregister this resource's internal listener to a dispatcher aware app.

        !!! note
            Dependent on the implementation, this may be called by
            `Resource.close` and may raise a `builtins.TypeError`if called
            when this resource's listeners haven't been registered yet.

        !!! note
            If the event dispatcher isn't provided during initialisation then
            this method will do nothing.
        """
        raise NotImplementedError

    async def open(self) -> None:
        """Startup the resource(s) and allow them to connect to their relevant backend(s).

        !!! note
            This should implicitly call `Resource.subscribe_listeners`.

        !!! note
            This should pass without raising if called on an already opened
            resource.
        """
        raise NotImplementedError  # TODO: connection errors.

    async def close(self) -> None:
        """Close the resource(s) and allow them to disconnect from their relevant backend(s).

        !!! note
            This should implicitly call `Resource.unsubscribe_listeners`.

        !!! note
            This should pass without raising if called on an already closed
            resource.
        """
        raise NotImplementedError


@typing.runtime_checkable
class PrefixCache(Resource, typing.Protocol):
    """The traits of a implementation which supports a prefix cache."""

    __slots__: typing.Sequence[str] = ()

    async def clear_prefixes(self) -> None:
        """Empty the prefix cache store.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    async def clear_prefixes_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        """Clear prefixes for a specific guild.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to clear the prefixes.

        !!! note
            Delete methods do not raise `sake.errors.EntryNotFound` when the
            targeted entity doesn't exist.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """
        raise NotImplementedError
    
    async def delete_prefixes(self, guild_id: snowflakes.Snowflakeish, prefix: str, /, *prefixes: str) -> None:
        """Delete prefixes from the cache.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to clear the prefixes.
        prefix : str
            The first prefix to delete from the cache.

        !!! note
            Delete methods do not raise `sake.errors.EntryNotFound` when the
            targeted entity doesn't exist.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """
        raise NotImplementedError

    async def get_prefixes(self, guild_id: snowflakes.Snowflakeish, /) -> typing.List[str]:
        """Get prefixes from the cache.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to get the prefixes from the cache.

        Returns
        -------
        typing.List[str]
            The list of prefixes fetched from the cache.

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
        raise NotImplementedError

    def iter_prefixes(self) -> CacheIterator[typing.List[str]]:
        """Iterate over the prefixes stored in the cache.

        Returns
        -------
        CacheIterator[typing.List[str]]
            An async iterator of the prefixes stored in the cache.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError
    
    async def add_prefixes(self, guild_id: snowflakes.Snowflakeish, prefix: str, /, *prefixes: str) -> None:
        """Add prefixes to the cache.

        Parameters
        ----------
        guild_id : snowflakes.Snowflakeish
            The ID of the guild to store the prefix for in the cache.
        prefix : str
            The first prefix to add to the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    async def set_prefixes(self, guild_id: snowflakes.Snowflakeish, prefixes: typing.Iterable[str], /) -> None:
        """Set prefixes for a guild.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to store the prefixes in the cache.
        prefixes : typing.Iterable[str]
            An iterable of prefixes to store in the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError


RefPrefixCache = PrefixCache


@typing.runtime_checkable
class EmojiCache(Resource, typing.Protocol):
    """The traits of a implementation which supports a emoji cache."""

    __slots__: typing.Sequence[str] = ()

    async def clear_emojis(self) -> None:
        """Empty the emoji cache store.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    async def delete_emoji(self, emoji_id: snowflakes.Snowflakeish, /) -> None:
        """Remove an emoji from the cache.

        Parameters
        ----------
        emoji_id : hikari.snowflakes.Snowflakeish
            The ID of the emoji to remove from the cache.

        !!! note
            Delete methods do not raise `sake.errors.EntryNotFound` when the
            targeted entity doesn't exist.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """
        raise NotImplementedError

    async def get_emoji(self, emoji_id: snowflakes.Snowflakeish, /) -> emojis.KnownCustomEmoji:
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
        raise NotImplementedError

    def iter_emojis(self) -> CacheIterator[emojis.KnownCustomEmoji]:
        """Iterate over the emojis stored in the cache.

        Returns
        -------
        CacheIterator[hikari.emojis.KnownCustomEmoji]
            An async iterator of the emojis stored in the cache.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError

    async def set_emoji(self, emoji: emojis.KnownCustomEmoji, /) -> None:
        """Add an emoji to the cache.

        Parameters
        ----------
        emoji : hikari.emojis.KnownCustomEmoji

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError


@typing.runtime_checkable
class RefEmojiCache(EmojiCache, typing.Protocol):
    """The traits of a implementation which supports a referencial emoji cache."""

    __slots__: typing.Sequence[str] = ()

    async def clear_emojis_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        """Remove emojis belonging to a specific guild from the cache.

        !!! note
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
        raise NotImplementedError

    def iter_emojis_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> CacheIterator[emojis.KnownCustomEmoji]:
        """Iterate over the emojis stored in the cache for a specific guild.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to iterate over the emojis cached for.

        Returns
        -------
        CacheIterator[hikari.emojis.KnownCustomEmoji]
            An async iterator of the emojis stored in the cache for the
            specified guild.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError


@typing.runtime_checkable
class GuildCache(Resource, typing.Protocol):
    """The traits of a implementation which supports a guild cache."""

    __slots__: typing.Sequence[str] = ()

    async def clear_guilds(self) -> None:
        """Empty the guild cache store.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    async def delete_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        """Remove a guild from the cache.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove from the cache.

        !!! note
            Delete methods do not raise `sake.errors.EntryNotFound` when the
            targeted entity doesn't exist.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """
        raise NotImplementedError

    async def get_guild(self, guild_id: snowflakes.Snowflakeish, /) -> guilds.GatewayGuild:
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
        raise NotImplementedError

    def iter_guilds(self) -> CacheIterator[guilds.GatewayGuild]:
        """Iterate over the guilds stored in the cache.

        Returns
        -------
        CacheIterator[hikari.guilds.GatewayGuild]
            An async iterator of the guilds stored in the cache.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError

    async def set_guild(self, guild: guilds.GatewayGuild, /) -> None:
        """Add a guild to the cache.

        Parameters
        ----------
        guild : hikari.guilds.GatewayGuild

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError


RefGuildCache = GuildCache


@typing.runtime_checkable
class GuildChannelCache(Resource, typing.Protocol):
    """The traits of a implementation which supports a guild channel cache."""

    __slots__: typing.Sequence[str] = ()

    async def clear_guild_channels(self) -> None:
        """Empty the guild channel cache store.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    async def delete_guild_channel(self, channel_id: snowflakes.Snowflakeish, /) -> None:
        """Remove a guild guild channel from the cache.

        Parameters
        ----------
        channel_id : hikari.snowflakes.Snowflakeish
            The ID of the guild channel to remove from the cache.

        !!! note
            Delete methods do not raise `sake.errors.EntryNotFound` when the
            targeted entity doesn't exist.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """
        raise NotImplementedError

    async def get_guild_channel(self, channel_id: snowflakes.Snowflakeish, /) -> channels.GuildChannel:
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
        raise NotImplementedError

    def iter_guild_channels(self) -> CacheIterator[channels.GuildChannel]:
        """Iterate over the guild channels stored in the cache.

        Returns
        -------
        CacheIterator[hikari.channels.GuildChannel]
            An async iterator of the guild channels stored in the cache.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError

    async def set_guild_channel(self, channel: channels.GuildChannel, /) -> None:
        """Add a guild channel to the cache.

        Parameters
        ----------
        channel : hikari.channels.GuildChannel

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError


@typing.runtime_checkable
class RefGuildChannelCache(GuildChannelCache, typing.Protocol):
    """The traits of a implementation which supports a referencial guild channel cache."""

    __slots__: typing.Sequence[str] = ()

    async def clear_guild_channels_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        """Remove the guild channel cache store for the specified guild.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove the cached channels for.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    def iter_guild_channels_for_guild(
        self, guild_id: snowflakes.Snowflakeish, /
    ) -> CacheIterator[channels.GuildChannel]:
        """Iterate over the guild channels stored in the cache for a specific guild.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to iterate over the guild channels cached for it.

        Returns
        -------
        CacheIterator[hikari.channels.GuildChannel]
            An async iterator of the guild channels stored in the cache for the
            specified guild.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError


@typing.runtime_checkable
class IntegrationCache(Resource, typing.Protocol):
    """The traits of a implementation which supports a integration cache."""

    async def clear_integrations(self) -> None:
        """Empty the integration cache store.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    async def delete_integration(self, integration_id: snowflakes.Snowflakeish, /) -> None:
        """Remove an integration channel from the cache.

        Parameters
        ----------
        integration_id : hikari.snowflakes.Snowflakeish
            The ID of the integration to remove from the cache.

        !!! note
            Delete methods do not raise `sake.errors.EntryNotFound` when the
            targeted entity doesn't exist.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """
        raise NotImplementedError

    async def get_integration(self, integration_id: snowflakes.Snowflakeish, /) -> guilds.Integration:
        """Get an integration from the cache.

        Parameters
        ----------
        integration_id : hikari.snowflakes.Snowflakeish
            The ID of the integration to get from the cache.

        Returns
        -------
        hikari.guilds.Integration
            The object of the integration fetched from the cache.

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
        raise NotImplementedError

    def iter_integrations(self) -> CacheIterator[guilds.Integration]:
        """Iterate over the integrations stored in the cache.

        Returns
        -------
        CacheIterator[hikari.guilds.Integration]
            An async iterator of the integrations stored in the cache.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError

    async def set_integration(self, integration: guilds.Integration, /) -> None:
        """Add an integration to the cache.

        Parameters
        ----------
        integration : hikari.guilds.Integration
            The integration to add to the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError


@typing.runtime_checkable
class RefIntegrationCache(IntegrationCache, typing.Protocol):
    """The traits of a implementation which supports a referencial invite cache."""

    async def clear_integrations_for_application(self, application_id: snowflakes.Snowflakeish, /) -> None:
        """Remove the integrations cached for an application.

        Parameters
        ----------
        application_id : hikari.snowflakes.Snowflakeish
            The ID of the application to remove cached integrations for.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    async def clear_integrations_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        """Remove the integrations cached for a guild.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove cached integrations for.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    async def delete_integration_by_application(
        self, guild_id: snowflakes.Snowflakeish, application_id: snowflakes.Snowflakeish, /
    ) -> None:
        """Remove an integration channel from the cache by the ID of it's application.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove an integration for.
        application_id : hikari.snowflakes.Snowflakeish
            The ID of the application to remove an integration for.

        !!! note
            Delete methods do not raise `sake.errors.EntryNotFound` when the
            targeted entity doesn't exist.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """
        raise NotImplementedError

    async def get_integration_by_application(
        self, guild_id: snowflakes.Snowflakeish, application_id: snowflakes.Snowflakeish, /
    ) -> guilds.Integration:
        """Get an integration from the cache by the ID of it's application.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to get an integration for.
        application_id : hikari.snowflakes.Snowflakeish
            The ID of the application to get an integration for.

        Returns
        -------
        hikari.guilds.Integration
            The object of the integration fetched from the cache.

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
        raise NotImplementedError

    def iter_integrations_for_application(
        self, application_id: snowflakes.Snowflakeish, /
    ) -> CacheIterator[guilds.Integration]:
        """Iterate over the integrations stored in the cache for a specific application.

        Parameters
        ----------
        application_id : hikari.snowflakes.Snowflakeish
            The ID of the application to iterate over the cached integrations for.

        Returns
        -------
        CacheIterator[hikari.guilds.Integration]
            An async iterator of the integrations stored in the cache for the
            specified application.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError

    def iter_integrations_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> CacheIterator[guilds.Integration]:
        """Iterate over the integrations stored in the cache for a specific guild.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to iterate over the cached integrations for.

        Returns
        -------
        CacheIterator[hikari.guilds.Integration]
            An async iterator of the integrations stored in the cache for the
            specified application.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError


@typing.runtime_checkable
class InviteCache(Resource, typing.Protocol):
    """The traits of a implementation which supports a invite cache."""

    __slots__: typing.Sequence[str] = ()

    async def clear_invites(self) -> None:
        """Empty the invites cache store.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    async def delete_invite(self, invite_code: str, /) -> None:
        """Remove an invite channel from the cache.

        Parameters
        ----------
        invite_code : builtins.str
            The code of the invite to remove from the cache.

        !!! note
            Delete methods do not raise `sake.errors.EntryNotFound` when the
            targeted entity doesn't exist.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """
        raise NotImplementedError

    async def get_invite(self, invite_code: str, /) -> invites.InviteWithMetadata:
        """Get an invite from the cache.

        Parameters
        ----------
        invite_code : builtins.str
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
        raise NotImplementedError

    def iter_invites(self) -> CacheIterator[invites.InviteWithMetadata]:
        """Iterate over the invites stored in the cache.

        Returns
        -------
        CacheIterator[hikari.invites.InviteWithMetadata]
            An async iterator of the invites stored in the cache.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError

    async def set_invite(self, invite: invites.InviteWithMetadata, /) -> None:
        """Add an invite to the cache.

        Parameters
        ----------
        invite : hikari.invites.InviteWithMetadata
            The invite to add to the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError


@typing.runtime_checkable
class RefInviteCache(InviteCache, typing.Protocol):
    """The traits of a implementation which supports a referencial invite cache."""

    __slots__: typing.Sequence[str] = ()

    async def clear_invites_for_channel(self, channel_id: snowflakes.Snowflakeish, /) -> None:
        """Remove invites cached for a specific channel..

        Parameters
        ----------
        channel_id : hikari.snowflakes.Snowflakeish
            The ID of the channel to remove the invites cached for it.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    async def clear_invites_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        """Remove invites cached for a specific guild.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove the invites cached for it.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    def iter_invites_for_channel(
        self, channel_id: snowflakes.Snowflakeish, /
    ) -> CacheIterator[invites.InviteWithMetadata]:
        """Iterate over the invites stored in the cache for a specific channel.

        Parameters
        ----------
        channel_id : hikari.snowflakes.Snowflakeish
            The ID of the channel to iterate over the invites cached for.

        Returns
        -------
        CacheIterator[hikari.invites.InviteWithMetadata]
            An async iterator of the invites stored in the cache for the
            specified channel.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError

    def iter_invites_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> CacheIterator[invites.InviteWithMetadata]:
        """Iterate over the invites stored in the cache for a specific guild.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to iterate over the invites cached for.

        Returns
        -------
        CacheIterator[hikari.invites.InviteWithMetadata]
            An async iterator of the invites stored in the cache for the
            specified guild.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError


@typing.runtime_checkable  # TODO: rename to OwnUserCache
class MeCache(Resource, typing.Protocol):
    """The traits of a implementation which supports a own user cache."""

    __slots__: typing.Sequence[str] = ()

    async def delete_me(self) -> None:
        """Remove the cached own user entry.

        !!! note
            Delete methods do not raise `sake.errors.EntryNotFound` when the
            targeted entity doesn't exist.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """
        raise NotImplementedError

    async def get_me(self) -> users.OwnUser:
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
        raise NotImplementedError

    async def set_me(self, me: users.OwnUser, /) -> None:
        """Set the own user entry in the cache.

        Parameters
        ----------
        me : hikari.users.OwnUser
            The own user object to set in the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError


RefMeCache = MeCache


@typing.runtime_checkable
class MemberCache(Resource, typing.Protocol):
    """The traits of a implementation which supports a member cache."""

    __slots__: typing.Sequence[str] = ()

    async def clear_members(self) -> None:
        """Empty the members cache store.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    async def delete_member(self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /) -> None:
        """Remove a member from the cache.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove a cached member for.
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the user to remove a cached member for.

        !!! note
            Delete methods do not raise `sake.errors.EntryNotFound` when the
            targeted entity doesn't exist.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """
        raise NotImplementedError

    async def get_member(self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /) -> guilds.Member:
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
        raise NotImplementedError

    def iter_members(
        self,
    ) -> CacheIterator[guilds.Member]:
        """Iterate over the members stored in the cache.

        Returns
        -------
        CacheIterator[hikari.guilds.Member]
            An async iterator of the members stored in the cache.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError

    async def set_member(self, member: guilds.Member, /) -> None:
        """Add a member to the cache.

        Parameters
        ----------
        member : hikari.guilds.Member
            The member to add to the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError


@typing.runtime_checkable
class RefMemberCache(MemberCache, typing.Protocol):
    """The traits of a implementation which supports a referencial member cache."""

    __slots__: typing.Sequence[str] = ()

    async def clear_members_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        """Remove the members cached for a specific guild.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove the cached members for.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    async def clear_members_for_user(self, user_id: snowflakes.Snowflakeish, /) -> None:
        """Remove the members cached for a specific user.

        Parameters
        ----------
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the user to remove the cached members for.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    def iter_members_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> CacheIterator[guilds.Member]:
        """Iterate over the members stored in the cache for a specific guild.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to get the members cached for.

        Returns
        -------
        CacheIterator[hikari.guilds.Member]
            An async iterator of the members stored in the cache for the
            specified guild.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError

    def iter_members_for_user(self, user_id: snowflakes.Snowflakeish, /) -> CacheIterator[guilds.Member]:
        """Iterate over the members stored in the cache for a specific user.

        Parameters
        ----------
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to get the user cached for.

        Returns
        -------
        CacheIterator[hikari.guilds.Member]
            An async iterator of the members stored in the cache for the
            specified user.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError


@typing.runtime_checkable
class MessageCache(Resource, typing.Protocol):
    """The traits of a implementation which supports a message cache."""

    __slots__: typing.Sequence[str] = ()

    async def clear_messages(self) -> None:
        """Empty the messages cache store.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    async def delete_message(self, message_id: snowflakes.Snowflakeish, /) -> None:
        """Remove a message from the cache.

        Parameters
        ----------
        message_id : hikari.snowflakes.Snowflakeish
            The ID of the message to remove from the cache.

        !!! note
            Delete methods do not raise `sake.errors.EntryNotFound` when the
            targeted entity doesn't exist.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """
        raise NotImplementedError

    async def get_message(self, message_id: snowflakes.Snowflakeish, /) -> messages.Message:
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
        raise NotImplementedError

    def iter_messages(self) -> CacheIterator[messages.Message]:
        """Iterate over the messages stored in the cache.

        Returns
        -------
        CacheIterator[hikari.messages.Message]
            An async iterator of the messages stored in the cache.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError

    async def set_message(self, message: messages.Message, /) -> None:
        """Add a message to the cache.

        Parameters
        ----------
        message : hikari.messages.Message
            The message to add to the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    async def update_message(self, message: messages.PartialMessage, /) -> bool:
        """Update a message in the cache using a partial message object.

        !!! note
            This won't be able to do anything unless an older version of the
            passed message is already cached.

        Parameters
        ----------
        message : messages.PartialMessage
            The partial object of the message to update in the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        # This is a special case method for handling the partial message updates we get
        raise NotImplementedError


@typing.runtime_checkable
class RefMessageCache(MessageCache, typing.Protocol):
    """The traits of a implementation which supports a referencial message cache."""

    __slots__: typing.Sequence[str] = ()

    async def clear_messages_for_author(self, user_id: snowflakes.Snowflakeish, /) -> None:
        """Remove the messages cached for a specific author.

        Parameters
        ----------
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the user to remove the messages cached for.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    async def clear_messages_for_channel(self, channel_id: snowflakes.Snowflakeish, /) -> None:
        """Remove the messages cached for a specific channel.

        Parameters
        ----------
        channel_id : hikari.snowflakes.Snowflakeish
            The ID of the channel to remove the messages cached for.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    async def clear_messages_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        """Remove the messages cached for a specific guild.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove the messages cached for.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    def iter_messages_for_author(self, user_id: snowflakes.Snowflakeish, /) -> CacheIterator[messages.Message]:
        """Iterate over the messages stored in the cache for a specific author.

        Parameters
        ----------
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the user to iterate over the messages cached for them.

        Returns
        -------
        CacheIterator[hikari.messages.Message]
            An async iterator of the messages stored in the cache for the
            specified user.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError

    def iter_message_for_channel(self, channel_id: snowflakes.Snowflakeish, /) -> CacheIterator[messages.Message]:
        """Iterate over the messages stored in the cache for a specific channel.

        Parameters
        ----------
        channel_id : hikari.snowflakes.Snowflakeish
            The ID of the channel to iterate over the messages cached for them.

        Returns
        -------
        CacheIterator[hikari.messages.Message]
            An async iterator of the messages stored in the cache for the
            specified channel.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError

    def iter_messages_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> CacheIterator[messages.Message]:
        """Iterate over the messages stored in the cache for a specific guild.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to iterate over the messages cached for them.

        Returns
        -------
        CacheIterator[hikari.messages.Message]
            An async iterator of the messages stored in the cache for the
            specified guild.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError


@typing.runtime_checkable
class PresenceCache(Resource, typing.Protocol):
    """The traits of a implementation which supports a presence cache."""

    __slots__: typing.Sequence[str] = ()

    async def clear_presences(self) -> None:
        """Empty the presences cache store.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    async def delete_presence(self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /) -> None:
        """Remove a presence from the cache.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove a cached presence for.
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the user to remove a cached presence for.

        !!! note
            Delete methods do not raise `sake.errors.EntryNotFound` when the
            targeted entity doesn't exist.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """
        raise NotImplementedError

    async def get_presence(
        self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /
    ) -> presences.MemberPresence:
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
        raise NotImplementedError

    def iter_presences(
        self,
    ) -> CacheIterator[presences.MemberPresence]:
        """Iterate over the presences stored in the cache.

        Returns
        -------
        CacheIterator[hikari.presences.MemberPresence]
            An async iterator of the presences stored in the cache.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError

    async def set_presence(self, presence: presences.MemberPresence, /) -> None:
        """Add a presence to the cache.

        Parameters
        ----------
        presence : hikari.presences.MemberPresence
            The presence to add to the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError


@typing.runtime_checkable
class RefPresenceCache(PresenceCache, typing.Protocol):
    """The traits of a implementation which supports a referencial presence cache."""

    __slots__: typing.Sequence[str] = ()

    async def clear_presences_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        """Remove the presences cached for a specific guild.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove the cached presences for.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    async def clear_presences_for_user(self, user_id: snowflakes.Snowflakeish, /) -> None:
        """Remove the presences cached for a specific user.

        Parameters
        ----------
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the user to remove the cached presences for.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    def iter_presences_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> CacheIterator[presences.MemberPresence]:
        """Iterate over the presences stored in the cache for a specific guild.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to iterate over the cached presences for.

        Returns
        -------
        CacheIterator[hikari.presences.MemberPresence]
            An async iterator of the presences stored in the cache for the
            specified guild.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError

    def iter_presences_for_user(self, user_id: snowflakes.Snowflakeish, /) -> CacheIterator[presences.MemberPresence]:
        """Iterate over the presences stored in the cache for a specific user.

        Parameters
        ----------
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the user to iterate over the cached presences for.

        Returns
        -------
        CacheIterator[hikari.presences.MemberPresence]
            An async iterator of the presences stored in the cache for the
            specified user.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError


@typing.runtime_checkable
class RoleCache(Resource, typing.Protocol):
    """The traits of a implementation which supports a role cache."""

    __slots__: typing.Sequence[str] = ()

    async def clear_roles(self) -> None:
        """Empty the roles cache store.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    async def delete_role(self, role_id: snowflakes.Snowflakeish, /) -> None:
        """Remove a role from the cache.

        Parameters
        ----------
        role_id : hikari.snowflakes.Snowflakeish
            The ID of the role to remove from the cache.

        !!! note
            Delete methods do not raise `sake.errors.EntryNotFound` when the
            targeted entity doesn't exist.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """
        raise NotImplementedError

    async def get_role(self, role_id: snowflakes.Snowflakeish, /) -> guilds.Role:
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
        raise NotImplementedError

    def iter_roles(self) -> CacheIterator[guilds.Role]:
        """Iterate over the roles stored in the cache.

        Returns
        -------
        CacheIterator[hikari.guilds.Role]
            An async iterator of the roles stored in the cache.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError

    async def set_role(self, role: guilds.Role, /) -> None:
        """Add a role to the cache.

        Parameters
        ----------
        role : hikari.guilds.Role
            The role to add to the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError


@typing.runtime_checkable
class RefRoleCache(RoleCache, typing.Protocol):
    """The traits of a implementation which supports a referencial role cache."""

    __slots__: typing.Sequence[str] = ()

    async def clear_roles_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        """Remove the roles cached for a specific guild.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove the cached roles for.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    def iter_roles_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> CacheIterator[guilds.Role]:
        """Iterate over the roles stored in the cache.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to get the roles cached for.

        Returns
        -------
        CacheIterator[hikari.guilds.Role]
            An async iterator of the roles stored in the cache for the
            specified guild.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError


@typing.runtime_checkable
class UserCache(Resource, typing.Protocol):
    """The traits of a cache implementation which supports a user cache.

    !!! note
        Unlike other resources, user doesn't have any events which
        directly update it and may only be updated through event
        listeners when resources which reference it are also included.
    """

    __slots__: typing.Sequence[str] = ()

    async def clear_users(self) -> None:  # TODO: cascade
        """Empty the users cache store.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    async def delete_user(self, user_id: snowflakes.Snowflakeish, /) -> None:
        """Remove a user from the cache.

        Parameters
        ----------
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the user to remove from the cache.

        !!! note
            Delete methods do not raise `sake.errors.EntryNotFound` when the
            targeted entity doesn't exist.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """
        raise NotImplementedError

    async def get_user(self, user_id: snowflakes.Snowflakeish, /) -> users.User:
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
        raise NotImplementedError

    def iter_users(self) -> CacheIterator[users.User]:
        """Iterate over the users stored in the cache.

        Returns
        -------
        CacheIterator[hikari.users.User]
            An async iterator of the users stored in the cache.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError

    async def set_user(self, user: users.User, /) -> None:
        """Add a user to the cache.

        Parameters
        ----------
        user : hikari.users.User
            The user to add to the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError


RefUserCache = UserCache


@typing.runtime_checkable
class VoiceStateCache(Resource, typing.Protocol):
    """The traits of a implementation which supports a voice state cache."""

    __slots__: typing.Sequence[str] = ()

    async def clear_voice_states(self) -> None:
        """Empty the voice states cache store.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    async def delete_voice_state(self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /) -> None:
        """Remove a voice state from the cache.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove a cached voice state for.
        user_id : hikari.snowflakes.Snowflakeish
            The ID of the user to remove a cached voice state for.

        !!! note
            Delete methods do not raise `sake.errors.EntryNotFound` when the
            targeted entity doesn't exist.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's backend.
            This may be a sign of underlying network or database issues.
        """
        raise NotImplementedError

    async def get_voice_state(
        self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /
    ) -> voices.VoiceState:
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
        raise NotImplementedError

    def iter_voice_states(self) -> CacheIterator[voices.VoiceState]:
        """Iterate over the voice states stored in the cache.

        Returns
        -------
        CacheIterator[hikari.voices.VoiceState]
            An async iterator of the voice states stored in the cache.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError

    async def set_voice_state(self, voice_state: voices.VoiceState, /) -> None:
        """Add a voice state to the cache.

        Parameters
        ----------
        voice_state : hikari.voices.VoiceState
            The voice state to add to the cache.

        Raises
        ------
        sake.errors.BackendError
            Raised when this failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError


@typing.runtime_checkable
class RefVoiceStateCache(VoiceStateCache, typing.Protocol):
    """The traits of a implementation which supports a referencial voice state cache."""

    __slots__: typing.Sequence[str] = ()  # TODO: for user?

    async def clear_voice_states_for_channel(self, channel_id: snowflakes.Snowflakeish, /) -> None:
        """Remove the voice states cached for a specified channel.

        Parameters
        ----------
        channel_id : hikari.snowflakes.Snowflakeish
            The ID of the channel to remove the voice states cached for.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    async def clear_voice_states_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        """Remove the voice states cached for a specified guild.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to remove the voice states cached for.

        !!! note
            There is no guarantee that this operation will be complete before
            the returned coroutine finishes.

        Raises
        ------
        sake.errors.BackendError
            Raised when this call failed to communicate with the cache's
            backend. This may be a sign of underlying network or database
            issues.
        """
        raise NotImplementedError

    def iter_voice_states_for_channel(self, channel_id: snowflakes.Snowflakeish, /) -> CacheIterator[voices.VoiceState]:
        """Iterate over the voice states stored in the cache for a specific channel.

        Parameters
        ----------
        channel_id : hikari.snowflakes.Snowflakeish
            The ID of the channel to iterate over the voice states cached for.

        Returns
        -------
        CacheIterator[hikari.voices.VoiceState]
            An async iterator of the voice states stored in the cache for the
            specified channel.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError

    def iter_voice_states_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> CacheIterator[voices.VoiceState]:
        """Iterate over the voice states stored in the cache for a specific guild.

        Parameters
        ----------
        guild_id : hikari.snowflakes.Snowflakeish
            The ID of the guild to iterate over the voice states cached for.

        Returns
        -------
        CacheIterator[hikari.voices.VoiceState]
            An async iterator of the voice states stored in the cache for the
            specified guild.

        !!! note
            Errors won't be raised by the initial call to this method but rather
            while iterating over the returned asynchronous iterator.

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
        raise NotImplementedError


@typing.runtime_checkable
class Cache(
    GuildCache,
    EmojiCache,
    GuildChannelCache,
    IntegrationCache,
    InviteCache,
    MeCache,
    MemberCache,
    MessageCache,
    PresenceCache,
    RoleCache,
    UserCache,
    VoiceStateCache,
    typing.Protocol,
):
    """Protocol of a cache which implements all the defined resources."""

    __slots__: typing.Sequence[str] = ()


@typing.runtime_checkable
class RefCache(
    Cache,
    RefGuildCache,
    RefEmojiCache,
    RefGuildChannelCache,
    RefIntegrationCache,
    RefInviteCache,
    RefMeCache,
    RefMemberCache,
    RefMessageCache,
    RefPresenceCache,
    RefRoleCache,
    RefUserCache,
    RefVoiceStateCache,
    typing.Protocol,
):
    """Protocol of a cache which implements all the defined reference resources."""

    __slots__: typing.Sequence[str] = ()
