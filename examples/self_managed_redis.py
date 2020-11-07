import os
import typing

import hikari

import sake

bot = hikari.BotApp(token=os.environ["BOT_TOKEN"])


integration_cache = sake.redis.IntegrationCache(
    bot,
    # The Hikari RESTAware client to be used when marshalling objects and making internal requests.
    None,
    # The dispatcher is left as None to allow us to manage event listening ourselves.
    # As a note this can just be left to default to None and is only explicitly provided for readability.
    address=os.environ["REDIS_ADDRESS"],
    password=os.environ["REDIS_PASSWORD"],
    ssl=True,
)


async def get_guilds_bot_channel(guild_id: hikari.Snowflake) -> typing.Optional[hikari.Snowflake]:
    """Lookup a guild's set channel for the bot's announcement in a database."""
    raise NotImplementedError


# We handle integration events ourselves here to ensure that we can fetch
# the integration from the cache before it's deleted by an IntegrationDelete
# listener.
@bot.listen()
async def on_integration_create(event: hikari.IntegrationCreateEvent) -> None:
    channel_id = await get_guilds_bot_channel(event.guild_id)
    integration = event.integration

    if channel_id is not None:
        await bot.rest.create_message(
            channel_id, content=f"{integration!r} {str(integration.type).lower()} integration created"
        )

    await integration_cache.set_integration(integration)


@bot.listen()
async def on_integration_delete(event: hikari.IntegrationDeleteEvent) -> None:
    try:
        integration = await integration_cache.get_integration(event.id)

    except sake.errors.EntryNotFound:
        # If we don't know the integration then we can't default back to REST
        # as there is no public endpoint for fetching an application by it's ID
        # and the application ID isn't guaranteed to be the same as the bot's
        # user ID.
        return

    channel_id = await get_guilds_bot_channel(event.guild_id)

    if channel_id is not None:
        await bot.rest.create_message(
            channel_id, content=f"{integration!r} {str(integration.type).lower()} integration deleted"
        )

    await integration_cache.delete_integration(event.id)


@bot.listen()
async def on_integration_update(event: hikari.IntegrationUpdateEvent) -> None:
    await integration_cache.set_integration(event.integration)


bot.run()
