from __future__ import annotations

__all__: typing.Final[typing.Sequence[str]] = ["serialize_user", "deserialize_user"]

import typing

from hikari import emojis
from hikari import snowflakes
from hikari import undefined
from hikari import users

if typing.TYPE_CHECKING:
    from hikari import traits


def serialize_emoji(emoji: emojis.KnownCustomEmoji) -> typing.Mapping[str, typing.Any]:
    data = {
        "id": int(emoji.id),
        "guild_id": int(emoji.guild_id),
        "role_ids": ",".join(map(str, emoji.role_ids)),
        "is_animated": int(emoji.is_animated),
        "is_colons_required": int(emoji.is_colons_required),
        "is_managed": int(emoji.is_managed),
        "is_available": int(emoji.is_available),
    }

    if emoji.name is not None:
        data["name"] = emoji.name

    if emoji.user is not None:
        data["user_id"] = int(emoji.user.id)

    return data


def deserialize_emoji(
    data: typing.Mapping[str, str], *, app: traits.RESTAware, user: typing.Optional[users.User]
) -> emojis.KnownCustomEmoji:
    return emojis.KnownCustomEmoji(
        app=app,
        id=snowflakes.Snowflake(data["id"]),
        name=data.get("name"),
        guild_id=snowflakes.Snowflake(data["guild_id"]),
        role_ids=[snowflakes.Snowflake(role_id) for role_id in data["role_ids"].split(",")],
        user=user,
        is_animated=bool(data["is_animated"]),
        is_colons_required=bool(data["is_colons_required"]),
        is_managed=bool(data["is_managed"]),
        is_available=bool(data["is_available"]),
    )


def serialize_user(user: users.User) -> typing.Mapping[str, typing.Any]:
    data = {
        "id": int(user.id),
        "discriminator": user.discriminator,
        "username": user.username,
        "is_bot": int(user.is_bot),
        "is_system": int(user.is_system),
        "flags": int(user.flags),
    }
    if user.avatar_hash is not undefined.UNDEFINED and user.avatar_hash is not None:
        data["avatar_hash"] = user.avatar_hash

    return data


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
