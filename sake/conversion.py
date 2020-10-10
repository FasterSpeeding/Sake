from __future__ import annotations

__all__: typing.Final[typing.Sequence[str]] = [
    "JSONhandler",
    "ObjectHandler",
    "PickleHandler",
]

import abc
import datetime
import json
import logging
import pickle
import typing

from hikari import applications
from hikari import channels
from hikari import colors
from hikari import embeds
from hikari import emojis
from hikari import guilds
from hikari import invites
from hikari import messages
from hikari import permissions
from hikari import presences
from hikari import snowflakes
from hikari import undefined
from hikari import users
from hikari import voices
from hikari.internal import time

if typing.TYPE_CHECKING:
    from hikari import traits


KeyT = typing.TypeVar("KeyT")
OtherKeyT = typing.TypeVar("OtherKeyT")
ValueT = typing.TypeVar("ValueT")
OtherValueT = typing.TypeVar("OtherValueT")

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

    @abc.abstractmethod
    def serialize_user(self, user: users.User) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_voice_state(self, value: bytes) -> voices.VoiceState:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_voice_state(self, voice_state: voices.VoiceState) -> bytes:
        raise NotImplementedError


class PickleHandler(ObjectHandler):
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


def _optional_converter(
    cast: typing.Callable[[ValueT], OtherValueT]
) -> typing.Callable[[typing.Optional[ValueT]], typing.Optional[OtherValueT]]:
    def converter(value: typing.Optional[ValueT]) -> typing.Optional[OtherValueT]:
        if value is None:
            return None
        return cast(value)

    return converter


_PASSED_THROUGH_SINGLETONS: typing.Final[typing.Mapping[typing.Any, str]] = {
    undefined.UNDEFINED: "UNDEFINED",
    None: "None",
}


def _generate_json_deserializer(
    cls: typing.Type[ValueT],
    *rules: typing.Union[
        str, typing.Tuple[str, typing.Union[typing.Callable[[typing.Any], typing.Any], None, undefined.UndefinedType]]
    ],
) -> typing.Callable[[typing.Mapping[str, typing.Any]], ValueT]:
    # For the case of no supplied rules, return an empty lambda to avoid syntax errors later on.
    if not rules:
        return lambda _: cls()

    getters = []
    casts = {rule[1] for rule in rules if isinstance(rule, tuple)}
    named_casts = {cast: f"c{i}" for i, cast in enumerate(casts)}

    for rule in rules:
        if isinstance(rule, str):  # TODO: specified whether a field can be undefined or not?
            getters.append(f"{_get_init_name(rule)}=data.get({rule!r}, UNDEFINED)")
            continue

        name, current_cast = rule
        init_name = _get_init_name(name)
        if current_cast in _PASSED_THROUGH_SINGLETONS:
            getters.append(f"{init_name}=data.get({name!r}, {_PASSED_THROUGH_SINGLETONS[current_cast]})")
        else:
            cast_name = named_casts[current_cast]
            getters.append(f"{init_name}={cast_name}(data[{name!r}]) if {name!r} in data else UNDEFINED")

    globals_: typing.Dict[str, typing.Any] = {"cls": cls, "UNDEFINED": undefined.UNDEFINED}
    globals_.update((value, key) for key, value in named_casts.items())
    code = f"def serialize(data): return cls({','.join(getters)})"
    _LOGGER.debug("generating json serialize method for %r\n  %r", cls, code)
    exec(code, globals_)
    return typing.cast("typing.Callable[[typing.Mapping[str, typing.Any]], ValueT]", globals_["serialize"])


