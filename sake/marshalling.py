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
from __future__ import annotations

__all__: typing.Final[typing.Sequence[str]] = [
    "JSONMarshaller",
    "ObjectMarshaller",
]

import abc
import asyncio
import base64
import datetime
import json
import logging
import typing

from hikari import channels
from hikari import colors
from hikari import embeds
from hikari import emojis
from hikari import files
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


_AsyncReaderT = typing.TypeVar("_AsyncReaderT", bound=files.AsyncReader)
_KeyT = typing.TypeVar("_KeyT")
_OtherKeyT = typing.TypeVar("_OtherKeyT")
_ValueT = typing.TypeVar("_ValueT")
_OtherValueT = typing.TypeVar("_OtherValueT")
JSONValue = typing.Union[str, int, float, bool, None]
"""A type hint used for representing the flat values supported for json serialization."""

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("hikari.sake.marshalling")


class ObjectMarshaller(abc.ABC, typing.Generic[_ValueT]):
    """Generic interface for object marshalling logic used within Sake."""

    __slots__: typing.Sequence[str] = ()

    @abc.abstractmethod
    def deserialize_prefixes(self, value: _ValueT, /) -> typing.List[str]:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_emoji(self, value: _ValueT, /) -> emojis.KnownCustomEmoji:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_emoji(self, emoji: emojis.KnownCustomEmoji, /) -> _ValueT:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_guild(self, value: _ValueT, /) -> guilds.GatewayGuild:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_guild(self, guild: guilds.GatewayGuild, /) -> _ValueT:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_guild_channel(self, value: _ValueT, /) -> channels.GuildChannel:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_guild_channel(self, channel: channels.GuildChannel, /) -> _ValueT:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_integration(self, value: _ValueT, /) -> guilds.Integration:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_integration(self, integration: guilds.Integration, /) -> _ValueT:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_invite(self, value: _ValueT, /) -> invites.InviteWithMetadata:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_invite(self, invite: invites.InviteWithMetadata, /) -> _ValueT:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_member(self, value: _ValueT, /) -> guilds.Member:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_member(self, member: guilds.Member, /) -> _ValueT:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_message(self, value: _ValueT, /) -> messages.Message:
        raise NotImplementedError

    # This is a special case serializer as it may have to asynchronously read "file" content.
    @abc.abstractmethod
    async def serialize_message(self, message: messages.Message, /) -> _ValueT:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_own_user(self, value: _ValueT, /) -> users.OwnUser:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_own_user(self, me: users.OwnUser, /) -> _ValueT:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_presence(self, value: _ValueT, /) -> presences.MemberPresence:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_presence(self, presence: presences.MemberPresence, /) -> _ValueT:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_role(self, value: _ValueT, /) -> guilds.Role:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_role(self, role: guilds.Role, /) -> _ValueT:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_user(self, value: _ValueT, /) -> users.User:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_user(self, user: users.User, /) -> _ValueT:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_voice_state(self, value: _ValueT, /) -> voices.VoiceState:
        raise NotImplementedError

    @abc.abstractmethod
    def serialize_voice_state(self, voice_state: voices.VoiceState, /) -> _ValueT:
        raise NotImplementedError


def _get_init_name(field: str, /) -> str:
    return field[1:] if field.startswith("_") else field


_UNDEFINABLE: typing.Final[str] = "UNDEFINABLE"
_PASS_KWARGS: typing.Final[str] = "PASS_KWARG"
"""Pass through the kwargs provided to this attribute's cast."""
_SET_KWARG: typing.Final[str] = "SET_KWARG"
"""Used to mark a field as being passed through the **kwargs argument."""

_DeserializeCastRuleT = typing.Union[typing.Callable[[typing.Any], typing.Any], str]


def _generate_map_deserializer(
    cls: typing.Type[_ValueT],
    /,
    *rules: typing.Union[
        str,
        typing.Tuple[str, _DeserializeCastRuleT],
        typing.Tuple[str, typing.AbstractSet[str]],
        typing.Tuple[str, _DeserializeCastRuleT, typing.AbstractSet[str]],
    ],
) -> typing.Callable[..., _ValueT]:
    # For the case of no supplied rules, return an empty lambda to avoid syntax errors later on.
    if not rules:
        return lambda _: cls()

    getters = []
    # Mypy false-positive complains about typing.Callable 'ha[ving] incompatible type "_SpecialForm"'
    # so we use AbstractSet in this check instead.
    casts = {rule[1] for rule in rules if isinstance(rule, tuple) and not isinstance(rule[1], typing.AbstractSet)}
    named_casts = {cast: f"c{i}" for i, cast in enumerate(casts)}

    for rule in rules:
        # Handles `str`
        if isinstance(rule, str):
            getters.append(f"{_get_init_name(rule)}=data[{rule!r}]")

        # Handles `typing.Tuple[str, typing.AbstractSet[str]]`
        elif isinstance(rule[1], typing.AbstractSet):
            name = rule[0]
            flags = rule[1]
            assert isinstance(flags, typing.AbstractSet), "This is already indirectly asserted above."

            if _UNDEFINABLE in flags:
                getters.append(f"{_get_init_name(name)}=data.get({name!r}, UNDEFINED)")
            else:
                getters.append(f"{_get_init_name(name)}=data[{name!r}]")

        # Handles
        # - `typing.Tuple[str, _DeserializeCastRuleT]`
        # - `typing.Tuple[str, _DeserializeCastRuleT, typing.AbstractSet[str]]`
        else:
            name = rule[0]
            current_cast = rule[1]
            # Mypy doesn't support scope narrowing tuple unions using length checks and thus assumes a 2 length tuple.
            flags = rule[2] if len(rule) > 2 else set()  # type: ignore[misc]
            init_name = _get_init_name(name)

            if current_cast == _SET_KWARG:
                getters.append(f"{init_name}=kwargs[{name!r}]")

            else:
                cast_name = named_casts[current_cast]
                call = f"(data[{name!r}])" if _PASS_KWARGS not in flags else f"(data[{name!r}], **kwargs)"
                getters.append(f"{init_name}={cast_name}{call} if {name!r} in data else UNDEFINED")

    globals_: typing.Dict[str, typing.Any] = {"cls": cls, "UNDEFINED": undefined.UNDEFINED}
    globals_.update((value, key) for key, value in named_casts.items())
    code = f"def deserialize(data, /, **kwargs): return cls({','.join(getters)})"
    _LOGGER.debug("generating mapping deserialize method for %r\n  %r", cls, code)
    exec(code, globals_)
    return typing.cast("typing.Callable[..., _ValueT]", globals_["deserialize"])


