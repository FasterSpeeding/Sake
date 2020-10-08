from __future__ import annotations

__all__: typing.Final[typing.Sequence[str]] = [
    "RedisValueT",
    "RedisMapT",
    "deserialize_emoji",
    "serialize_emoji",
    "deserialize_guild",
    "serialize_guild",
    "deserialize_guild_channel",
    "serialize_guild_channel",
    "deserialize_invite",
    "serialize_invite",
    "deserialize_me",
    "serialize_me",
    "deserialize_member",
    "serialize_member",
    "deserialize_presence",
    "serialize_presence",
    "deserialize_role",
    "serialize_role",
    "deserialize_user",
    "serialize_user",
    "deserialize_voice_state",
    "serialize_voice_state",
]

import datetime
import pickle
import typing

from hikari import channels
from hikari import colors
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
    from hikari import applications
    from hikari import traits


#  TODO: can we use a type var here for invariance?
RedisValueT = typing.Union[bytearray, bytes, float, int, str]
"""A type variable of the value types accepted by aioredis."""

RedisMapT = typing.MutableMapping[str, RedisValueT]
"""A type variable of the mapping type accepted by aioredis"""

ValueT = typing.TypeVar("ValueT")


def _deserialize_array(array: typing.Optional[str], cast: typing.Callable[[str], ValueT]) -> typing.Sequence[ValueT]:
    if array:
        return (*map(cast, array.split(",")),)

    return ()


def _serialize_array(array: typing.Sequence[typing.Any]) -> typing.Optional[str]:
    return ",".join(map(str, array)) or None


def deserialize_emoji(
    data: typing.Mapping[str, str], *, app: traits.RESTAware, user: typing.Optional[users.User]
) -> emojis.KnownCustomEmoji:
    return emojis.KnownCustomEmoji(
        app=app,
        id=snowflakes.Snowflake(data["id"]),
        name=data.get("name"),
        guild_id=snowflakes.Snowflake(data["guild_id"]),
        role_ids=_deserialize_array(data.get("role_ids"), snowflakes.Snowflake),
        user=user,
        is_animated=bool(data["is_animated"]),
        is_colons_required=bool(data["is_colons_required"]),
        is_managed=bool(data["is_managed"]),
        is_available=bool(data["is_available"]),
    )


def serialize_emoji(emoji: emojis.KnownCustomEmoji) -> RedisMapT:
    data: RedisMapT = {
        "id": int(emoji.id),
        "guild_id": int(emoji.guild_id),
        "is_animated": int(emoji.is_animated),
        "is_colons_required": int(emoji.is_colons_required),
        "is_managed": int(emoji.is_managed),
        "is_available": int(emoji.is_available),
    }

    if role_ids := _serialize_array(emoji.role_ids):
        data["role_ids"] = role_ids

    if emoji.name is not None:
        data["name"] = emoji.name

    if emoji.user is not None:
        data["user_id"] = int(emoji.user.id)

    return data


