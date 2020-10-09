from __future__ import annotations

__all__: typing.Final[typing.Sequence[str]] = [
    "RedisValueT",
    "RedisMapT",
    "ObjectHandler",
    "ObjectPickler",
]

import abc
import json
import logging
import pickle
import typing

from hikari import channels
from hikari import colors
from hikari import emojis
from hikari import guilds
from hikari import invites
from hikari import messages
from hikari import presences
from hikari import snowflakes
from hikari import undefined
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

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("hikari.sake.conversion")


class ObjectHandler(abc.ABC):
    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    def deserialize_emoji(self, value: bytes) -> emojis.KnownCustomEmoji:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_emoji(self, emoji: emojis.KnownCustomEmoji) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_guild(self, value: bytes) -> guilds.GatewayGuild:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_guild(self, guild: guilds.GatewayGuild) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_guild_channel(self, value: bytes) -> channels.GuildChannel:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_guild_channel(self, channel: channels.GuildChannel) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_invite(self, value: bytes) -> invites.InviteWithMetadata:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_invite(self, invite: invites.InviteWithMetadata) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_me(self, value: bytes) -> users.OwnUser:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_me(self, me: users.OwnUser) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_member(self, value: bytes) -> guilds.Member:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_member(self, member: guilds.Member) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_message(self, value: bytes) -> messages.Message:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_message(self, message: messages.Message) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_presence(self, value: bytes) -> presences.MemberPresence:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_presence(self, presence: presences.MemberPresence) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_role(self, value: bytes) -> guilds.Role:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_role(self, role: guilds.Role) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_user(self, value: bytes) -> users.User:
        raise NotImplementedError

    @abc.abstractmethod  # TODO: check for missed types like voice state
    def serialize_user(self, user: users.User) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_voice_state(self, value: bytes) -> voices.VoiceState:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_voice_state(self, voice_state: voices.VoiceState) -> bytes:
        raise NotImplementedError