def _generate_map_serializer(
    *rules: typing.Union[
        str,
        typing.Tuple[str, typing.Callable[[typing.Any], typing.Any]],
        typing.Tuple[str, typing.AbstractSet[str]],
        typing.Tuple[str, typing.Callable[[typing.Any], typing.Any], typing.AbstractSet[str]],
    ],
) -> typing.Callable[[typing.Any], typing.MutableMapping[str, typing.Any]]:
    # For the case of no supplied rules, return an empty lambda to avoid syntax errors later on.
    if not rules:
        return lambda _: {}

    setters = []
    # Mypy false-positive complains about typing.Callable 'ha[ving] incompatible type "_SpecialForm"'
    # so we use AbstractSet in this check instead.
    casts = {rule[1] for rule in rules if isinstance(rule, tuple) and not isinstance(rule[1], typing.AbstractSet)}
    named_casts = {cast: f"c{i}" for i, cast in enumerate(casts)}

    for rule in rules:
        # Handles `str`
        if isinstance(rule, str):
            setters.append(f"data[{rule!r}] = m.{rule}")

        # Handles `typing.Tuple[str, typing.AbstractSet[str]]`
        elif isinstance(rule[1], typing.AbstractSet):
            name = rule[0]
            flags = rule[1]
            assert isinstance(flags, typing.AbstractSet), "This is already indirectly asserted above."

            if _UNDEFINABLE in flags:
                setters.append(f"if m.{name} is not UNDEFINED: data[{name!r}] = m.{name}")
            else:
                setters.append(f"data[{name!r}] = m.{name}")

        # Handles
        # - `typing.Tuple[str, typing.Callable[[typing.Any], typing.Any]]`
        # - `typing.Tuple[str, typing.Callable[[typing.Any], typing.Any], typing.AbstractSet[str]]`
        else:
            name = rule[0]
            cast = rule[1]
            # Mypy doesn't support scope narrowing tuple unions using length checks and thus assumes a 2 length tuple.
            flags = rule[2] if len(rule) > 2 else set()  # type: ignore[misc]
            cast_name = named_casts[cast]
            if _UNDEFINABLE in flags:
                setters.append(f"if m.{name} is not UNDEFINED: data[{name!r}] = {cast_name}(m.{name})")

            else:
                setters.append(f"data[{name!r}] = {cast_name}(m.{name})")

    globals_: typing.Dict[str, typing.Any] = {"UNDEFINED": undefined.UNDEFINED}
    globals_.update((value, key) for key, value in named_casts.items())
    code = "def serialize(m, /):\n  data = {};\n  " + "\n  ".join(setters) + "\n  return data"
    _LOGGER.debug("generating mapping serialize method:\n  %r", code)
    exec(code, globals_)
    return typing.cast("typing.Callable[[typing.Any], typing.MutableMapping[str, typing.Any]]", globals_["serialize"])


def _optional_cast(cast: typing.Callable[..., _OtherValueT], /) -> typing.Callable[..., typing.Optional[_OtherValueT]]:
    return lambda value, /, **kwargs: cast(value, **kwargs) if value is not None else None


def _cast_sequence(cast: typing.Callable[..., _OtherValueT], /) -> typing.Callable[..., typing.Sequence[_OtherValueT]]:
    return lambda array, /, **kwargs: [cast(value, **kwargs) for value in array]


def _cast_mapping(
    key_cast: typing.Callable[[_KeyT], _OtherKeyT], value_cast: typing.Callable[..., _OtherValueT], /
) -> typing.Callable[..., typing.MutableMapping[_OtherKeyT, _OtherValueT]]:
    return lambda mapping, /, **kwargs: {key_cast(key): value_cast(value, **kwargs) for key, value in mapping.items()}


def _no_cast(value: _ValueT, /) -> _ValueT:
    return value


def _deserialize_timedelta(delta: float, /) -> datetime.timedelta:
    return datetime.timedelta(seconds=delta)


def _serialize_timedelta(delta: datetime.timedelta, /) -> float:
    return delta.total_seconds()


def _deserialize_datetime(date: str, /) -> datetime.datetime:
    return time.iso8601_datetime_string_to_datetime(date)


def _serialize_datetime(date: datetime.datetime, /) -> str:
    return date.isoformat()


def _user_deserialize_rules() -> typing.Sequence[typing.Union[str, typing.Tuple[str, _DeserializeCastRuleT]]]:
    return (
        ("id", snowflakes.Snowflake),
        ("app", _SET_KWARG),
        "discriminator",
        "username",
        "avatar_hash",
        "is_bot",
        "is_system",
        ("flags", users.UserFlag),
    )


def _user_serialize_rules() -> typing.Sequence[typing.Union[str, str]]:
    return ("id", "discriminator", "username", "avatar_hash", "is_bot", "is_system", "flags")


