"""Protocols and abstract classes for the cache resources defined by this standard.

!!! note
    Unlike the abstract classes defined here, there is no guarantee that the
    protocols defined here will be included in the MRO of the classes which
    implement them.

!!! note
    Mypy should be used to check for compatibility between protocols and
    their relevant implementations.
"""

from __future__ import annotations

__all__: typing.Final[typing.Sequence[str]] = [
    "Cache",
    "EmojiCache",
    "GuildCache",
    "GuildChannelCache",
    "IntegrationCache",
    "InviteCache",
    "MeCache",
    "MemberCache",
    "PresenceCache",
    "Resource",
    "RefCache",
    "RefEmojiCache",
    "RefGuildCache",
    "RefGuildChannelCache",
    "RefIntegrationCache",
    "RefInviteCache",
    "RefMeCache",
    "RefMemberCache",
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


class CacheIterator(iterators.LazyIterator[ValueT], abc.ABC):
    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    async def len(self) -> typing.Optional[int]:
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
class EmojiCache(Resource, typing.Protocol):
    __slots__: typing.Sequence[str] = ()

    async def clear_emojis(self) -> None:
        raise NotImplementedError

    async def delete_emoji(self, emoji_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    async def get_emoji(self, emoji_id: snowflakes.Snowflakeish, /) -> emojis.KnownCustomEmoji:
        raise NotImplementedError

    def iter_emojis(self) -> CacheIterator[emojis.KnownCustomEmoji]:
        raise NotImplementedError

    async def set_emoji(self, emoji: emojis.KnownCustomEmoji, /) -> None:
        raise NotImplementedError


@typing.runtime_checkable
class RefEmojiCache(EmojiCache, typing.Protocol):
    __slots__: typing.Sequence[str] = ()

    async def clear_emojis_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    def iter_emojis_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> CacheIterator[emojis.KnownCustomEmoji]:
        raise NotImplementedError


@typing.runtime_checkable
class GuildCache(Resource, typing.Protocol):
    __slots__: typing.Sequence[str] = ()

    async def clear_guilds(self) -> None:
        raise NotImplementedError

    async def delete_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    async def get_guild(self, guild_id: snowflakes.Snowflakeish, /) -> guilds.GatewayGuild:
        raise NotImplementedError

    def iter_guilds(self) -> CacheIterator[guilds.GatewayGuild]:
        raise NotImplementedError

    async def set_guild(self, guild: guilds.GatewayGuild, /) -> None:
        raise NotImplementedError


RefGuildCache = GuildCache


@typing.runtime_checkable
class GuildChannelCache(Resource, typing.Protocol):
    __slots__: typing.Sequence[str] = ()

    async def clear_guild_channels(self) -> None:
        raise NotImplementedError

    async def delete_guild_channel(self, channel_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    async def get_guild_channel(self, channel_id: snowflakes.Snowflakeish, /) -> channels.GuildChannel:
        raise NotImplementedError

    def iter_guild_channels(self) -> CacheIterator[channels.GuildChannel]:
        raise NotImplementedError

    async def set_guild_channel(self, channel: channels.GuildChannel, /) -> None:
        raise NotImplementedError


@typing.runtime_checkable
class RefGuildChannelCache(GuildChannelCache, typing.Protocol):
    __slots__: typing.Sequence[str] = ()

    async def clear_guild_channels_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    def iter_guild_channels_for_guild(
        self, guild_id: snowflakes.Snowflakeish, /
    ) -> CacheIterator[channels.GuildChannel]:
        raise NotImplementedError


@typing.runtime_checkable
class IntegrationCache(Resource, typing.Protocol):
    async def clear_integrations(self) -> None:
        raise NotImplementedError

    async def delete_integration(self, integration_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    def iter_integrations(self) -> CacheIterator[guilds.Integration]:
        raise NotImplementedError

    async def get_integration(self, integration_id: snowflakes.Snowflakeish, /) -> guilds.Integration:
        raise NotImplementedError

    async def set_integration(self, integration: guilds.Integration, /) -> None:
        raise NotImplementedError


class RefIntegrationCache(IntegrationCache, typing.Protocol):
    async def clear_integrations_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    async def delete_integration_by_application(self, application_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    async def get_integration_by_application(self, application_id: snowflakes.Snowflakeish, /) -> guilds.Integration:
        raise NotImplementedError

    def iter_integrations_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> CacheIterator[guilds.Integration]:
        raise NotImplementedError


@typing.runtime_checkable
class InviteCache(Resource, typing.Protocol):
    __slots__: typing.Sequence[str] = ()

    async def clear_invites(self) -> None:
        raise NotImplementedError

    async def delete_invite(self, invite_code: str, /) -> None:
        raise NotImplementedError

    async def get_invite(self, invite_code: str, /) -> invites.InviteWithMetadata:
        raise NotImplementedError

    def iter_invites(self) -> CacheIterator[invites.InviteWithMetadata]:
        raise NotImplementedError

    async def set_invite(self, invite: invites.InviteWithMetadata, /) -> None:
        raise NotImplementedError


@typing.runtime_checkable
class RefInviteCache(InviteCache, typing.Protocol):
    __slots__: typing.Sequence[str] = ()

    async def clear_invites_for_channel(self, channel_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    async def clear_invites_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    def iter_invites_for_channel(
        self, channel_id: snowflakes.Snowflakeish, /
    ) -> CacheIterator[invites.InviteWithMetadata]:
        raise NotImplementedError

    def iter_invites_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> CacheIterator[invites.InviteWithMetadata]:
        raise NotImplementedError


@typing.runtime_checkable
class MeCache(Resource, typing.Protocol):
    __slots__: typing.Sequence[str] = ()

    async def delete_me(self) -> None:
        raise NotImplementedError

    async def get_me(self) -> users.OwnUser:
        raise NotImplementedError

    async def set_me(self, me: users.OwnUser, /) -> None:
        raise NotImplementedError


RefMeCache = MeCache


@typing.runtime_checkable
class MemberCache(Resource, typing.Protocol):
    __slots__: typing.Sequence[str] = ()

    async def clear_members(self) -> None:
        raise NotImplementedError

    async def delete_member(self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    async def get_member(self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /) -> guilds.Member:
        raise NotImplementedError

    def iter_members(
        self,
    ) -> CacheIterator[guilds.Member]:
        raise NotImplementedError

    async def set_member(self, member: guilds.Member, /) -> None:
        raise NotImplementedError


@typing.runtime_checkable
class RefMemberCache(MemberCache, typing.Protocol):
    __slots__: typing.Sequence[str] = ()

    async def clear_members_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    async def clear_members_for_user(self, user_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    def iter_members_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> CacheIterator[guilds.Member]:
        raise NotImplementedError

    def iter_members_for_user(self, user_id: snowflakes.Snowflakeish, /) -> CacheIterator[guilds.Member]:
        raise NotImplementedError


@typing.runtime_checkable
class MessageCache(Resource, typing.Protocol):
    __slots__: typing.Sequence[str] = ()

    async def clear_messages(self) -> None:
        raise NotImplementedError

    async def delete_message(self, message_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    async def get_message(self, message_id: snowflakes.Snowflakeish, /) -> messages.Message:
        raise NotImplementedError

    def iter_messages(self) -> CacheIterator[messages.Message]:
        raise NotImplementedError

    async def set_message(self, message: messages.Message, /) -> None:
        raise NotImplementedError

    async def update_message(self, message: messages.PartialMessage, /) -> bool:
        # This is a special case method for handling the partial message updates we get
        raise NotImplementedError


@typing.runtime_checkable
class RefMessageCache(MessageCache, typing.Protocol):
    __slots__: typing.Sequence[str] = ()

    async def clear_messages_for_author(self, user_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    async def clear_messages_for_channel(self, channel_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    async def clear_messages_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    def iter_messages_for_author(self, user_id: snowflakes.Snowflakeish, /) -> CacheIterator[messages.Message]:
        raise NotImplementedError

    def iter_message_for_channel(self, channel_id: snowflakes.Snowflakeish, /) -> CacheIterator[messages.Message]:
        raise NotImplementedError

    def iter_messages_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> CacheIterator[messages.Message]:
        raise NotImplementedError


@typing.runtime_checkable
class PresenceCache(Resource, typing.Protocol):
    __slots__: typing.Sequence[str] = ()

    async def clear_presences(self) -> None:
        raise NotImplementedError

    async def delete_presence(self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    async def get_presence(
        self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /
    ) -> presences.MemberPresence:
        raise NotImplementedError

    def iter_presences(
        self,
    ) -> CacheIterator[presences.MemberPresence]:
        raise NotImplementedError

    async def set_presence(self, presence: presences.MemberPresence, /) -> None:
        raise NotImplementedError


@typing.runtime_checkable
class RefPresenceCache(PresenceCache, typing.Protocol):
    __slots__: typing.Sequence[str] = ()

    async def clear_presences_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    async def clear_presences_for_user(self, user_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    def iter_presences_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> CacheIterator[presences.MemberPresence]:
        raise NotImplementedError

    def iter_presences_for_user(self, user_id: snowflakes.Snowflakeish, /) -> CacheIterator[presences.MemberPresence]:
        raise NotImplementedError


@typing.runtime_checkable
class RoleCache(Resource, typing.Protocol):
    __slots__: typing.Sequence[str] = ()

    async def clear_roles(self) -> None:
        raise NotImplementedError

    async def delete_role(self, role_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    async def get_role(self, role_id: snowflakes.Snowflakeish, /) -> guilds.Role:
        raise NotImplementedError

    def iter_roles(self) -> CacheIterator[guilds.Role]:
        raise NotImplementedError

    async def set_role(self, role: guilds.Role, /) -> None:
        raise NotImplementedError


@typing.runtime_checkable
class RefRoleCache(RoleCache, typing.Protocol):
    __slots__: typing.Sequence[str] = ()

    async def clear_roles_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    def iter_roles_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> CacheIterator[guilds.Role]:
        raise NotImplementedError


@typing.runtime_checkable
class UserCache(Resource, typing.Protocol):
    """The traits for a cache implementation which supports a user cache.

    !!! note
        Unlike other resources, user doesn't have any events which
        directly update it and may only be updated through event
        listeners when resources which reference it are also included.
    """

    __slots__: typing.Sequence[str] = ()

    async def clear_users(self) -> None:  # TODO: cascade
        raise NotImplementedError

    async def delete_user(self, user_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    async def get_user(self, user_id: snowflakes.Snowflakeish, /) -> users.User:
        raise NotImplementedError

    def iter_users(self) -> CacheIterator[users.User]:
        raise NotImplementedError

    async def set_user(self, user: users.User, /) -> None:
        raise NotImplementedError


RefUserCache = UserCache


@typing.runtime_checkable
class VoiceStateCache(Resource, typing.Protocol):
    __slots__: typing.Sequence[str] = ()

    async def clear_voice_states(self) -> None:
        raise NotImplementedError

    async def delete_voice_state(self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    async def get_voice_state(
        self, guild_id: snowflakes.Snowflakeish, user_id: snowflakes.Snowflakeish, /
    ) -> voices.VoiceState:
        raise NotImplementedError

    def iter_voice_states(self) -> CacheIterator[voices.VoiceState]:
        raise NotImplementedError

    async def set_voice_state(self, voice_state: voices.VoiceState, /) -> None:
        raise NotImplementedError


@typing.runtime_checkable
class RefVoiceStateCache(VoiceStateCache, typing.Protocol):
    __slots__: typing.Sequence[str] = ()  # TODO: for user?

    async def clear_voice_states_for_channel(self, channel_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    async def clear_voice_states_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        raise NotImplementedError

    def iter_voice_states_for_channel(self, channel_id: snowflakes.Snowflakeish, /) -> CacheIterator[voices.VoiceState]:
        raise NotImplementedError

    def iter_voice_states_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> CacheIterator[voices.VoiceState]:
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