class ObjectPickler(ObjectHandler):
    # TODO: persistant pickler and depickler?
    __slots__: typing.Sequence[str] = ("_app",)

    def __init__(self, app: traits.RESTAware) -> None:
        self._app = app

    @property
    def protocol(self) -> int:
        return 4

    def _dumps(self, obj: typing.Any, *, fix_imports: bool = True, buffer_callback: typing.Any = None) -> bytes:
        return pickle.dumps(obj, protocol=self.protocol, fix_imports=fix_imports, buffer_callback=buffer_callback)

    def _loads(
        self,
        data: bytes,
        /,
        *,
        fix_imports: bool = True,
        encoding: str = "ASCII",
        errors: str = "strict",
        buffers: typing.Any = None,
    ) -> typing.Any:
        return pickle.loads(data, fix_imports=fix_imports, encoding=encoding, errors=errors, buffers=buffers)

    def deserialize_emoji(self, value: bytes) -> emojis.KnownCustomEmoji:
        emoji = self._loads(value)

        if not isinstance(emoji, emojis.KnownCustomEmoji):
            raise ValueError(f"Unexpected object type {type(emoji)}, expected a KnownCustomEmoji")

        emoji.app = self._app

        if emoji.user is not None:
            emoji.user.app = self._app

        return emoji

    def serialize_emoji(self, emoji: emojis.KnownCustomEmoji) -> bytes:
        return self._dumps(emoji)

    def deserialize_guild(self, value: bytes) -> guilds.GatewayGuild:
        guild = self._loads(value)

        if not isinstance(guild, guilds.GatewayGuild):
            raise ValueError(f"Unexpected object type {type(guild)}, expected a GatewayGuild")

        guild.app = self._app
        return guild

    def serialize_guild(self, guild: guilds.GatewayGuild) -> bytes:
        return self._dumps(guild)

    def deserialize_guild_channel(self, value: bytes) -> channels.GuildChannel:
        channel = self._loads(value)

        if not isinstance(channel, channels.GuildChannel):
            raise ValueError(f"Unexpected object type {type(channel)}, expected a GuildChannel")

        channel.app = self._app
        return channel

    def serialize_guild_channel(self, channel: channels.GuildChannel) -> bytes:
        return self._dumps(channel)

    def deserialize_invite(self, value: bytes) -> invites.InviteWithMetadata:
        invite = self._loads(value)

        if not isinstance(invite, invites.InviteWithMetadata):
            raise ValueError(f"Unexpected object type {type(invite)}, expected a InviteWithMetadata")

        invite.app = self._app

        if invite.target_user is not None:
            invite.target_user.app = self._app

        if invite.inviter is not None:
            invite.inviter.app = self._app

        return invite

    def serialize_invite(self, invite: invites.InviteWithMetadata) -> bytes:
        return self._dumps(invite)

    def deserialize_me(self, value: bytes) -> users.OwnUser:
        me = self._loads(value)

        if not isinstance(me, users.OwnUser):
            raise ValueError(f"Unexpected object type {type(me)}, expected a OwnUser")

        me.app = self._app
        return me

    def serialize_me(self, me: users.OwnUser) -> bytes:
        return self._dumps(me)

    def deserialize_member(self, value: bytes) -> guilds.Member:
        member = self._loads(value)

        if not isinstance(member, guilds.Member):
            raise ValueError(f"Unexpected object type {type(member)}, expected Member")

        member.user.app = self._app
        return member

    def serialize_member(self, member: guilds.Member) -> bytes:
        return self._dumps(member)

    def deserialize_message(self, value: bytes) -> messages.Message:
        message = self._loads(value)

        if not isinstance(message, messages.Message):
            raise ValueError(f"Unexpected object type {type(message)}, expected Message")

        if message.member is not None:
            message.member.user.app = self._app

        if message.application is not None:
            message.application.app = self._app

        if message.message_reference is not None:
            message.message_reference.app = self._app

        message.author.app = self._app
        return message

    def serialize_message(self, message: messages.Message) -> bytes:
        return self._dumps(message)

    def deserialize_presence(self, value: bytes) -> presences.MemberPresence:
        presence = self._loads(value)

        if not isinstance(presence, presences.MemberPresence):
            raise ValueError(f"Unexpected object type {type(presence)}, expected a MemberPresence")

        presence.app = self._app

        for activity in presence.activities:
            if isinstance(activity.emoji, emojis.KnownCustomEmoji):
                activity.emoji.app = self._app

        return presence

    def serialize_presence(self, presence: presences.MemberPresence) -> bytes:
        return self._dumps(presence)

    def deserialize_role(self, value: bytes) -> guilds.Role:
        role = self._loads(value)

        if not isinstance(role, guilds.Role):
            raise ValueError(f"Unexpected object type {type(role)}, expected a Role")

        role.app = self._app
        return role

    def serialize_role(self, role: guilds.Role) -> bytes:
        return self._dumps(role)

    def deserialize_user(self, value: bytes) -> users.User:
        user = self._loads(value)

        if not isinstance(user, users.User):
            raise ValueError(f"Unexpected object type {type(user)}, expected a User impl")

        user.app = self._app
        return user

    def serialize_user(self, user: users.User) -> bytes:
        return self._dumps(user)

    def deserialize_voice_state(self, value: bytes) -> voices.VoiceState:
        voice_state = self._loads(value)

        if not isinstance(voice_state, voices.VoiceState):
            raise ValueError(f"Unexpected object type {type(voice_state)}, expected a VoiceState")

        voice_state.app = self._app
        voice_state.member.user.app = self._app
        return voice_state

    def serialize_voice_state(self, voice_state: voices.VoiceState) -> bytes:
        return self._dumps(voice_state)


def _get_init_name(field: str) -> str:
    return field[1:] if field.startswith("_") else field


_PASSED_THROUGH_SINGLETONS: typing.Final[typing.Mapping[typing.Any, str]] = {
    undefined.UNDEFINED: "UNDEFINED",
    None: "None",
}


