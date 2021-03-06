__all__ = ["register"]

import typing

import hikari

import sake


def nsfw_check(content: str) -> bool:
    """Check a message's content for NSFW phrases."""
    raise NotImplementedError


# This function registers event listeners to a provided hikari dispatch aware
# client implementation which use multiple sake cache resources without being
# bound to any specific implementations.
def register(
    dispatch: hikari.DispatcherAware,
    *,
    prefix: str = "!",
    channel_cache: sake.traits.GuildChannelCache,
    guild_cache: sake.traits.GuildCache,
    member_cache: sake.traits.RefMemberCache,
    # While we could make a specific Protocol which implements all these resources,
    # for the sake of being more compatible with services which use multiple
    # implementations we will take in each resources as a separate argument.
) -> typing.Callable[..., None]:
    # This listener simply deletes a message if it's updated to NSFW content.
    @dispatch.dispatcher.listen()
    async def on_message_update(event: hikari.MessageUpdateEvent) -> None:
        if event.message.content is hikari.undefined.UNDEFINED:
            return

        if nsfw_check(event.message.content):
            channel = await channel_cache.get_guild_channel(event.message.channel_id)
            if not channel.is_nsfw:
                await event.message.delete()
                await event.message.reply(content=f"Deleted NSFW message by {event.message.author}")

    # This listener handles both a "member count" command which performs a database lookup and
    # the deletion of messages created with nsfw content.
    @dispatch.dispatcher.listen()
    async def on_message_create(event: hikari.MessageCreateEvent) -> None:
        # Delete nsfw content
        if nsfw_check(event.message.content):
            channel = await channel_cache.get_guild_channel(event.message.channel_id)
            if not channel.is_nsfw:
                await event.message.delete()

        # Handle the "member count" command
        if not event.message.content.startswith(prefix) or not event.is_human:
            return

        arguments = event.message.content[len(prefix) :].split()

        if arguments[0] == "member" and arguments[1] == "count":
            guild = await guild_cache.get_guild(event.message.guild_id)
            count = await member_cache.iter_members_for_guild(event.message.guild_id).len()
            await event.message.reply(content=f"{count} members known for guild {guild}.")
            return

    def unsubscribe() -> None:
        """Unsubscribe the listeners registered by this function."""
        dispatch.dispatcher.unsubscribe(hikari.MessageUpdateEvent, on_message_update)
        dispatch.dispatcher.unsubscribe(hikari.MessageCreateEvent, on_message_create)

    # Return a callable which can be used to unsubscribe the listeners this
    # function registered, essentially turning it off.
    return unsubscribe
