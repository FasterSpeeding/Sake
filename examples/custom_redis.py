# -*- coding: utf-8 -*-
# cython: language_level=3
# Tanjun Examples - A collection of examples for Tanjun.
# Written in 2021 by Lucina Lucina@lmbyrne.dev
#
# To the extent possible under law, the author(s) have dedicated all copyright
# and related and neighboring rights to this software to the public domain worldwide.
# This software is distributed without any warranty.
#
# You should have received a copy of the CC0 Public Domain Dedication along with this software.
# If not, see <https://creativecommons.org/publicdomain/zero/1.0/>.
"""Examples of defining a specific set of cache stores to target."""
import os
import typing

import hikari

import sake

bot = hikari.GatewayBot(token=os.environ["BOT_TOKEN"])
prefix = os.environ["BOT_PREFIX"]


# As a note, the user cache is a special case as it doesn't subscribe it's
# own event listeners and will be reliant on other resources which reference
# user objects (e.g. members) to add user entries.
class Cache(sake.redis.MemberCache, sake.redis.UserCache):
    """Here we combine the redis member and user cache implementations.

    This should still be compatible with the Integration and GuildChannel generic
    Protocols.

    !!! warn
        Only cache resources from the same implementation should ever be combined
        like this and combining between implementations may lead to unexpected
        behaviour or just not work.
    """

    __slots__: typing.Sequence[str] = ()


cache = Cache(
    app=bot,
    # The Hikari RESTAware client to be used when marshalling objects and making internal requests.
    event_manager=bot.event_manager,
    # The second positional argument may either be a Hikari DispatcherAware client or None.
    # When DispatcherAware is passed here the client will register it's own event listeners when started.
    address=os.environ["REDIS_ADDRESS"],
    password=os.environ["REDIS_PASSWORD"],
)


@bot.listen()
async def on_message_create(event: hikari.MessageCreateEvent) -> None:
    if not event.is_human or not event.message.content or not event.message.content.startswith(prefix):
        return

    arguments = event.message.content[len(prefix) :]

    if arguments[0] == "user":
        try:
            user = await cache.get_user(int(arguments[1]))

        except sake.EntryNotFound:
            await event.message.respond(content="User not found.")

        except ValueError:
            await event.message.respond(content="Invalid user ID passed.")

        except IndexError:
            await event.message.respond(content="Missing user ID.")

        else:
            embed = (
                hikari.Embed(title=str(user))
                .add_field(name="Is bot", value=str(user.is_bot).lower())
                .add_field(name="Joined Discord", value=user.created_at.strftime("%d/%m/%y %H:%M %|"))
                .set_thumbnail(user.avatar_url)
            )
            await event.message.respond(embed=embed)


bot.run()