def _generate_json_serializer(
    *rules: typing.Union[str, typing.Tuple[str, typing.Callable[[typing.Any], typing.Any]]],
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
        cast_name = named_casts[cast]
        setters.append(f"if m.{name} is not UNDEFINED: data[{name!r}] = {cast_name}(m.{name})")

    globals_: typing.Dict[str, typing.Any] = {"UNDEFINED": undefined.UNDEFINED}
    globals_.update((value, key) for key, value in named_casts.items())
    code = "def serialize(m):\n  data = {};\n  " + "\n  ".join(setters) + "\n  return data"
    _LOGGER.debug("generating json serialize method:\n  %r", code)
    exec(code, globals_)
    return typing.cast("typing.Callable[[typing.Any], typing.MutableMapping[str, typing.Any]]", globals_["serialize"])


def _array_converter(
    cast: typing.Callable[[ValueT], OtherValueT]
) -> typing.Callable[[typing.Sequence[ValueT]], typing.Sequence[OtherValueT]]:
    def converter(array: typing.Sequence[ValueT]) -> typing.Sequence[OtherValueT]:
        return [*map(cast, array)]

    return converter


def _mapping_converter(
    key_cast: typing.Callable[[KeyT], OtherKeyT], value_cast: typing.Callable[[ValueT], OtherValueT]
) -> typing.Callable[[typing.Mapping[KeyT, ValueT]], typing.MutableMapping[OtherKeyT, OtherValueT]]:
    def converter(mapping: typing.Mapping[KeyT, ValueT]) -> typing.MutableMapping[OtherKeyT, OtherValueT]:
        return {key_cast(key): value_cast(value) for key, value in mapping.items()}

    return converter


class JSONhandler(ObjectHandler):
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
        result = self._decoder.decode(data.decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError("Invalud json content received")

        return result

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
            ("role_ids", _array_converter(snowflakes.Snowflake)),
            ("user", _optional_converter(self.deserialize_user)),
            "is_colons_required",
            "is_managed",
            "is_available",
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
            "guild_id",
            "role_ids",
            ("user", _optional_converter(self.serialize_user)),
            "is_animated",
            "is_colons_required",
            "is_managed",
            "is_available",
        )
        self._serializers[emojis.KnownCustomEmoji] = serializer
        return serializer

    def serialize_emoji(self, emoji: emojis.KnownCustomEmoji) -> bytes:
        return self._dumps(self._get_emoji_serializer()(emoji))

    def _get_guild_deserializer(self) -> typing.Callable[[typing.Mapping[str, typing.Any]], guilds.GatewayGuild]:
        try:
            return self._deserializers[guilds.GatewayGuild]
        except KeyError:
            pass

        deserializer = _generate_json_deserializer(
            guilds.GatewayGuild,
            ("app", None),
            ("features", _array_converter(guilds.GuildFeature)),
            ("id", snowflakes.Snowflake),
            "icon_hash",
            "name",
            ("application_id", _optional_converter(snowflakes.Snowflake)),
            ("afk_channel_id", _optional_converter(snowflakes.Snowflake)),
            ("afk_timeout", lambda data: datetime.timedelta(seconds=data)),
            "banner_hash",
            ("default_message_notifications", guilds.GuildMessageNotificationsLevel),
            "description",
            "discovery_splash_hash",
            ("explicit_content_filter", guilds.GuildExplicitContentFilterLevel),
            "is_widget_enabled",
            "max_video_channel_users",
            ("mfa_level", guilds.GuildMFALevel),
            ("owner_id", snowflakes.Snowflake),
            "preferred_locale",
            "premium_subscription_count",
            ("premium_tier", guilds.GuildPremiumTier),
            ("public_updates_channel_id", _optional_converter(snowflakes.Snowflake)),
            "region",
            ("rules_channel_id", _optional_converter(snowflakes.Snowflake)),
            "splash_hash",
            ("system_channel_flags", guilds.GuildSystemChannelFlag),
            ("system_channel_id", _optional_converter(snowflakes.Snowflake)),
            "vanity_url_code",
            ("verification_level", guilds.GuildVerificationLevel),
            ("widget_channel_id", _optional_converter(snowflakes.Snowflake)),
            "is_large",
            ("joined_at", _optional_converter(time.iso8601_datetime_string_to_datetime)),
            "member_count",
        )
        self._deserializers[guilds.GatewayGuild] = deserializer
        return deserializer

    def deserialize_guild(self, value: bytes) -> guilds.GatewayGuild:
        guild = self._get_guild_deserializer()(self._loads(value))
        guild.app = self._app
        return guild

    def _get_guild_serializer(self) -> typing.Callable[[guilds.GatewayGuild], typing.Mapping[str, typing.Any]]:
        try:
            return self._serializers[guilds.GatewayGuild]
        except KeyError:
            pass

        serializer = _generate_json_serializer(
            "features",
            "id",
            "icon_hash",
            "name",
            "application_id",
            "afk_channel_id",
            ("afk_timeout", lambda delta: delta.total_seconds()),
            "banner_hash",
            "default_message_notifications",
            "description",
            "discovery_splash_hash",
            "explicit_content_filter",
            "is_widget_enabled",
            "max_video_channel_users",
            "mfa_level",
            "owner_id",
            "preferred_locale",
            "premium_subscription_count",
            "premium_tier",
            "public_updates_channel_id",
            "region",
            "rules_channel_id",
            "splash_hash",
            "system_channel_flags",
            "system_channel_id",
            "vanity_url_code",
            "verification_level",
            "widget_channel_id",
            "is_large",
            ("joined_at", lambda date: date.isoformat()),
            "member_count",
        )
        self._serializers[guilds.GatewayGuild] = serializer
        return serializer

    def serialize_guild(self, guild: guilds.GatewayGuild) -> bytes:
        return self._dumps(self._get_guild_serializer()(guild))

    def deserialize_guild_channel(self, value: bytes) -> channels.GuildChannel:
        raise NotImplementedError

    def serialize_guild_channel(self, channel: channels.GuildChannel) -> bytes:
        raise NotImplementedError

    def _get_invite_deserializer(
        self,
    ) -> typing.Callable[[typing.Mapping[str, typing.Any]], invites.InviteWithMetadata]:
        try:
            return self._deserializers[invites.InviteWithMetadata]
        except KeyError:
            pass

        guild_deserializer = _generate_json_deserializer(
            invites.InviteGuild,
            ("app", None),
            ("features", _array_converter(guilds.GuildFeature)),
            ("id", snowflakes.Snowflake),
            "icon_hash",
            "name",
            "splash_hash",
            "banner_hash",
            "description",
            ("verification_level", guilds.GuildVerificationLevel),
            "vanity_url_code",
        )
        channel_deserializer = _generate_json_deserializer(
            channels.PartialChannel,
            ("app", None),
            ("id", snowflakes.Snowflake),
            "name",
            ("type", channels.ChannelType),
        )

        deserializer = _generate_json_deserializer(
            invites.InviteWithMetadata,
            ("app", None),
            "code",
            ("guild", _optional_converter(guild_deserializer)),
            ("guild_id", _optional_converter(snowflakes.Snowflake)),
            ("channel", _optional_converter(channel_deserializer)),
            ("channel_id", snowflakes.Snowflake),
            ("inviter", self._get_user_serializer()),
            ("target_user", self._get_user_serializer()),
            ("target_user_type", _optional_converter(invites.TargetUserType)),
            "approximate_active_member_count",
            "approximate_member_count",
        )
        self._deserializers[invites.InviteWithMetadata] = deserializer
        return deserializer

    def deserialize_invite(self, value: bytes) -> invites.InviteWithMetadata:
        invite = self._get_invite_deserializer()(self._loads(value))
        invite.app = self._app

        if invite.guild is not None:
            invite.guild.app = self._app

        if invite.channel is not None:
            invite.channel.app = self._app

        if invite.inviter is not None:
            invite.inviter.app = self._app

        if invite.target_user is not None:
            invite.target_user.app = self._app

        return invite

    def _get_invite_serializer(self) -> typing.Callable[[invites.InviteWithMetadata], typing.Mapping[str, typing.Any]]:
        try:
            return self._serializers[invites.InviteWithMetadata]
        except KeyError:
            pass

        guild_serializer = _generate_json_serializer(
            "features",
            "id",
            "icon_hash",
            "name",
            "splash_hash",
            "banner_hash",
            "description",
            "verification_level",
            "vanity_url_code",
        )

        channel_serializer = _generate_json_serializer("id", "name", "type")

        serializer = _generate_json_serializer(
            "code",
            ("guild", _optional_converter(guild_serializer)),
            "guild_id",
            ("channel", _optional_converter(channel_serializer)),
            "channel_id",
            ("inviter", self._get_user_serializer()),
            ("target_user", self._get_user_serializer()),
            "target_user_type",
            "approximate_active_member_count",
            "approximate_member_count",
        )
        self._serializers[invites.InviteWithMetadata] = serializer
        return serializer

    def serialize_invite(self, invite: invites.InviteWithMetadata) -> bytes:
        return self._dumps(self._get_invite_serializer()(invite))

    def _get_me_deserializer(self) -> typing.Callable[[typing.Mapping[str, typing.Any]], users.OwnUser]:
        try:
            return self._deserializers[users.OwnUser]
        except KeyError:
            pass

        deserializer = _generate_json_deserializer(
            users.OwnUser,
            *self._user_serialize_rules(),
            "is_mfa_enabled",
            "locale",
            "is_verified",
            "email",
            ("premium_type", _optional_converter(users.PremiumType)),
        )
        self._deserializers[users.OwnUser] = deserializer
        return deserializer

    def deserialize_me(self, value: bytes) -> users.OwnUser:
        me = self._get_me_deserializer()(self._loads(value))
        me.app = self._app
        return me

    def _get_me_serializer(self) -> typing.Callable[[users.OwnUser], typing.Mapping[str, typing.Any]]:
        try:
            return self._serializers[users.OwnUser]
        except KeyError:
            pass

        serializer = _generate_json_serializer(
            *self._user_serialize_rules(), "is_mfa_enabled", "locale", "is_verified", "email", "premium_type"
        )
        self._serializers[users.OwnUser] = serializer
        return serializer

    def serialize_me(self, me: users.OwnUser) -> bytes:
        return self._dumps(self._get_me_serializer()(me))

    def _get_member_deserializer(self) -> typing.Callable[[typing.Mapping[str, typing.Any]], guilds.Member]:
        try:
            return self._deserializers[guilds.Member]
        except KeyError:
            pass

        deserializer = _generate_json_deserializer(
            guilds.Member,
            ("guild_id", snowflakes.Snowflake),
            "is_deaf",
            "is_mute",
            ("joined_at", time.iso8601_datetime_string_to_datetime),
            "nickname",
            (
                "premium_since",
                _optional_converter(time.iso8601_datetime_string_to_datetime),
            ),
            ("role_ids", _array_converter),
            ("user", self._get_user_deserializer()),
        )
        self._deserializers[guilds.Member] = deserializer
        return deserializer

    def deserialize_member(self, value: bytes) -> guilds.Member:
        member = self._get_member_deserializer()(self._loads(value))
        member.user = self._app
        return member

    def _get_member_serializer(self) -> typing.Callable[[guilds.Member], typing.Mapping[str, typing.Any]]:
        try:
            return self._serializers[guilds.Member]
        except KeyError:
            pass

        serializer = _generate_json_serializer(
            "guild_id",
            "is_deaf",
            "is_mute",
            "nickname",
            ("joined_at", lambda date: date.isoformat()),
            ("premium_since", lambda premium_since: premium_since.isoformat() if premium_since is not None else None),
            "role_ids",
            ("user", self._get_user_serializer()),
        )
        self._serializers[guilds.Member] = serializer
        return serializer

    def serialize_member(self, member: guilds.Member) -> bytes:
        return self._dumps(self._get_member_serializer()(member))

    def _get_message_deserializer(self) -> typing.Callable[[typing.Mapping[str, typing.Any]], messages.Message]:
        try:
            return self._deserializers[messages.Message]
        except KeyError:
            pass

        deserializer = _generate_json_deserializer()
        self._deserializers[messages.Message] = deserializer
        return deserializer

    def deserialize_message(self, value: bytes) -> messages.Message:
        raise NotImplementedError

    def _get_message_serializier(self) -> typing.Callable[[messages.Message], typing.Mapping[str, typing.Any]]:
        try:
            return self._serializers[messages.Message]
        except KeyError:
            pass

        attachment_serializer = _generate_json_serializer(
            "id", "url", "filename", "size", "proxy_url", "height", "width"
        )
        resource_with_proxy_serializer = _generate_json_serializer()
        embed_serializer = _generate_json_serializer(
            "title",
            "description",
            "url",
            "color",
            ("timestamp", lambda date: date.isoformat()),
            (
                "footer",
                _optional_converter(_generate_json_serializer("text", ("icon", resource_with_proxy_serializer))),
            ),
            ("image",),  # TODO: resource  # TODO: resource
            ("thumbnail",),  # TODO: same as image: resource
            ("video",),  # TODO: resource
            ("provider", _optional_converter(_generate_json_serializer("name", "url"))),
            ("author", _optional_converter(_generate_json_serializer("name", "url", ("icon",)))),  # TODO: resource
            ("fields", _array_converter(_generate_json_serializer("name", "value", "is_inline"))),
        )
        reaction_serializer = _generate_json_serializer("count", "emoji", "is_me")
        message_activity_serializer = _generate_json_serializer("type", "party_id")
        team_member_serializer = _generate_json_serializer(
            "membership_state", "permissions", "team_id", ("user", self._get_user_serializer())
        )
        team_serializer = _generate_json_serializer(
            "id", "icon_hash", ("members", _mapping_converter(lambda foo: foo, team_member_serializer)), "owner_id"
        )
        application_serializer = _generate_json_serializer(
            "id",
            "name",
            "description",
            "is_bot_public",
            "is_bot_code_grant_required",
            ("owner", self._get_user_serializer()),
            "rpc_origins",
            "summary",
            "verify_key",
            "icon_hash",
            ("team", _optional_converter(team_serializer)),
            "guild_id",
            "primary_sku_id",
            "slug",
            "cover_image_hash",
        )
        reference_serializer = _generate_json_serializer("id", "channel_id", "guild_id")
        serializer = _generate_json_serializer(
            "id",
            "channel_id",
            "guild_id",
            ("author", self._get_user_serializer()),
            ("member", _optional_converter(self._get_member_serializer())),
            "content",
            ("timestamp", lambda date: date.isoformat()),
            ("edited_timestamp", lambda date: date.isoformat() if date is not None else None),
            "is_tts",
            "is_mentioning_everyone",
            "user_mentions",
            "role_mentions",
            "channel_mentions",
            ("attachments", _array_converter(attachment_serializer)),
            ("embeds", _array_converter(embed_serializer)),
            ("reactions", _array_converter(reaction_serializer)),
            "is_pinned",
            "webhook_id",
            "type",
            ("activity", _optional_converter(message_activity_serializer)),
            ("application", _optional_converter(application_serializer)),
            ("message_reference", _optional_converter(reference_serializer)),
            "flags",
            "nonce",
        )
        self._serializers[messages.Message] = serializer
        return serializer

    def serialize_message(self, message: messages.Message) -> bytes:
        raise NotImplementedError

    def deserialize_presence(self, value: bytes) -> presences.MemberPresence:
        raise NotImplementedError

    def serialize_presence(self, presence: presences.MemberPresence) -> bytes:
        raise NotImplementedError

    def _get_role_deserializer(self) -> typing.Callable[[typing.Mapping[str, typing.Any]], guilds.Role]:
        try:
            return self._deserializers[guilds.Role]
        except KeyError:
            pass

        deserializer = _generate_json_deserializer(
            guilds.Role,
            ("app", None),
            ("id", snowflakes.Snowflake),
            "name",
            ("color", colors.Color),
            ("guild_id", snowflakes.Snowflake),
            "is_hoisted",
            "is_managed",
            "is_mentionable",
            ("permissions", permissions.Permissions),
            "position",
        )
        self._deserializers[guilds.Role] = deserializer
        return deserializer

    def deserialize_role(self, value: bytes) -> guilds.Role:
        role = self._get_role_deserializer()(self._loads(value))
        role.app = self._app
        return role

    def _user_serialize_rules(self) -> typing.Sequence[typing.Union[str, str]]:
        return (
            "id",
            "name",
            "color",
            "guild_id",
            "is_hoisted",
            "is_managed",
            "is_mentionable",
            "permissions",
            "position",
        )

    def _get_role_serializer(self) -> typing.Callable[[guilds.Role], typing.Mapping[str, typing.Any]]:
        try:
            return self._serializers[guilds.Role]
        except KeyError:
            pass

        serializer = _generate_json_serializer(*self._user_serialize_rules())
        self._serializers[guilds.Role] = serializer
        return serializer

    def serialize_role(self, role: guilds.Role) -> bytes:
        return self._dumps(self._get_role_serializer()(role))

    def _user_deserialize_rules(
        self,
    ) -> typing.Sequence[
        typing.Union[str, typing.Tuple[str, typing.Optional[typing.Callable[[typing.Any], typing.Any]]]]
    ]:
        return (
            ("id", snowflakes.Snowflake),
            ("app", None),
            "discriminator",
            "username",
            "avatar_hash",
            "is_bot",
            "is_system",
            ("flags", users.UserFlag),
        )

    def _get_user_deserializer(self) -> typing.Callable[[typing.Mapping[str, typing.Any]], users.User]:
        try:
            return self._deserializers[users.UserImpl]
        except KeyError:
            pass

        deserializer = _generate_json_deserializer(users.UserImpl, *self._user_deserialize_rules())
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
            "id", "discriminator", "username", "avatar_hash", "is_bot", "is_system", "flags"
        )
        self._serializers[users.UserImpl] = serializer
        return serializer

    def serialize_user(self, user: users.User) -> bytes:
        return self._dumps(self._get_user_serializer()(user))

    def _get_voice_state_deserializer(self) -> typing.Callable[[typing.Mapping[str, typing.Any]], voices.VoiceState]:
        try:
            return self._deserializers[voices.VoiceState]
        except KeyError:
            pass

        deserializer = _generate_json_deserializer(
            voices.VoiceState,
            ("app", None),
            ("channel_id", _optional_converter(snowflakes.Snowflake)),
            ("guild_id", snowflakes.Snowflake),
            "is_guild_deafened",
            "is_guild_muted",
            "is_self_deafened",
            "is_self_muted",
            "is_streaming",
            "is_suppressed",
            "is_video_enabled",
            ("user_id", snowflakes.Snowflake),
            ("member", self._get_member_deserializer()),
            "session_id",
        )
        self._deserializers[voices.VoiceState] = deserializer
        return deserializer

    def deserialize_voice_state(self, value: bytes) -> voices.VoiceState:
        voice_state = self._get_voice_state_deserializer()(self._loads(value))
        voice_state.app = self._app
        voice_state.member.user.app = self._app
        return voice_state

    def _get_voice_state_serializer(self) -> typing.Callable[[voices.VoiceState], typing.Mapping[str, typing.Any]]:
        try:
            return self._serializers[voices.VoiceState]
        except KeyError:
            pass

        serializer = _generate_json_serializer(
            "channel_id",
            "guild_id",
            "is_guild_deafened",
            "is_guild_muted",
            "is_self_deafened",
            "is_self_muted",
            "is_streaming",
            "is_suppressed",
            "is_video_enabled",
            "user_id",
            ("member", self._get_member_serializer()),
            "session_id",
        )
        self._serializers[voices.VoiceState] = serializer
        return serializer

    def serialize_voice_state(self, voice_state: voices.VoiceState) -> bytes:
        return self._dumps(self._get_voice_state_serializer()(voice_state))
