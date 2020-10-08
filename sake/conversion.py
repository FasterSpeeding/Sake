from __future__ import annotations

__all__: typing.Final[typing.Sequence[str]] = [
    "RedisValueT",
    "RedisMapT",
    "ObjectHandler",
    "ObjectPickler",
]

import abc
import pickle
import typing

from hikari import channels
from hikari import emojis
from hikari import guilds
from hikari import invites
from hikari import messages
from hikari import presences
from hikari import users
from hikari import voices

if typing.TYPE_CHECKING:
    from hikari import traits


#  TODO: can we use a type var here for invariance?
RedisValueT = typing.Union[bytearray, bytes, float, int, str]
"""A type variable of the value types accepted by aioredis."""

RedisMapT = typing.MutableMapping[str, RedisValueT]
"""A type variable of the mapping type accepted by aioredis"""

ValueT = typing.TypeVar("ValueT")


class ObjectHandler(abc.ABC):
    @abc.abstractmethod
    def deserialize_emoji(self, value: bytes, *, app: traits.RESTAware) -> emojis.KnownCustomEmoji:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_emoji(self, emoji: emojis.KnownCustomEmoji) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_guild(self, value: bytes, *, app: traits.RESTAware) -> guilds.GatewayGuild:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_guild(self, guild: guilds.GatewayGuild) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_guild_channel(self, value: bytes, *, app: traits.RESTAware) -> channels.GuildChannel:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_guild_channel(self, channel: channels.GuildChannel) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_invite(self, value: bytes, *, app: traits.RESTAware) -> invites.InviteWithMetadata:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_invite(self, invite: invites.InviteWithMetadata) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_me(self, value: bytes, *, app: traits.RESTAware) -> users.OwnUser:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_me(self, me: users.OwnUser) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_member(self, value: bytes, *, app: traits.RESTAware) -> guilds.Member:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_member(self, member: guilds.Member) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_message(self, value: bytes, *, app: traits.RESTAware) -> messages.Message:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_message(self, message: messages.Message) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_presence(self, value: bytes, *, app: traits.RESTAware) -> presences.MemberPresence:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_presence(self, presence: presences.MemberPresence) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_role(self, value: bytes, *, app: traits.RESTAware) -> guilds.Role:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_role(self, role: guilds.Role) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_user(self, value: bytes, *, app: traits.RESTAware) -> users.User:
        raise NotImplementedError

    @abc.abstractmethod  # TODO: check for missed types like voice state
    def serialize_user(self, user: users.User) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_voice_state(self, value: bytes, *, app: traits.RESTAware) -> voices.VoiceState:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_voice_state(self, voice_state: voices.VoiceState) -> bytes:
        raise NotImplementedError