def _generate_json_deserializer(
    cls: typing.Type[ValueT],
    *rules: typing.Sequence[typing.Union[str, typing.Tuple[str, typing.Callable[[typing.Any], typing.Any]]]],
) -> typing.Callable[[typing.Mapping[str, typing.Any]], ValueT]:
    # For the case of no supplied rules, return an empty lambda to avoid syntax errors later on.
    if not rules:
        return lambda _: {}

    getters = []
    casts = {rule[1] for rule in rules if isinstance(rule, tuple)}
    named_casts = {cast: f"c{i}" for i, cast in enumerate(casts)}

    for rule in rules:
        if isinstance(rule, str):
            getters.append(f"{_get_init_name(rule)}=data[{rule!r}]")
            continue

        name, current_cast = rule
        init_name = _get_init_name(name)
        if current_cast in _PASSED_THROUGH_SINGLETONS:
            getters.append(f"{init_name}=data.get({name!r}, {_PASSED_THROUGH_SINGLETONS[current_cast]})")
        else:
            cast_name = named_casts[current_cast]
            getters.append(f"{init_name}={cast_name}(data[{name!r}]) if {name!r} in data else UNDEFINED")

    globals_ = {"cls": cls, "UNDEFINED": undefined.UNDEFINED}
    globals_.update((value, key) for key, value in named_casts.items())
    code = f"def serialize(data): return cls({','.join(getters)})"
    _LOGGER.debug("generating json serialize method for %r\n  %r", cls, code)
    exec(code, globals_)
    return typing.cast("typing.Callable[[typing.Mapping[str, typing.Any]], ValueT]", globals_["serialize"])


def _generate_json_serializer(
    *rules: typing.Sequence[
        typing.Union[str, typing.Tuple[str, typing.Optional[typing.Callable[[typing.Any], typing.Any]]]]
    ],
) -> typing.Callable[[typing.Any], typing.MutableMapping[str, typing.Any]]:
    # For the case of no supplied rules, return an empty lambda to avoid syntax errors later on.
    if not rules:
        return lambda _: {}

    setters = []
    casts = {rule[1] for rule in rules if isinstance(rule, tuple)}
    named_casts = {cast: f"c{i}" for i, cast in enumerate(casts)}

    for rule in rules:
        if isinstance(rule, str):
            setters.append(f"if m.{rule} is not UNDEFINED: data[{rule!r}] = m.{rule}")
            continue

        name, cast = rule
        if cast is not None:
            cast_name = named_casts[cast]
            setters.append(f"if m.{name} is not UNDEFINED: data[{name!r}] = {cast_name}(m.{name})")

    globals_ = {"UNDEFINED": undefined.UNDEFINED}
    globals_.update((value, key) for key, value in named_casts.items())
    code = "def serialize(m):\n  data = {};\n  " + "\n  ".join(setters) + "\n  return data"
    _LOGGER.debug("generating json serialize method:\n  %r", code)
    exec(code, globals_)
    return typing.cast("typing.Callable[[typing.Any], typing.MutableMapping[str, typing.Any]]", globals_["serialize"])


def _deserialize_snowflake_array(array: typing.Sequence[int]) -> typing.Sequence[snowflakes.Snowflake]:
    return [*map(snowflakes.Snowflake, array)]