def deserialize_guild(data: typing.Mapping[str, str], *, app: traits.RESTAware) -> guilds.GatewayGuild:
    premium_subscription_count: typing.Optional[int] = None
    if "premium_subscription_count" in data:
        premium_subscription_count = int(data["premium_subscription_count"])

    public_updates_channel_id: typing.Optional[snowflakes.Snowflake] = None
    if "public_updates_channel_id" in data:
        public_updates_channel_id = snowflakes.Snowflake(data["public_updates_channel_id"])

    return guilds.GatewayGuild(
        app=app,
        features=_deserialize_array(data.get("features"), guilds.GuildFeature),
        id=snowflakes.Snowflake(data["id"]),
        name=data["name"],
        owner_id=snowflakes.Snowflake(data["owner_id"]),
        region=data["region"],
        afk_timeout=datetime.timedelta(seconds=int(data["afk_timeout"])),
        explicit_content_filter=guilds.GuildExplicitContentFilterLevel(int(data["explicit_content_filter"])),
        mfa_level=guilds.GuildMFALevel(int(data["mfa_level"])),
        application_id=snowflakes.Snowflake(data["application_id"]) if "application_id" in data else None,
        premium_tier=guilds.GuildPremiumTier(int(data["premium_tier"])),
        preferred_locale=data["preferred_locale"],
        verification_level=guilds.GuildVerificationLevel(int(data["verification_level"])),
        system_channel_flags=guilds.GuildSystemChannelFlag(int(data["system_channel_flags"])),
        icon_hash=data.get("icon_hash"),
        splash_hash=data.get("splash_hash"),
        discovery_splash_hash=data.get("discovery_splash_hash"),
        afk_channel_id=snowflakes.Snowflake(data["afk_channel_id"]) if "afk_channel_id" in data else None,
        default_message_notifications=guilds.GuildMessageNotificationsLevel(int(data["default_message_notifications"])),
        is_widget_enabled=bool(data["is_widget_enabled"]) if "is_widget_enabled" in data else None,
        widget_channel_id=snowflakes.Snowflake(data["widget_channel_id"]) if "widget_channel_id" in data else None,
        system_channel_id=snowflakes.Snowflake(data["system_channel_id"]) if "system_channel_id" in data else None,
        rules_channel_id=snowflakes.Snowflake(data["rules_channel_id"]) if "rules_channel_id" in data else None,
        max_video_channel_users=int(data["max_video_channel_users"]) if "max_video_channel_users" in data else None,
        vanity_url_code=data.get("vanity_url_code"),
        description=data.get("description"),
        banner_hash=data.get("banner_hash"),
        premium_subscription_count=premium_subscription_count,
        public_updates_channel_id=public_updates_channel_id,
        joined_at=time.iso8601_datetime_string_to_datetime(data["joined_at"]) if "joined_at" in data else None,
        is_large=bool(data["is_large"]) if "is_large" in data else None,
        member_count=int(data["member_count"]) if "member_count" in data else None,
    )


def serialize_guild(guild: guilds.GatewayGuild) -> RedisMapT:
    data: RedisMapT = {
        "id": int(guild.id),
        "name": guild.name,
        "owner_id": int(guild.owner_id),
        "region": guild.region,
        "afk_timeout": guild.afk_timeout.total_seconds(),
        "explicit_content_filter": int(guild.explicit_content_filter),
        "mfa_level": int(guild.mfa_level),
        "premium_tier": int(guild.premium_tier),
        "preferred_locale": guild.preferred_locale,
        "verification_level": int(guild.verification_level),
        "system_channel_flags": int(guild.system_channel_flags),
    }

    if features := _serialize_array(guild.features):
        data["features"] = features

    if guild.icon_hash is not None:
        data["icon_hash"] = guild.icon_hash

    if guild.splash_hash is not None:
        data["splash_hash"] = guild.splash_hash

    if guild.discovery_splash_hash is not None:
        data["discovery_splash_hash"] = guild.discovery_splash_hash

    if guild.application_id is not None:
        data["application_id"] = int(guild.application_id)

    if guild.afk_channel_id is not None:
        data["afk_channel_id"] = int(guild.afk_channel_id)

    if guild.default_message_notifications is not None:
        data["default_message_notifications"] = int(guild.default_message_notifications)

    if guild.is_widget_enabled is not None:
        data["is_widget_enabled"] = int(guild.is_widget_enabled)

    if guild.widget_channel_id is not None:
        data["widget_channel_id"] = int(guild.widget_channel_id)

    if guild.system_channel_id is not None:
        data["system_channel_id"] = int(guild.system_channel_id)

    if guild.rules_channel_id is not None:
        data["rules_channel_id"] = int(guild.rules_channel_id)

    if guild.max_video_channel_users is not None:
        data["max_video_channel_users"] = guild.max_video_channel_users

    if guild.vanity_url_code is not None:
        data["vanity_url_code"] = guild.vanity_url_code

    if guild.description is not None:
        data["description"] = guild.description

    if guild.banner_hash is not None:
        data["banner_hash"] = guild.banner_hash

    if guild.premium_subscription_count is not None:
        data["premium_subscription_count"] = guild.premium_subscription_count

    if guild.public_updates_channel_id is not None:
        data["public_updates_channel_id"] = int(guild.public_updates_channel_id)

    if guild.joined_at is not None:
        data["joined_at"] = guild.joined_at.isoformat()

    if guild.is_large is not None:
        data["is_large"] = int(guild.is_large)

    if guild.member_count is not None:
        data["member_count"] = guild.member_count

    return data


