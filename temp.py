class PrefixCache(ResourceClient, traits.PrefixCache):
    __slots__: typing.Sequence[str] = ()

    @classmethod
    def index(cls) -> typing.Sequence[ResourceIndex]:
        # <<Inherited docstring from ResourceClient>>
        return (ResourceIndex.PREFIX,)

    def subscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().subscribe_listeners()

    def unsubscribe_listeners(self) -> None:
        # <<Inherited docstring from sake.traits.Resource>>
        super().unsubscribe_listeners()

    async def clear_prefixes(self) -> None:
        # <<Inherited docstring from sake.traits.PrefixCache>>
        client = await self.get_connection(ResourceIndex.PREFIX)
        await client.flushdb()

    async def clear_prefixes_for_guild(self, guild_id: snowflakes.Snowflakeish, /) -> None:
        # <<Inherited docstring from sake.traits.PrefixCache>>
        client = await self.get_connection(ResourceIndex.PREFIX)
        await client.delete(int(guild_id))

    async def delete_prefix(self, guild_id: snowflakes.Snowflakeish, prefix: str) -> None:
        client = await self.get_connection(ResourceIndex.PREFIX)
        await client.srem(int(guild_id), prefix)

    async def get_prefixes(self, guild_id: snowflakes.Snowflakeish, /) -> typing.List[str]:
        # <<Inherited docstring from sake.traits.PrefixCache>>
        guild_id = int(guild_id)
        client = await self.get_connection(ResourceIndex.PREFIX)
        data = await client.smembers(int(guild_id))
        if not data:
            raise errors.EntryNotFound(f"Prefix entry `{guild_id}` not found")

        return [prefix.decode() for prefix in data]

    def iter_prefixes(self, *, window_size: int = WINDOW_SIZE) -> traits.CacheIterator[typing.List[str]]:
        # <<Inherited docstring from sake.traits.PrefixCache>>
        return redis_iterators.Iterator(
            self, ResourceIndex.PREFIX, self.marshaller.deserialize_prefixes, window_size=window_size
        )

    async def add_prefix(self, guild_id: snowflakes.Snowflakeish, prefix: str, /) -> None:
        # <<Inherited docstring from sake.traits.PrefixCache>>
        client = await self.get_connection(ResourceIndex.PREFIX)
        await client.sadd(int(guild_id), prefix)

    async def add_prefixes(self, guild_id: snowflakes.Snowflakeish, prefixes: typing.Iterable[str], /) -> None:
        # <<Inherited docstring from sake.traits.PrefixCache>>
        client = await self.get_connection(ResourceIndex.PREFIX)
        await client.sadd(int(guild_id), *prefixes)

    async def set_prefixes(self, guild_id: snowflakes.Snowflakeish, prefixes: typing.Iterable[str], /) -> None:
        # <<Inherited docstring from sake.traits.PrefixCache>>
        await self.clear_prefixes_for_guild(guild_id)
        await self.add_prefixes(guild_id, prefixes)