class Jsonhandler(ObjectHandler):
    __slots__: typing.Sequence[str] = ("_app", "_decoder", "_encoder", "_deserializers", "_serializers")

    def __init__(self, app: traits.RESTAware) -> None:
        self._app = app
        self._decoder = json.JSONDecoder()
        self._encoder = json.JSONEncoder()
        self._deserializers: typing.MutableMapping[
            typing.Any, typing.Callable[[typing.Mapping[str, typing.Any]], typing.Any]
        ] = {}
        self._serializers: typing.MutableMapping[
            typing.Any, typing.Callable[[typing.Any], typing.MutableMapping[str, typing.Any]]
        ] = {}

    def _dumps(self, data: typing.Mapping[str, typing.Any]) -> bytes:
        return self._encoder.encode(data).encode()

    def _loads(self, data: bytes) -> typing.Mapping[str, typing.Any]:
        return self._decoder.decode(data.decode("utf-8"))

    def _get_emoji_deserializer(self) -> typing.Callable[[typing.Mapping[str, typing.Any]], emojis.KnownCustomEmoji]:
        try:
            return self._deserializers[emojis.KnownCustomEmoji]
        except KeyError:
            pass

        deserializer = _generate_json_deserializer(
            emojis.KnownCustomEmoji,
            ("id", snowflakes.Snowflake),
            "name",
            "is_animated",
            ("app", None),
            ("guild_id", snowflakes.Snowflake),
            ("role_ids", _deserialize_snowflake_array),
            ("user", lambda data: self.deserialize_user(data) if data is not None else None),
        )
        self._deserializers[emojis.KnownCustomEmoji] = deserializer
        return deserializer

    def deserialize_emoji(self, value: bytes) -> emojis.KnownCustomEmoji:
        emoji = self._get_emoji_deserializer()(self._loads(value))
        emoji.app = self._app

        if emoji.user is not None:
            emoji.user.app = self._app

        return emoji

    def _get_emoji_serializer(self) -> typing.Callable[[emojis.KnownCustomEmoji], typing.Mapping[str, typing.Any]]:
        try:
            return self._serializers[emojis.KnownCustomEmoji]
        except KeyError:
            pass

        serializer = _generate_json_serializer(
            "id",
            "name",
            "is_animated",
            ("app", None),
            "guild_id",
            "role_ids",
            ("user", lambda user: self.serialize_user(user) if user is not None else None),
            "is_animated",
            "is_colons_required",
            "is_managed",
            "is_available",
        )
        self._serializers[emojis.KnownCustomEmoji] = serializer
        return serializer

    def serialize_emoji(self, emoji: emojis.KnownCustomEmoji) -> bytes:
        return self._dumps(self._get_emoji_serializer()(emoji))

    def deserialize_guild(self, value: bytes) -> guilds.GatewayGuild:
        pass

    def serialize_guild(self, guild: guilds.GatewayGuild) -> bytes:
        pass

    def deserialize_guild_channel(self, value: bytes) -> channels.GuildChannel:
        pass

    def serialize_guild_channel(self, channel: channels.GuildChannel) -> bytes:
        pass

    def deserialize_invite(self, value: bytes) -> invites.InviteWithMetadata:
        pass

    def serialize_invite(self, invite: invites.InviteWithMetadata) -> bytes:
        pass

    def deserialize_me(self, value: bytes) -> users.OwnUser:
        pass

    def serialize_me(self, me: users.OwnUser) -> bytes:
        pass

    def deserialize_member(self, value: bytes) -> guilds.Member:
        pass

    def serialize_member(self, member: guilds.Member) -> bytes:
        pass

    def deserialize_message(self, value: bytes) -> messages.Message:
        pass

    def serialize_message(self, message: messages.Message) -> bytes:
        pass

    def deserialize_presence(self, value: bytes) -> presences.MemberPresence:
        pass

    def serialize_presence(self, presence: presences.MemberPresence) -> bytes:
        pass

    def deserialize_role(self, value: bytes) -> guilds.Role:
        pass

    def serialize_role(self, role: guilds.Role) -> bytes:
        pass

    def _get_user_deserializer(self) -> typing.Callable[[typing.Mapping[str, typing.Any]], users.User]:
        try:
            return self._deserializers[users.UserImpl]
        except KeyError:
            pass

        deserializer = _generate_json_deserializer(
            users.UserImpl,
            ("id", snowflakes.Snowflake),
            ("app", None),
            "discriminator",
            "username",
            "avatar_hash",
            "is_bot",
            "is_system",
            ("flags", users.UserFlag),
        )
        self._deserializers[users.UserImpl] = deserializer
        return deserializer

    def deserialize_user(self, value: bytes) -> users.User:
        user = self._get_user_deserializer()(self._loads(value))
        user.app = self._app
        return user

    def _get_user_serializer(self) -> typing.Callable[[users.User], typing.Mapping[str, typing.Any]]:
        try:
            return self._serializers[users.UserImpl]
        except KeyError:
            pass

        serializer = _generate_json_serializer(
            "id", ("app", None), "discriminator", "username", "avatar_hash", "is_bot", "is_system", "flags"
        )
        self._serializers[users.UserImpl] = serializer
        return serializer

    def serialize_user(self, user: users.User) -> bytes:
        return self._dumps(self._get_user_serializer()(user))

    def deserialize_voice_state(self, value: bytes) -> voices.VoiceState:
        pass

    def serialize_voice_state(self, voice_state: voices.VoiceState) -> bytes:
        pass