class ObjectPickler(ObjectHandler):
    def deserialize_emoji(self, value: bytes, *, app: traits.RESTAware) -> emojis.KnownCustomEmoji:
        emoji = pickle.loads(value)

        if not isinstance(emoji, emojis.KnownCustomEmoji):
            raise ValueError(f"Unexpected object type {type(emoji)}, expected a KnownCustomEmoji")

        emoji.app = app

        if emoji.user is not None:
            emoji.user.app = app

        return emoji

    def serialize_emoji(self, emoji: emojis.KnownCustomEmoji) -> bytes:
        return pickle.dumps(emoji)

    def deserialize_guild(self, value: bytes, *, app: traits.RESTAware) -> guilds.GatewayGuild:
        guild = pickle.loads(value)

        if not isinstance(guild, guilds.GatewayGuild):
            raise ValueError(f"Unexpected object type {type(guild)}, expected a GatewayGuild")

        guild.app = app
        return guild

    def serialize_guild(self, guild: guilds.GatewayGuild) -> bytes:
        return pickle.dumps(guild)

    def deserialize_guild_channel(self, value: bytes, *, app: traits.RESTAware) -> channels.GuildChannel:
        channel = pickle.loads(value)

        if not isinstance(channel, channels.GuildChannel):
            raise ValueError(f"Unexpected object type {type(channel)}, expected a GuildChannel")

        channel.app = app
        return channel

    def serialize_guild_channel(self, channel: channels.GuildChannel) -> bytes:
        return pickle.dumps(channel)

    def deserialize_invite(self, value: bytes, *, app: traits.RESTAware) -> invites.InviteWithMetadata:
        invite = pickle.loads(value)

        if not isinstance(invite, invites.InviteWithMetadata):
            raise ValueError(f"Unexpected object type {type(invite)}, expected a InviteWithMetadata")

        invite.app = app

        if invite.target_user is not None:
            invite.target_user.app = app

        if invite.inviter is not None:
            invite.inviter.app = app

        return invite

    def serialize_invite(self, invite: invites.InviteWithMetadata) -> bytes:
        return pickle.dumps(invite)

    def deserialize_me(self, value: bytes, *, app: traits.RESTAware) -> users.OwnUser:
        me = pickle.loads(value)

        if not isinstance(me, users.OwnUser):
            raise ValueError(f"Unexpected object type {type(me)}, expected a OwnUser")

        me.app = app
        return me

    def serialize_me(self, me: users.OwnUser) -> bytes:
        return pickle.dumps(me)

    def deserialize_member(self, value: bytes, *, app: traits.RESTAware) -> guilds.Member:
        member = pickle.loads(value)

        if not isinstance(member, guilds.Member):
            raise ValueError(f"Unexpected object type {type(member)}, expected Member")

        member.user.app = app
        return member

    def serialize_member(self, member: guilds.Member) -> bytes:
        return pickle.dumps(member)

    def deserialize_message(self, value: bytes, *, app: traits.RESTAware) -> messages.Message:
        message = pickle.loads(value)

        if not isinstance(message, messages.Message):
            raise ValueError(f"Unexpected object type {type(message)}, expected Message")

        if message.member is not None:
            message.member.user.app = app

        if message.application is not None:
            message.application.app = app

        if message.message_reference is not None:
            message.message_reference.app = app

        message.author.app = app
        return message

    def serialize_message(self, message: messages.Message) -> bytes:
        return pickle.dumps(message)

    def deserialize_presence(self, value: bytes, *, app: traits.RESTAware) -> presences.MemberPresence:
        presence = pickle.loads(value)

        if not isinstance(presence, presences.MemberPresence):
            raise ValueError(f"Unexpected object type {type(presence)}, expected a MemberPresence")

        presence.app = app

        for activity in presence.activities:
            if isinstance(activity.emoji, emojis.KnownCustomEmoji):
                activity.emoji.app = app

        return presence

    def serialize_presence(self, presence: presences.MemberPresence) -> bytes:
        return pickle.dumps(presence)

    def deserialize_role(self, value: bytes, *, app: traits.RESTAware) -> guilds.Role:
        role = pickle.loads(value)

        if not isinstance(role, guilds.Role):
            raise ValueError(f"Unexpected object type {type(role)}, expected a Role")

        role.app = app
        return role

    def serialize_role(self, role: guilds.Role) -> bytes:
        return pickle.dumps(role)

    def deserialize_user(self, value: bytes, *, app: traits.RESTAware) -> users.User:
        user = pickle.loads(value)

        if not isinstance(user, users.User):
            raise ValueError(f"Unexpected object type {type(user)}, expected a User impl")

        user.app = app
        return user

    def serialize_user(self, user: users.User) -> bytes:
        return pickle.dumps(user)

    def deserialize_voice_state(self, value: bytes, *, app: traits.RESTAware) -> voices.VoiceState:
        voice_state = pickle.loads(value)

        if not isinstance(voice_state, voices.VoiceState):
            raise ValueError(f"Unexpected object type {type(voice_state)}, expected a VoiceState")

        voice_state.app = app
        voice_state.member.user.app = app
        return voice_state

    def serialize_voice_state(self, voice_state: voices.VoiceState) -> bytes:
        return pickle.dumps(voice_state)
