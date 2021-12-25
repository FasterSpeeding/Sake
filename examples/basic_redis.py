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
"""Examples of basic Redis usage."""
import os

import hikari

import sake

bot = hikari.GatewayBot(token=os.environ["BOT_TOKEN"])
# Initiate a self-managing cache with all supplied resources.
cache = sake.redis.RedisCache(
    app=bot,
    # The Hikari RESTAware client to be used when marshalling objects and making internal requests.
    event_manager=bot.event_manager,
    # The second positional argument may either be a Hikari DispatcherAware client or None.
    # When DispatcherAware is passed here the client will register it's own event listeners when started.
    address=os.environ["REDIS_ADDRESS"],
    password=os.environ["REDIS_PASSWORD"],
)
prefix = os.environ["BOT_PREFIX"]


@bot.listen()
async def on_message(event: hikari.MessageCreateEvent) -> None:
    if not event.message.content or not event.message.content.startswith(prefix) or not event.is_human:
        return

    arguments = event.message.content[len(prefix)].split()

    if arguments[0] == "member":
        if not event.message.guild_id:
            await event.message.respond("Cannot use this command in a DM")
            return

        try:
            member = await cache.get_member(event.message.guild_id, int(arguments[1]))

        except sake.errors.EntryNotFound:
            await event.message.respond(content="Member not found.")

        except ValueError:
            await event.message.respond(content="Invalid ID passed.")

        except IndexError:
            await event.message.respond(content="Missing ID.")

        else:
            embed = (
                hikari.Embed(title=f"Member: {member}")
                .set_thumbnail(member.avatar_url)
                .add_field(name="Joined server", value=member.joined_at.strftime("%d/%m/%y %H:%M %|"))
                .add_field(name="Roles", value=",".join(map(str, member.role_ids)))
                .add_field(name="Is bot", value=str(member.is_bot).lower())
            )
            await event.message.respond(embed=embed)


bot.run()