def deserialize_guild_channel(data: typing.Mapping[str, str], *, app: traits.RESTAware) -> channels.GuildChannel:
    raise NotImplementedError


def serialize_guild_channel(channel: channels.GuildChannel) -> RedisMapT:
    raise NotImplementedError


def deserialize_invite(data: typing.Mapping[str, str], *, app: traits.RESTAware) -> invites.InviteWithMetadata:
    raise NotImplementedError


def serialize_invite(invite: invites.InviteWithMetadata) -> RedisMapT:
    raise NotImplementedError


def deserialize_me(data: typing.Mapping[str, str], *, app: traits.RESTAware) -> users.OwnUser:
    # TODO: can we not duplicate this logic between here, deserialize_user and deserialize_member
    return users.OwnUser(
        app=app,
        id=snowflakes.Snowflake(data["id"]),
        discriminator=data["discriminator"],
        username=data["username"],
        avatar_hash=data.get("avatar_hash"),
        is_bot=bool(data["is_bot"]),
        is_system=bool(data["is_system"]),
        flags=users.UserFlag(int(data["flags"])),
        is_mfa_enabled=bool(data["is_mfa_enabled"]),
        locale=data.get("locale"),
        is_verified=bool(data["is_verified"]) if "is_verified" in data else None,
        email=data.get("email"),
        premium_type=users.PremiumType(data["premium_type"]) if "premium_type" in data else None,
    )


def serialize_me(me: users.OwnUser) -> RedisMapT:
    data = serialize_user(me)
    data.update(is_mfa_enabled=int(me.is_mfa_enabled))

    if me.locale is not None:
        data["locale"] = me.locale

    if me.is_verified is not None:
        data["is_verified"] = int(me.is_verified)

    if me.email is not None:
        data["email"] = me.email

    if me.premium_type is not None:
        data["premium_type"] = int(me.premium_type)

    return data


def deserialize_member(data: typing.Mapping[str, str], *, user: users.User) -> guilds.Member:
    premium_since: typing.Optional[datetime.datetime] = None
    if "premium_since" in data:
        premium_since = time.iso8601_datetime_string_to_datetime(data["premium_since"])

    return guilds.Member(
        user=user,
        guild_id=snowflakes.Snowflake(data["guild_id"]),
        nickname=data.get("nickname"),
        role_ids=_deserialize_array(data.get("role_ids"), snowflakes.Snowflake),
        joined_at=time.iso8601_datetime_string_to_datetime(data["joined_at"]),
        premium_since=premium_since,
        is_deaf=bool(data["is_deaf"]) if "is_deaf" in data else undefined.UNDEFINED,
        is_mute=bool(data["is_mute"]) if "is_mute" in data else undefined.UNDEFINED,
    )


def serialize_member(member: guilds.Member) -> RedisMapT:
    data: RedisMapT = {
        "guild_id": int(member.guild_id),
        "user_id": int(member.user.id),
        "joined_at": member.joined_at.isoformat(),
    }

    if member.nickname is not None and member.nickname is not undefined.UNDEFINED:
        data["nickname"] = member.nickname

    if role_ids := _serialize_array(member.role_ids):
        data["role_ids"] = role_ids

    if member.premium_since is not None:
        data["premium_since"] = member.premium_since.isoformat()

    if member.is_deaf is not undefined.UNDEFINED:
        data["is_deaf"] = int(member.is_deaf)

    if member.is_mute is not undefined.UNDEFINED:
        data["is_mute"] = int(member.is_mute)

    return data