class MappingMarshaller(ObjectMarshaller[_ValueT], abc.ABC):
    __slots__: typing.Sequence[str] = ("_app",)

    # These special case serializers require an asynchronous environment to read referenced data.
    _async_serializers: typing.MutableMapping[
        typing.Any,
        typing.Callable[[typing.Any], typing.Coroutine[typing.Any, typing.Any, typing.MutableMapping[str, typing.Any]]],
    ] = {}
    _deserializers: typing.MutableMapping[typing.Any, typing.Callable[..., typing.Any]] = {}
    _serializers: typing.MutableMapping[
        typing.Any, typing.Callable[[typing.Any], typing.MutableMapping[str, typing.Any]]
    ] = {}

    def __init__(self, app: traits.RESTAware) -> None:
        self._app = app

    @abc.abstractmethod
    def dumps(self, data: typing.Mapping[str, typing.Any], /) -> _ValueT:
        raise NotImplementedError

    @abc.abstractmethod
    def loads(self, data: _ValueT, /) -> typing.MutableMapping[str, typing.Any]:
        raise NotImplementedError

    def deserialize_prefixes(self, value: _ValueT, /) -> typing.List[str]:
        return [prefix.decode() for prefix in value]

    def _get_emoji_deserializer(self) -> typing.Callable[..., emojis.KnownCustomEmoji]:
        try:
            return self._deserializers[emojis.KnownCustomEmoji]
        except KeyError:
            pass

        deserialize = _generate_map_deserializer(
            emojis.KnownCustomEmoji,
            ("id", snowflakes.Snowflake),
            "name",
            "is_animated",
            ("app", _SET_KWARG),
            ("guild_id", snowflakes.Snowflake),
            ("role_ids", _cast_sequence(snowflakes.Snowflake)),
            ("user", _optional_cast(self._get_user_deserializer()), {_PASS_KWARGS}),
            "is_colons_required",
            "is_managed",
            "is_available",
        )
        self._deserializers[emojis.KnownCustomEmoji] = deserialize
        return deserialize

    def deserialize_emoji(self, value: _ValueT, /) -> emojis.KnownCustomEmoji:
        return self._get_emoji_deserializer()(self.loads(value), app=self._app)

    def _get_emoji_serializer(self) -> typing.Callable[[emojis.KnownCustomEmoji], typing.Mapping[str, typing.Any]]:
        try:
            return self._serializers[emojis.KnownCustomEmoji]
        except KeyError:
            pass

        serialize = _generate_map_serializer(
            "id",
            "name",
            "is_animated",
            "guild_id",
            "role_ids",
            ("user", _optional_cast(self._get_user_serializer())),
            "is_animated",
            "is_colons_required",
            "is_managed",
            "is_available",
        )
        self._serializers[emojis.KnownCustomEmoji] = serialize
        return serialize

    def serialize_emoji(self, emoji: emojis.KnownCustomEmoji, /) -> _ValueT:
        return self.dumps(self._get_emoji_serializer()(emoji))

    def _get_guild_deserializer(self) -> typing.Callable[..., guilds.GatewayGuild]:
        try:
            return self._deserializers[guilds.GatewayGuild]
        except KeyError:
            pass

        deserialize = _generate_map_deserializer(
            guilds.GatewayGuild,
            ("app", _SET_KWARG),
            ("features", _cast_sequence(guilds.GuildFeature)),
            ("id", snowflakes.Snowflake),
            "icon_hash",
            "name",
            ("application_id", _optional_cast(snowflakes.Snowflake)),
            ("afk_channel_id", _optional_cast(snowflakes.Snowflake)),
            ("afk_timeout", _deserialize_timedelta),
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
            ("public_updates_channel_id", _optional_cast(snowflakes.Snowflake)),
            "region",
            ("rules_channel_id", _optional_cast(snowflakes.Snowflake)),
            "splash_hash",
            ("system_channel_flags", guilds.GuildSystemChannelFlag),
            ("system_channel_id", _optional_cast(snowflakes.Snowflake)),
            "vanity_url_code",
            ("verification_level", guilds.GuildVerificationLevel),
            ("widget_channel_id", _optional_cast(snowflakes.Snowflake)),
            "is_large",
            ("joined_at", _optional_cast(_deserialize_datetime)),
            "member_count",
        )
        self._deserializers[guilds.GatewayGuild] = deserialize
        return deserialize

    def deserialize_guild(self, value: _ValueT, /) -> guilds.GatewayGuild:
        return self._get_guild_deserializer()(self.loads(value), app=self._app)

    def _get_guild_serializer(self) -> typing.Callable[[guilds.GatewayGuild], typing.Mapping[str, typing.Any]]:
        try:
            return self._serializers[guilds.GatewayGuild]
        except KeyError:
            pass

        serialize = _generate_map_serializer(
            "features",
            "id",
            "icon_hash",
            "name",
            "application_id",
            "afk_channel_id",
            ("afk_timeout", _serialize_timedelta),
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
            ("joined_at", _serialize_datetime),
            "member_count",
        )
        self._serializers[guilds.GatewayGuild] = serialize
        return serialize

    def serialize_guild(self, guild: guilds.GatewayGuild, /) -> _ValueT:
        return self.dumps(self._get_guild_serializer()(guild))

    def _guild_channel_deserialize_rules(
        self,
    ) -> typing.Sequence[typing.Union[str, typing.Tuple[str, _DeserializeCastRuleT]]]:
        deserialize_overwrite = _generate_map_deserializer(
            channels.PermissionOverwrite,
            ("id", snowflakes.Snowflake),
            ("type", channels.PermissionOverwriteType),
            ("allow", permissions.Permissions),
            ("deny", permissions.Permissions),
        )
        return (
            ("app", _SET_KWARG),
            ("id", snowflakes.Snowflake),
            "name",
            ("type", channels.ChannelType),
            ("guild_id", snowflakes.Snowflake),
            "position",
            ("permission_overwrites", _cast_mapping(snowflakes.Snowflake, deserialize_overwrite)),
            "is_nsfw",
            ("parent_id", _optional_cast(snowflakes.Snowflake)),
        )

    def _get_category_deserializer(self) -> typing.Callable[..., channels.GuildCategory]:
        try:
            return self._deserializers[channels.GuildCategory]
        except KeyError:
            deserialize = _generate_map_deserializer(channels.GuildCategory, *self._guild_channel_deserialize_rules())
            self._deserializers[channels.GuildCategory] = deserialize
            return deserialize

    def _get_text_channel_deserializer(
        self,
    ) -> typing.Callable[..., channels.GuildTextChannel]:
        try:
            return self._deserializers[channels.GuildTextChannel]
        except KeyError:
            deserialize = _generate_map_deserializer(
                channels.GuildTextChannel,
                *self._guild_channel_deserialize_rules(),
                "topic",
                ("last_message_id", _optional_cast(snowflakes.Snowflake)),
                ("rate_limit_per_user", _deserialize_timedelta),
                ("last_pin_timestamp", _optional_cast(_deserialize_datetime)),
            )
            self._deserializers[channels.GuildTextChannel] = deserialize
            return deserialize

    def _get_news_channel_deserializer(
        self,
    ) -> typing.Callable[..., channels.GuildNewsChannel]:
        try:
            return self._deserializers[channels.GuildNewsChannel]
        except KeyError:
            deserialize = _generate_map_deserializer(
                channels.GuildNewsChannel,
                *self._guild_channel_deserialize_rules(),
                "topic",
                ("last_message_id", _optional_cast(snowflakes.Snowflake)),
                ("last_pin_timestamp", _optional_cast(_deserialize_datetime)),
            )
            self._deserializers[channels.GuildNewsChannel] = deserialize
            return deserialize

    def _get_store_channel_deserializer(
        self,
    ) -> typing.Callable[..., channels.GuildStoreChannel]:
        try:
            return self._deserializers[channels.GuildStoreChannel]
        except KeyError:
            deserialize = _generate_map_deserializer(
                channels.GuildStoreChannel, *self._guild_channel_deserialize_rules()
            )
            self._deserializers[channels.GuildStoreChannel] = deserialize
            return deserialize

    def _get_voice_channel_deserializer(
        self,
    ) -> typing.Callable[..., channels.GuildVoiceChannel]:
        try:
            return self._deserializers[channels.GuildVoiceChannel]
        except KeyError:
            deserialize = _generate_map_deserializer(
                channels.GuildVoiceChannel, *self._guild_channel_deserialize_rules(), "bitrate", "user_limit"
            )
            self._deserializers[channels.GuildVoiceChannel] = deserialize
            return deserialize

    def deserialize_guild_channel(self, value: _ValueT, /) -> channels.GuildChannel:
        data = self.loads(value)
        channel_type = channels.ChannelType(data.get("type", -1))

        if channel_type is channels.ChannelType.GUILD_CATEGORY:
            return self._get_category_deserializer()(data, app=self._app)

        if channel_type is channels.ChannelType.GUILD_TEXT:
            return self._get_text_channel_deserializer()(data, app=self._app)

        if channel_type is channels.ChannelType.GUILD_NEWS:
            return self._get_news_channel_deserializer()(data, app=self._app)

        if channel_type is channels.ChannelType.GUILD_STORE:
            return self._get_store_channel_deserializer()(data, app=self._app)

        if channel_type is channels.ChannelType.GUILD_VOICE:
            return self._get_voice_channel_deserializer()(data, app=self._app)

        raise NotImplementedError(f"Deserialization not implemented for {channel_type} channel type")

    def _guild_channel_serialize_rules(
        self,
    ) -> typing.Sequence[typing.Union[str, typing.Tuple[str, typing.Callable[[typing.Any], typing.Any]]]]:
        serialize_overwrite = _generate_map_serializer("id", "type", "allow", "deny")
        return (
            "id",
            "name",
            "type",
            "guild_id",
            "position",
            ("permission_overwrites", _cast_mapping(_no_cast, serialize_overwrite)),
            "is_nsfw",
            "parent_id",
        )

    def _get_category_serializer(
        self,
    ) -> typing.Callable[[channels.GuildCategory], typing.Mapping[str, typing.Any]]:
        try:
            return self._serializers[channels.GuildCategory]
        except KeyError:
            serialize = _generate_map_serializer(*self._guild_channel_serialize_rules())
            self._serializers[channels.GuildCategory] = serialize
            return serialize

    def _get_text_channel_serializer(
        self,
    ) -> typing.Callable[[channels.GuildTextChannel], typing.Mapping[str, typing.Any]]:
        try:
            return self._serializers[channels.GuildTextChannel]
        except KeyError:
            serialize = _generate_map_serializer(
                *self._guild_channel_serialize_rules(),
                "topic",
                "last_message_id",
                ("rate_limit_per_user", _serialize_timedelta),
                ("last_pin_timestamp", _optional_cast(_serialize_datetime)),
            )
            self._serializers[channels.GuildTextChannel] = serialize
            return serialize

    def _get_news_channel_serializer(
        self,
    ) -> typing.Callable[[channels.GuildNewsChannel], typing.Mapping[str, typing.Any]]:
        try:
            return self._serializers[channels.GuildNewsChannel]
        except KeyError:
            serialize = _generate_map_serializer(
                *self._guild_channel_serialize_rules(),
                "topic",
                "last_message_id",
                ("last_pin_timestamp", _optional_cast(_serialize_datetime)),
            )
            self._serializers[channels.GuildNewsChannel] = serialize
            return serialize

    def _get_store_channel_serializer(
        self,
    ) -> typing.Callable[[channels.GuildStoreChannel], typing.Mapping[str, typing.Any]]:
        try:
            return self._serializers[channels.GuildStoreChannel]
        except KeyError:
            serialize = _generate_map_serializer(*self._guild_channel_serialize_rules())
            self._serializers[channels.GuildStoreChannel] = serialize
            return serialize

    def _get_voice_channel_serializer(
        self,
    ) -> typing.Callable[[channels.GuildVoiceChannel], typing.Mapping[str, typing.Any]]:
        try:
            return self._serializers[channels.GuildVoiceChannel]
        except KeyError:
            serialize = _generate_map_serializer(*self._guild_channel_serialize_rules(), "bitrate", "user_limit")
            self._serializers[channels.GuildVoiceChannel] = serialize
            return serialize

    def serialize_guild_channel(self, channel: channels.GuildChannel, /) -> _ValueT:
        if isinstance(channel, channels.GuildCategory):
            data = self._get_category_serializer()(channel)

        elif isinstance(channel, channels.GuildTextChannel):
            data = self._get_text_channel_serializer()(channel)

        elif isinstance(channel, channels.GuildNewsChannel):
            data = self._get_news_channel_serializer()(channel)

        elif isinstance(channel, channels.GuildStoreChannel):
            data = self._get_store_channel_serializer()(channel)

        elif isinstance(channel, channels.GuildVoiceChannel):
            data = self._get_voice_channel_serializer()(channel)

        else:
            raise NotImplementedError(f"Serializer not implemented for {type(channel).__name__} channel type")

        return self.dumps(data)

    def _get_integration_deserializer(self) -> typing.Callable[..., guilds.Integration]:
        try:
            return self._deserializers[guilds.Integration]
        except KeyError:
            pass

        deserialize_application = _generate_map_deserializer(
            guilds.IntegrationApplication,
            ("id", snowflakes.Snowflake),
            "name",
            "icon_hash",
            "summary",
            "description",
            ("bot", _optional_cast(self._get_user_deserializer()), {_PASS_KWARGS}),
        )
        deserialize = _generate_map_deserializer(
            guilds.Integration,
            ("account", _generate_map_deserializer(guilds.IntegrationAccount, "id", "name")),
            ("id", snowflakes.Snowflake),
            "name",
            ("type", guilds.IntegrationType),
            ("expire_behavior", _optional_cast(guilds.IntegrationExpireBehaviour)),
            ("expire_grace_period", _optional_cast(_deserialize_timedelta)),
            "is_enabled",
            "is_syncing",
            "is_emojis_enabled",
            "is_revoked",
            ("last_synced_at", _optional_cast(_deserialize_datetime)),
            ("role_id", _optional_cast(snowflakes.Snowflake)),
            ("user", _optional_cast(self._get_user_deserializer()), {_PASS_KWARGS}),
            "subscriber_count",
            ("application", _optional_cast(deserialize_application), {_PASS_KWARGS}),
            ("guild_id", snowflakes.Snowflake),
        )
        self._deserializers[guilds.Integration] = deserialize
        return deserialize

    def deserialize_integration(self, value: _ValueT, /) -> guilds.Integration:
        return self._get_integration_deserializer()(self.loads(value), app=self._app)

    def _get_integration_serializer(
        self,
    ) -> typing.Callable[[guilds.Integration], typing.MutableMapping[str, typing.Any]]:
        try:
            return self._serializers[guilds.Integration]
        except KeyError:
            pass

        serialize_application = _generate_map_serializer(
            "id", "name", "icon_hash", "summary", "description", ("bot", _optional_cast(self._get_user_serializer()))
        )
        serialize = _generate_map_serializer(
            ("account", _generate_map_serializer("id", "name")),
            "id",
            "name",
            "type",
            "expire_behavior",
            ("expire_grace_period", _optional_cast(_serialize_timedelta)),
            "is_enabled",
            "is_syncing",
            "is_emojis_enabled",
            "is_revoked",
            ("last_synced_at", _optional_cast(_serialize_datetime)),
            "role_id",
            ("user", _optional_cast(self._get_user_serializer())),
            "subscriber_count",
            ("application", _optional_cast(serialize_application)),
            "guild_id",
        )
        self._serializers[guilds.Integration] = serialize
        return serialize

    def serialize_integration(self, integration: guilds.Integration, /) -> _ValueT:
        return self.dumps(self._get_integration_serializer()(integration))

    def _get_invite_deserializer(
        self,
    ) -> typing.Callable[..., invites.InviteWithMetadata]:
        try:
            return self._deserializers[invites.InviteWithMetadata]
        except KeyError:
            pass

        deserialize_invite_guild = _generate_map_deserializer(
            invites.InviteGuild,
            ("app", _SET_KWARG),
            ("features", _cast_sequence(guilds.GuildFeature)),
            ("id", snowflakes.Snowflake),
            "icon_hash",
            "name",
            "splash_hash",
            "banner_hash",
            "description",
            ("verification_level", guilds.GuildVerificationLevel),
            "vanity_url_code",
        )
        deserialize_partial_channel = _generate_map_deserializer(
            channels.PartialChannel,
            ("app", _SET_KWARG),
            ("id", snowflakes.Snowflake),
            "name",
            ("type", channels.ChannelType),
        )

        deserialize = _generate_map_deserializer(
            invites.InviteWithMetadata,
            ("app", _SET_KWARG),
            "code",
            ("guild", _optional_cast(deserialize_invite_guild), {_PASS_KWARGS}),
            ("guild_id", _optional_cast(snowflakes.Snowflake)),
            ("channel", _optional_cast(deserialize_partial_channel), {_PASS_KWARGS}),
            ("channel_id", snowflakes.Snowflake),
            ("inviter", _optional_cast(self._get_user_deserializer()), {_PASS_KWARGS}),
            ("target_user", _optional_cast(self._get_user_deserializer()), {_PASS_KWARGS}),
            ("target_user_type", _optional_cast(invites.TargetUserType)),
            "approximate_active_member_count",
            "approximate_member_count",
            "uses",
            "max_uses",
            ("max_age", _optional_cast(_deserialize_timedelta)),
            "is_temporary",
            ("created_at", _deserialize_datetime),
        )
        self._deserializers[invites.InviteWithMetadata] = deserialize
        return deserialize

    def deserialize_invite(self, value: _ValueT, /) -> invites.InviteWithMetadata:
        return self._get_invite_deserializer()(self.loads(value), app=self._app)

    def _get_invite_serializer(self) -> typing.Callable[[invites.InviteWithMetadata], typing.Mapping[str, typing.Any]]:
        try:
            return self._serializers[invites.InviteWithMetadata]
        except KeyError:
            pass

        serialize_guild_invite = _generate_map_serializer(
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
        serialize_partial_channel = _generate_map_serializer("id", "name", "type")
        serialize = _generate_map_serializer(
            "code",
            ("guild", _optional_cast(serialize_guild_invite)),
            "guild_id",
            ("channel", _optional_cast(serialize_partial_channel)),
            "channel_id",
            ("inviter", _optional_cast(self._get_user_serializer())),
            ("target_user", _optional_cast(self._get_user_serializer())),
            "target_user_type",
            "approximate_active_member_count",
            "approximate_member_count",
            "uses",
            "max_uses",
            ("max_age", _optional_cast(_serialize_timedelta)),
            "is_temporary",
            ("created_at", _serialize_datetime),
        )
        self._serializers[invites.InviteWithMetadata] = serialize
        return serialize

    def serialize_invite(self, invite: invites.InviteWithMetadata, /) -> _ValueT:
        return self.dumps(self._get_invite_serializer()(invite))

    def _get_member_deserializer(self) -> typing.Callable[..., guilds.Member]:
        try:
            return self._deserializers[guilds.Member]
        except KeyError:
            pass

        deserialize = _generate_map_deserializer(
            guilds.Member,
            ("guild_id", snowflakes.Snowflake),
            ("is_deaf", {_UNDEFINABLE}),
            ("is_mute", {_UNDEFINABLE}),
            ("joined_at", _deserialize_datetime),
            ("nickname", {_UNDEFINABLE}),
            (
                "premium_since",
                _optional_cast(_deserialize_datetime),
            ),
            ("role_ids", _cast_sequence(snowflakes.Snowflake)),
            ("user", self._get_user_deserializer(), {_PASS_KWARGS}),
        )
        self._deserializers[guilds.Member] = deserialize
        return deserialize

    def deserialize_member(self, value: _ValueT, /) -> guilds.Member:
        return self._get_member_deserializer()(self.loads(value), app=self._app)

    def _get_member_serializer(self) -> typing.Callable[[guilds.Member], typing.Mapping[str, typing.Any]]:
        try:
            return self._serializers[guilds.Member]
        except KeyError:
            pass

        serialize = _generate_map_serializer(
            "guild_id",
            ("is_deaf", {_UNDEFINABLE}),
            ("is_mute", {_UNDEFINABLE}),
            ("nickname", {_UNDEFINABLE}),
            ("joined_at", _serialize_datetime),
            ("premium_since", _optional_cast(_serialize_datetime)),
            "role_ids",
            ("user", self._get_user_serializer()),
        )
        self._serializers[guilds.Member] = serialize
        return serialize

    def serialize_member(self, member: guilds.Member, /) -> _ValueT:
        return self.dumps(self._get_member_serializer()(member))

    @staticmethod
    def deserialize_resource(data: typing.Mapping[str, typing.Any], /) -> files.Resource[files.AsyncReader]:
        resource_type = data.get("type")
        if resource_type == "url":
            return typing.cast("files.Resource[files.AsyncReader]", files.URL(url=data["url"]))

        if resource_type == "file":
            return typing.cast(
                "files.Resource[files.AsyncReader]",
                files.File(files.ensure_path(data["path"]), filename=data["filename"], spoiler=data["is_spoiler"]),
            )

        if resource_type == "ValueT":
            return typing.cast(
                "files.Resource[files.AsyncReader]",
                files.Bytes(
                    base64.b64decode(data["data"]),
                    data["filename"],
                    mimetype=data["mimetype"],
                    spoiler=data["is_spoiler"],
                ),
            )

        raise RuntimeError(f"Missing marshalling implementation for file type {resource_type!r}")

    def _get_message_deserializer(self) -> typing.Callable[..., messages.Message]:
        try:
            return self._deserializers[messages.Message]
        except KeyError:
            pass

        deserialize_attachment = _generate_map_deserializer(
            messages.Attachment, ("id", snowflakes.Snowflake), "url", "filename", "size", "proxy_url", "height", "width"
        )
        deserialize_resource_rules = (("resource", self.deserialize_resource),)
        deserialize_resource_with_proxy_rules = (
            *deserialize_resource_rules,
            ("proxy_resource", _optional_cast(self.deserialize_resource)),
        )
        deserialize_image = _generate_map_deserializer(
            embeds.EmbedImage, *deserialize_resource_with_proxy_rules, "height", "width"
        )
        deserialize_video = _generate_map_deserializer(
            embeds.EmbedVideo, *deserialize_resource_rules, "height", "width"
        )
        deserialize_resource_with_proxy = _generate_map_deserializer(
            embeds.EmbedResourceWithProxy, *deserialize_resource_with_proxy_rules
        )
        deserialize_author = _generate_map_deserializer(
            embeds.EmbedAuthor, "name", "url", ("icon", _optional_cast(deserialize_resource_with_proxy))
        )
        deserialize_provider = _generate_map_deserializer(embeds.EmbedProvider, "name", "url")
        deserialize_footer = _generate_map_deserializer(
            embeds.EmbedFooter, "text", ("icon", _optional_cast(deserialize_resource_with_proxy))
        )

        def deserialize_field(data: typing.Mapping[str, typing.Any], /) -> embeds.EmbedField:
            return embeds.EmbedField(name=data["name"], value=data["value"], inline=data["is_inline"])

        def deserialize_embed(data: typing.Mapping[str, typing.Any], /) -> embeds.Embed:
            color: typing.Optional[colors.Color] = None
            if (raw_color := data["color"]) is not None:
                color = colors.Color(raw_color)

            timestamp: typing.Optional[datetime.datetime] = None
            if (raw_timestamp := data["timestamp"]) is not None:
                timestamp = _deserialize_datetime(raw_timestamp)

            image: typing.Optional[embeds.EmbedImage[files.AsyncReader]] = None
            if (raw_image := data["image"]) is not None:
                image = deserialize_image(raw_image)

            thumbnail: typing.Optional[embeds.EmbedImage[files.AsyncReader]] = None
            if (raw_thumbnail := data["thumbnail"]) is not None:
                thumbnail = deserialize_image(raw_thumbnail)

            video: typing.Optional[embeds.EmbedVideo[files.AsyncReader]] = None
            if (raw_video := data["video"]) is not None:
                video = deserialize_video(raw_video)

            author: typing.Optional[embeds.EmbedAuthor] = None
            if (raw_author := data["author"]) is not None:
                author = deserialize_author(raw_author)

            provider: typing.Optional[embeds.EmbedProvider] = None
            if (raw_provider := data["provider"]) is not None:
                provider = deserialize_provider(raw_provider)

            footer: typing.Optional[embeds.EmbedFooter] = None
            if (raw_footer := data["footer"]) is not None:
                footer = deserialize_footer(raw_footer)

            return embeds.Embed.from_received_embed(
                title=data["title"],
                description=data["description"],
                url=data["url"],
                color=color,
                timestamp=timestamp,
                image=image,
                thumbnail=thumbnail,
                video=video,
                author=author,
                provider=provider,
                footer=footer,
                fields=list(map(deserialize_field, data["fields"])),
            )

        deserialize_reaction = _generate_map_deserializer(
            messages.Reaction, "count", ("emoji", self.deserialize_unknown_emoji), "is_me"
        )
        deserialize_activity = _generate_map_deserializer(
            messages.MessageActivity, ("type", messages.MessageActivityType), "party_id"
        )
        deserialize_application = _generate_map_deserializer(
            messages.MessageApplication,
            ("id", snowflakes.Snowflake),
            "name",
            "description",
            "icon_hash",
            "summary",
            "cover_image_hash",
            ("primary_sku_id", _optional_cast(snowflakes.Snowflake)),
        )
        deserialize_crosspost = _generate_map_deserializer(
            messages.MessageCrosspost,
            ("app", _SET_KWARG),
            ("id", snowflakes.Snowflake),
            ("channel_id", snowflakes.Snowflake),
            ("guild_id", _optional_cast(snowflakes.Snowflake)),
        )
        deserialize = _generate_map_deserializer(
            messages.Message,
            ("app", _SET_KWARG),
            ("id", snowflakes.Snowflake),
            ("channel_id", snowflakes.Snowflake),
            ("guild_id", _optional_cast(snowflakes.Snowflake)),
            ("author", self._get_user_deserializer(), {_PASS_KWARGS}),
            ("member", _optional_cast(self._get_member_deserializer()), {_PASS_KWARGS}),
            "content",
            ("timestamp", _deserialize_datetime),
            ("edited_timestamp", _optional_cast(_deserialize_datetime)),
            "is_tts",
            "is_mentioning_everyone",
            ("user_mentions", _cast_sequence(snowflakes.Snowflake)),
            ("role_mentions", _cast_sequence(snowflakes.Snowflake)),
            ("channel_mentions", _cast_sequence(snowflakes.Snowflake)),
            ("attachments", _cast_sequence(deserialize_attachment)),
            ("embeds", _cast_sequence(deserialize_embed)),
            ("reactions", _cast_sequence(deserialize_reaction)),
            "is_pinned",
            ("webhook_id", _optional_cast(snowflakes.Snowflake)),
            ("type", messages.MessageType),
            ("activity", _optional_cast(deserialize_activity)),
            ("application", _optional_cast(deserialize_application)),
            ("message_reference", _optional_cast(deserialize_crosspost), {_PASS_KWARGS}),
            ("flags", _optional_cast(messages.MessageFlag)),
            "nonce",
        )
        self._deserializers[messages.Message] = deserialize
        return deserialize

    def deserialize_message(self, value: _ValueT, /) -> messages.Message:
        return self._get_message_deserializer()(self.loads(value), app=self._app)

    async def _serialize_resource(
        self, resource: files.Resource[_AsyncReaderT]
    ) -> typing.MutableMapping[str, typing.Any]:
        data: typing.Mapping[str, typing.Any]
        # For some reason despite the isinstance type narrowing MYPY always thinks resource is <nothing>
        if isinstance(resource, files.URL):
            return {"type": "url", "url": resource.url}  # type: ignore[attr-defined]

        # TODO: is there any point in serializing a local file or should this just raise
        if isinstance(resource, files.File):
            return {
                "type": "file",
                "path": str(resource.path),  # type: ignore[attr-defined]
                "is_spoiler": resource.is_spoiler,  # type: ignore[attr-defined]
                "filename": resource.filename,  # type: ignore[attr-defined]
            }

        if isinstance(resource, files.Bytes):
            return {
                "type": "bytes",
                "data": base64.b64encode(await resource.read()),  # type: ignore[attr-defined]
                "mimetype": resource.mimetype,  # type: ignore[attr-defined]
                "is_spoiler": resource.is_spoiler,  # type: ignore[attr-defined]
                "filename": resource.filename,  # type: ignore[attr-defined]
            }

        raise RuntimeError(f"Missing marshalling implementation for {type(resource)!r} file implementation")

    def _get_message_serializer(
        self,
    ) -> typing.Callable[[messages.Message], typing.Coroutine[typing.Any, typing.Any, typing.Mapping[str, typing.Any]]]:
        try:
            return self._async_serializers[messages.Message]
        except KeyError:
            pass

        serialize_attachment = _generate_map_serializer("id", "url", "filename", "size", "proxy_url", "height", "width")
        serialize_provider = _generate_map_serializer("name", "url")
        serialize_filed = _generate_map_serializer("name", "value", "is_inline")

        async def serialize_resource_with_proxy(
            resource: embeds.EmbedResourceWithProxy[files.AsyncReader],
            /,
        ) -> typing.MutableMapping[str, typing.Any]:
            proxy_resource: typing.Optional[typing.MutableMapping[str, typing.Any]] = None
            if resource.proxy_resource is not None:
                proxy_resource = await self._serialize_resource(resource.proxy_resource)

            return {"resource": await self._serialize_resource(resource.resource), "proxy_resource": proxy_resource}

        async def serialize_image(
            image: embeds.EmbedImage[files.AsyncReader],
            /,
        ) -> typing.MutableMapping[str, typing.Any]:
            data = await serialize_resource_with_proxy(image)
            data["height"] = image.height
            data["width"] = image.width
            return data

        async def serialize_video(
            video: embeds.EmbedVideo[files.AsyncReader],
            /,
        ) -> typing.MutableMapping[str, typing.Any]:
            return {
                "resource": await self._serialize_resource(video.resource),
                "height": video.height,
                "width": video.width,
            }

        async def serialize_footer(footer: embeds.EmbedFooter, /) -> typing.MutableMapping[str, typing.Any]:
            return {
                "text": footer.text,
                "icon": await serialize_resource_with_proxy(footer.icon) if footer.icon is not None else None,
            }

        async def serialize_author(author: embeds.EmbedAuthor, /) -> typing.MutableMapping[str, typing.Any]:
            return {
                "name": author.name,
                "url": author.url,
                "icon": await serialize_resource_with_proxy(author.icon) if author.icon is not None else None,
            }

        async def serialize_embed(embed: embeds.Embed, /) -> typing.MutableMapping[str, typing.Any]:
            return {
                "title": embed.title,
                "description": embed.description,
                "url": embed.url,
                "color": embed.colour,
                "timestamp": _serialize_datetime(embed.timestamp) if embed.timestamp is not None else None,
                "footer": await serialize_footer(embed.footer) if embed.footer is not None else None,
                "image": await serialize_image(embed.image) if embed.image is not None else None,
                "thumbnail": await serialize_image(embed.thumbnail) if embed.thumbnail is not None else None,
                "video": await serialize_video(embed.video) if embed.video is not None else None,
                "provider": serialize_provider(embed.provider) if embed.provider is not None else None,
                "author": await serialize_author(embed.author) if embed.author is not None else None,
                "fields": list(map(serialize_filed, embed.fields)),
            }

        serialize_reaction = _generate_map_serializer("count", ("emoji", self.serialize_unknown_emoji), "is_me")
        serialize_message_activity = _generate_map_serializer("type", "party_id")
        serialize_application = _generate_map_serializer(
            "id", "name", "description", "icon_hash", "summary", "cover_image_hash", "primary_sku_id"
        )
        serialize_reference = _generate_map_serializer("id", "channel_id", "guild_id")
        serialize_user = self._get_user_serializer()
        serialize_member_ = self._get_member_serializer()

        async def serialize(message: messages.Message, /) -> typing.MutableMapping[str, typing.Any]:
            message_reference: typing.Optional[typing.MutableMapping[str, typing.Any]] = None
            if message.message_reference is not None:
                message_reference = serialize_reference(message.message_reference)

            edited_timestamp: typing.Optional[str] = None
            if message.edited_timestamp is not None:
                edited_timestamp = _serialize_datetime(message.edited_timestamp)

            return {
                "id": message.id,
                "channel_id": message.channel_id,
                "guild_id": message.guild_id,
                "author": serialize_user(message.author),
                "member": serialize_member_(message.member) if message.member is not None else None,
                "content": message.content,
                "timestamp": _serialize_datetime(message.timestamp),
                "edited_timestamp": edited_timestamp,
                "is_tts": message.is_tts,
                "is_mentioning_everyone": message.is_mentioning_everyone,
                "user_mentions": message.user_mentions,
                "role_mentions": message.role_mentions,
                "channel_mentions": message.channel_mentions,
                "attachments": list(map(serialize_attachment, message.attachments)),
                "embeds": await asyncio.gather(*map(serialize_embed, message.embeds)),
                "reactions": list(map(serialize_reaction, message.reactions)),
                "is_pinned": message.is_pinned,
                "webhook_id": message.webhook_id,
                "type": message.type,
                "activity": serialize_message_activity(message.activity) if message.activity is not None else None,
                "application": serialize_application(message.application) if message.application is not None else None,
                "message_reference": message_reference,
                "flags": message.flags,
                "nonce": message.nonce,
            }

        self._async_serializers[messages.Message] = serialize
        return serialize

    # This is a special case serializer as it may have to asynchronously read "file" content.
    async def serialize_message(self, message: messages.Message, /) -> _ValueT:
        return self.dumps(await self._get_message_serializer()(message))

    def _get_own_user_deserializer(self) -> typing.Callable[..., users.OwnUser]:
        try:
            return self._deserializers[users.OwnUser]
        except KeyError:
            pass

        deserialize = _generate_map_deserializer(
            users.OwnUser,
            *_user_deserialize_rules(),
            "is_mfa_enabled",
            "locale",
            "is_verified",
            "email",
            ("premium_type", _optional_cast(users.PremiumType)),
        )
        self._deserializers[users.OwnUser] = deserialize
        return deserialize

    def deserialize_own_user(self, value: _ValueT, /) -> users.OwnUser:
        return self._get_own_user_deserializer()(self.loads(value), app=self._app)

    def _get_own_user_serializer(self) -> typing.Callable[[users.OwnUser], typing.Mapping[str, typing.Any]]:
        try:
            return self._serializers[users.OwnUser]
        except KeyError:
            pass

        serialize = _generate_map_serializer(
            *_user_serialize_rules(), "is_mfa_enabled", "locale", "is_verified", "email", "premium_type"
        )
        self._serializers[users.OwnUser] = serialize
        return serialize

    def serialize_own_user(self, me: users.OwnUser, /) -> _ValueT:
        return self.dumps(self._get_own_user_serializer()(me))

    def _get_presence_deserializer(
        self,
    ) -> typing.Callable[..., presences.MemberPresence]:
        try:
            return self._deserializers[presences.MemberPresence]
        except KeyError:
            pass

        deserialize_timestamps = _generate_map_deserializer(
            presences.ActivityTimestamps,
            ("start", _optional_cast(_deserialize_datetime)),
            ("end", _optional_cast(_deserialize_datetime)),
        )
        deserialize_client_status = _generate_map_deserializer(
            presences.ClientStatus,
            ("desktop", presences.Status),
            ("mobile", presences.Status),
            ("web", presences.Status),
        )
        deserialize_party = _generate_map_deserializer(presences.ActivityParty, "id", "current_size", "max_size")
        deserialize_assets = _generate_map_deserializer(
            presences.ActivityAssets, "large_image", "large_text", "small_image", "small_text"
        )
        deserialize_secrets = _generate_map_deserializer(presences.ActivitySecret, "join", "spectate", "match")
        deserialize_activity = _generate_map_deserializer(
            presences.RichActivity,
            "name",
            "url",
            ("type", presences.ActivityType),
            ("created_at", _deserialize_datetime),
            ("timestamps", _optional_cast(deserialize_timestamps)),
            ("application_id", _optional_cast(snowflakes.Snowflake)),
            "details",
            "state",
            ("emoji", _optional_cast(self.deserialize_unknown_emoji)),
            ("party", _optional_cast(deserialize_party)),
            ("assets", _optional_cast(deserialize_assets)),
            ("secrets", _optional_cast(deserialize_secrets)),
            "is_instance",
            ("flags", _optional_cast(presences.ActivityFlag)),
        )
        deserialize = _generate_map_deserializer(
            presences.MemberPresence,
            ("app", _SET_KWARG),
            ("user_id", snowflakes.Snowflake),
            ("guild_id", snowflakes.Snowflake),
            ("visible_status", presences.Status),
            ("activities", _cast_sequence(deserialize_activity)),
            ("client_status", deserialize_client_status),
        )
        self._deserializers[presences.MemberPresence] = deserialize
        return deserialize

    def deserialize_presence(self, value: _ValueT, /) -> presences.MemberPresence:
        return self._get_presence_deserializer()(self.loads(value), app=self._app)

    def _get_presence_serializer(self) -> typing.Callable[[presences.MemberPresence], typing.Mapping[str, typing.Any]]:
        try:
            return self._serializers[presences.MemberPresence]
        except KeyError:
            pass

        serialize_assets = _generate_map_serializer("large_image", "large_text", "small_image", "small_text")
        serialize_timestamps = _generate_map_serializer(
            ("start", _optional_cast(_serialize_datetime)),
            ("end", _optional_cast(_serialize_datetime)),
        )
        serialize_activity = _generate_map_serializer(
            "name",
            "url",
            "type",
            ("created_at", _serialize_datetime),
            ("timestamps", _optional_cast(serialize_timestamps)),
            "application_id",
            "details",
            "state",
            ("emoji", _optional_cast(self.serialize_unknown_emoji)),
            ("party", _optional_cast(_generate_map_serializer("id", "current_size", "max_size"))),
            ("assets", _optional_cast(serialize_assets)),
            ("secrets", _optional_cast(_generate_map_serializer("join", "spectate", "match"))),
            "is_instance",
            "flags",
        )
        serialize_client_status = _generate_map_serializer("desktop", "mobile", "web")
        serialize = _generate_map_serializer(
            "user_id",
            "guild_id",
            "visible_status",
            ("activities", _cast_sequence(serialize_activity)),
            ("client_status", serialize_client_status),
        )
        self._serializers[presences.MemberPresence] = serialize
        return serialize

    def serialize_presence(self, presence: presences.MemberPresence, /) -> _ValueT:
        return self.dumps(self._get_presence_serializer()(presence))

    def _get_role_deserializer(self) -> typing.Callable[..., guilds.Role]:
        try:
            return self._deserializers[guilds.Role]
        except KeyError:
            pass

        deserialize = _generate_map_deserializer(
            guilds.Role,
            ("app", _SET_KWARG),
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
        self._deserializers[guilds.Role] = deserialize
        return deserialize

    def deserialize_role(self, value: _ValueT, /) -> guilds.Role:
        return self._get_role_deserializer()(self.loads(value), app=self._app)

    def _get_role_serializer(self) -> typing.Callable[[guilds.Role], typing.Mapping[str, typing.Any]]:
        try:
            return self._serializers[guilds.Role]
        except KeyError:
            pass

        serialize = _generate_map_serializer(
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
        self._serializers[guilds.Role] = serialize
        return serialize

    def serialize_role(self, role: guilds.Role, /) -> _ValueT:
        return self.dumps(self._get_role_serializer()(role))

    @staticmethod
    def deserialize_unknown_emoji(data: typing.Mapping[str, typing.Any], /) -> emojis.Emoji:
        if "id" in data:
            return emojis.CustomEmoji(
                id=snowflakes.Snowflake(data["id"]), name=data["name"], is_animated=data["is_animated"]
            )

        return emojis.UnicodeEmoji(data["name"])

    @staticmethod
    def serialize_unknown_emoji(emoji: emojis.Emoji, /) -> typing.MutableMapping[str, typing.Any]:
        if isinstance(emoji, emojis.CustomEmoji):
            return {"id": emoji.id, "name": emoji.name, "is_animated": emoji.is_animated}

        return {"name": emoji.name}

    def _get_user_deserializer(self) -> typing.Callable[..., users.User]:
        try:
            return self._deserializers[users.UserImpl]
        except KeyError:
            pass

        deserialize = _generate_map_deserializer(users.UserImpl, *_user_deserialize_rules())
        self._deserializers[users.UserImpl] = deserialize
        return deserialize

    def deserialize_user(self, value: _ValueT, /) -> users.User:
        return self._get_user_deserializer()(self.loads(value), app=self._app)

    def _get_user_serializer(self) -> typing.Callable[[users.User], typing.Mapping[str, typing.Any]]:
        try:
            return self._serializers[users.UserImpl]
        except KeyError:
            pass

        serialize = _generate_map_serializer(*_user_serialize_rules())
        self._serializers[users.UserImpl] = serialize
        return serialize

    def serialize_user(self, user: users.User, /) -> _ValueT:
        return self.dumps(self._get_user_serializer()(user))

    def _get_voice_state_deserializer(self) -> typing.Callable[..., voices.VoiceState]:
        try:
            return self._deserializers[voices.VoiceState]
        except KeyError:
            pass

        deserialize = _generate_map_deserializer(
            voices.VoiceState,
            ("app", _SET_KWARG),
            ("channel_id", _optional_cast(snowflakes.Snowflake)),
            ("guild_id", snowflakes.Snowflake),
            "is_guild_deafened",
            "is_guild_muted",
            "is_self_deafened",
            "is_self_muted",
            "is_streaming",
            "is_suppressed",
            "is_video_enabled",
            ("user_id", snowflakes.Snowflake),
            ("member", self._get_member_deserializer(), {_PASS_KWARGS}),
            "session_id",
        )
        self._deserializers[voices.VoiceState] = deserialize
        return deserialize

    def deserialize_voice_state(self, value: _ValueT, /) -> voices.VoiceState:
        return self._get_voice_state_deserializer()(self.loads(value), app=self._app)

    def _get_voice_state_serializer(self) -> typing.Callable[[voices.VoiceState], typing.Mapping[str, typing.Any]]:
        try:
            return self._serializers[voices.VoiceState]
        except KeyError:
            pass

        serialize = _generate_map_serializer(
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
        self._serializers[voices.VoiceState] = serialize
        return serialize

    def serialize_voice_state(self, voice_state: voices.VoiceState, /) -> _ValueT:
        return self.dumps(self._get_voice_state_serializer()(voice_state))


class JSONMarshaller(MappingMarshaller[bytes]):
    __slots__: typing.Sequence[str] = ("_decoder", "_encoder")

    def __init__(self, app: traits.RESTAware) -> None:
        super().__init__(app)
        self._decoder = json.JSONDecoder()
        self._encoder = json.JSONEncoder()

    def dumps(self, data: typing.Mapping[str, typing.Any], /) -> bytes:
        return self._encoder.encode(data).encode()

    def loads(self, data: bytes, /) -> typing.MutableMapping[str, typing.Any]:
        result = self._decoder.decode(data.decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError("Invalid json content received")

        return result
