import os
import typing

import hikari

import sake

bot = hikari.BotApp(token=os.environ["BOT_TOKEN"])
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
    bot,
    # The Hikari RESTAware client to be used when marshalling objects and making internal requests.
    bot,
    # The second positional argument may either be a Hikari DispatcherAware client or None.
    # When DispatcherAware is passed here the client will register it's own event listeners when started.
    address=os.environ["REDIS_ADDRESS"],
    password=os.environ["REDIS_PASSWORD"],
    ssl=True,
    # Whether ssl should be used when connecting to the redis database.
)


@bot.listen()
async def on_message_create(event: hikari.MessageCreateEvent) -> None:
    if not event.is_human or not event.message.content.startswith(prefix):
        return

    arguments = event.message.content[len(prefix) :]

    if arguments[0] == "user":
        try:
            user = await cache.get_user(int(arguments[1]))

        except sake.EntryNotFound:
            await event.message.reply(content="User not found.")

        except ValueError:
            await event.message.reply(content="Invalid user ID passed.")

        except IndexError:
            await event.message.reply(content="Missing user ID.")

        else:
            embed = (
                hikari.Embed(title=str(user))
                .add_field(name="Is bot", value=str(user.is_bot).lower())
                .add_field(name="Joined Discord", value=user.created_at.strftime("%d/%m/%y %H:%M %|"))
                .set_thumbnail(user.avatar_url)
            )
            await event.message.reply(embed=embed)


bot.run()