def deserialize_message(
    data: typing.MutableMapping[str, str], *, app: traits.RESTAware, user: users.User, member: typing.Optional[guilds.Member]
) -> messages.Message:
    edited_timestamp: typing.Optional[datetime.datetime] = None
    if "edited_timestamp" in data:
        edited_timestamp = time.iso8601_datetime_string_to_datetime(data["edited_timestamp"])

    application: typing.Optional[applications.Application] = None
    if "application" in data:
        application = pickle.loads(data["application"])
        application.app = app

        if application.owner is not None:
            application.owner.app = app

        if application.team is not None:
            application.team.app = app

            for member in application.team.members.values():
                member.app = app

    message_reference: typing.Optional[messages.MessageCrosspost] = None
    if "message_reference" in data:
        message_reference = pickle.loads(data["message_reference"])
        message_reference.app = app

    return messages.Message(
        app=app,
        id=snowflakes.Snowflake(data["id"]),
        channel_id=snowflakes.Snowflake(data["channel_id"]),
        author=user,
        member=member,
        guild_id=snowflakes.Snowflake(data["guild_id"]) if "guild_id" in data else None,
        content=data.get("content"),
        timestamp=time.iso8601_datetime_string_to_datetime(data["timestamp"]),
        edited_timestamp=edited_timestamp,
        is_tts=bool(data["is_tts"]),
        is_mentioning_everyone=bool(data["is_mentioning_everyone"]),
        user_mentions=_deserialize_array(data.get("user_mentions"), cast=snowflakes.Snowflake),
        role_mentions=_deserialize_array(data.get("role_mentions"), cast=snowflakes.Snowflake),
        channel_mentions=_deserialize_array(data.get("channel_mentions"), cast=snowflakes.Snowflake),
        attachments=pickle.loads(data["attachments"]) if "attachments" in data else (),
        embeds=pickle.loads(data["embeds"]) if "embeds" in data else (),
        reactions=pickle.loads(data["reactions"]) if "reactions" in data else (),
        is_pinned=bool(data["is_pinned"]),
        webhook_id=snowflakes.Snowflake(data["webhook_id"]) if "webhook_id" in data else None,
        type=messages.MessageType(int(data["type"])),
        activity=pickle.loads(data["activity"]) if "activity" in data else None,
        application=application,
        message_reference=message_reference,
        flags=messages.MessageFlag(int(data["flags"])) if "flags" in data else None,
        nonce=data.get("nonce"),
    )


def serialize_message(message: messages.Message) -> RedisMapT:
    data: RedisMapT = {
        "id": int(message.id),
        "channel_id": int(message.channel_id),
        "author_id": int(message.author.id),
        "timestamp": message.timestamp.isoformat(),
        "is_tts": int(message.is_tts),
        "is_mentioning_everyone": int(message.is_mentioning_everyone),
        "is_pinned": int(message.is_pinned),
        "type": int(message.type),
    }

    if message.guild_id is not None:
        data["guild_id"] = int(message.guild_id)

    if message.content is not None:
        data["content"] = message.content

    if message.edited_timestamp is not None:
        data["edited_timestamp"] = message.edited_timestamp.isoformat()

    if user_mentions := _serialize_array(message.user_mentions):
        data["user_mentions"] = user_mentions

    if role_mentions := _serialize_array(message.role_mentions):
        data["role_mentions"] = role_mentions

    if channel_mentions := _serialize_array(message.channel_mentions):
        data["channel_mentions"] = channel_mentions

    if message.attachments:
        data["attachments"] = pickle.dumps(message.attachments)

    if message.embeds:
        data["embeds"] = pickle.dumps(message.embeds)

    if message.reactions:
        data["reactions"] = pickle.dumps(message.reactions)

    if message.webhook_id is not None:
        data["webhook_id"] = int(message.webhook_id)

    if message.activity is not None:
        data["activity"] = pickle.dumps(message.activity)

    if message.application is not None:
        data["application"] = pickle.dumps(message.application)

    if message.message_reference is not None:
        data["message_reference"] = pickle.dumps(message.message_reference)

    if message.flags is not None:
        data["flags"] = int(message.flags)

    if message.nonce is not None:
        data["nonce"] = message.nonce

    return data


def deserialize_presence(data: typing.Mapping[str, str], *, app: traits.RESTAware) -> presences.MemberPresence:
    raise NotImplementedError


def serialize_presence(presence: presences.MemberPresence) -> RedisMapT:
    raise NotImplementedError


def deserialize_role(data: typing.Mapping[str, str], *, app: traits.RESTAware) -> guilds.Role:
    return guilds.Role(
        app=app,
        id=snowflakes.Snowflake(data["id"]),
        name=data["name"],
        color=colors.Color(int(data["color"])),
        guild_id=snowflakes.Snowflake(data["guild_id"]),
        is_hoisted=bool(data["is_hoisted"]),
        position=int(data["position"]),
        permissions=permissions.Permissions(int(data["permissions"])),
        is_managed=bool(data["is_managed"]),
        is_mentionable=bool(data["is_mentionable"]),
    )


def serialize_role(role: guilds.Role) -> RedisMapT:
    return {
        "id": int(role.id),
        "name": role.name,
        "color": int(role.color),
        "guild_id": int(role.guild_id),
        "is_hoisted": int(role.is_hoisted),
        "position": role.position,
        "permissions": int(role.permissions),
        "is_managed": int(role.is_managed),
        "is_mentionable": int(role.is_mentionable),
    }


def deserialize_user(data: typing.Mapping[str, str], *, app: traits.RESTAware) -> users.User:
    return users.UserImpl(
        app=app,
        id=snowflakes.Snowflake(data["id"]),
        discriminator=data["discriminator"],
        username=data["username"],
        avatar_hash=data.get("avatar_hash"),
        is_bot=bool(data["is_bot"]),
        is_system=bool(data["is_system"]),
        flags=users.UserFlag(int(data["flags"])),
    )


def serialize_user(user: users.User) -> RedisMapT:
    data: RedisMapT = {
        "id": int(user.id),
        "discriminator": user.discriminator,
        "username": user.username,
        "is_bot": int(user.is_bot),
        "is_system": int(user.is_system),
        "flags": int(user.flags),
    }
    if user.avatar_hash is not None:
        data["avatar_hash"] = user.avatar_hash

    return data


def deserialize_voice_state(
    data: typing.Mapping[str, str], *, app: traits.RESTAware, member: guilds.Member
) -> voices.VoiceState:
    return voices.VoiceState(
        app=app,
        channel_id=snowflakes.Snowflake(data["channel_id"]) if "channel_id" in data else None,
        guild_id=snowflakes.Snowflake(data["guild_id"]),
        is_guild_deafened=bool(data["is_guild_deafened"]),
        is_guild_muted=bool(data["is_guild_muted"]),
        is_self_deafened=bool(data["is_self_deafened"]),
        is_self_muted=bool(data["is_self_muted"]),
        is_streaming=bool(data["is_streaming"]),
        is_suppressed=bool(data["is_suppressed"]),
        is_video_enabled=bool(data["is_video_enabled"]),
        user_id=snowflakes.Snowflake(data["user_id"]),
        member=member,
        session_id=data["session_id"],
    )


def serialize_voice_state(voice_state: voices.VoiceState) -> RedisMapT:
    data: RedisMapT = {
        "guild_id": int(voice_state.guild_id),
        "is_guild_deafened": int(voice_state.is_guild_deafened),
        "is_guild_muted": int(voice_state.is_guild_muted),
        "is_self_deafened": int(voice_state.is_self_deafened),
        "is_self_muted": int(voice_state.is_self_muted),
        "is_streaming": int(voice_state.is_streaming),
        "is_suppressed": int(voice_state.is_suppressed),
        "is_video_enabled": int(voice_state.is_video_enabled),
        "user_id": int(voice_state.user_id),
        # member,
        "session_id": voice_state.session_id,
    }

    if voice_state.channel_id is not None:
        data["channel_id"] = int(voice_state.channel_id)

    return data
