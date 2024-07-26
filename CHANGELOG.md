# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
## [1.1.0] - 2024-07-26
### Changed
- [hikari.api.RESTClient][] can now be passed as app to
  [sake.redis.ResourceClient.__init__][].

### Fixed
- Case where the OwnIDStore would error on returning a phantom stored ID.

## [1.0.8] - 2023-12-28
### Added
- Support for Python 3.12.

## [1.0.7] - 2023-02-20
### Fixed
- Fix [hikari.errors.UnrecognisedEntityError][] handling in the Redis iterators.
  This will no-longer lead to an [NameError][] being raised.

## [1.0.6] - 2023-01-01
### Fixed
- Catch and ignore [hikari.errors.UnrecognisedEntityError][] during iteration.

## [1.0.5a1] - 2022-12-08
### Added
- `"tanjun"` feature flag for ensuring this is installed with a Tanjun version that's
  compatible with the Tanjun adapters.

### Changed
- Dropped support for Python 3.8.

## [1.0.4a1.post1] - 2022-11-10
### Changed
- Switched from aioredis to redis-py for connecting to Redis to properly support
  Python 3.11.

## [1.0.4a1] - 2022-11-10
### Changed
- Bumped the minimum hikari version to 2.0.0.dev112.
- Avoid fetching the bot's user to get it's ID if a READY event has already
  been received.
- The Tanjun adapter get and iter methods no-longer raise [TypeError][] when
  the inner-client is inactive.

### Fixed
- Async locking when fetching the bot's ID internally and ensure it only
  fetches once.
- Raise [sake.errors.ClosedClient][] instead of [TypeError][] when the Sake
  client is inactive (from get and iter methods).

### Removed
- The project metadata dunder attributes from [sake][].
  [importlib.metadata][] should be used to get this metadata instead.
- `sake.redis_iterators` is now internal/hidden.
- `redis.ResourceClient.get_connection` is now internal/hidden.

## [1.0.3a1.post1] - 2022-08-30
### Changed
- Bumped the minimum hikari version to 2.0.0.dev110.

## [1.0.3a1] - 2022-08-27
### Fixed
- Fixed compatibility with newer Hikari and Tanjun releases.

## [1.0.2a1] - 2022-01-13
### Fixed
- The Redis `clear_voice_states_for_guild` method mishandling passing coroutines to
  `asyncio.gather` leading to errors being raised.

### Removed
- The Prefix and Integration cache implementations and interfaces as a part of
  pruning out-of-scope and useless cache stores.

## [1.0.1a1] - 2022-01-06
### Added
- `RedisResource.add_to_tanjun` method for easier integration with Tanjun.
  This both registers dependencies for the resource abcs and impls but also
  registers adapters which make Sake compatible with Tanjun's AsyncCache
  interfaces for the relevant resources.

### Changed
- `abc.ABC` is now used as the base class for the abstract interfaces in
  `sake.abc` instead of `typing.Protocol`.
- `abc.CacheIterator.len` is now guaranteed to always returns `int`.

## [1.0.0a1] - 2021-12-25
### Added
- Python 3.10 compatibility thx to Aioredis 2.0.0.
- Efficiency improvements brought in by the switch to listening to raw
  event dispatch.
- `ClosedClient` error which is raised in favour of `RuntimeError` when
  an operation is attempted on an inactive sake client.
- Client `is_alive` attribute (both on the abstract interface and redit impl).

### Changed
- Aioredis dependency minimum version to 2.0.0.
  This comes with breaking changes to how RedisClient is initialised as
  parameters have been replaced and changed (semantically speaking).
  For more information on this see the documentation.
  And the client returned by `RedisClient.get_connection` is now
  fundamentally different.
- Bumped Hikari dependency minimum version to 2.0.0.dev104.
- The structure and semantics of internal aspects of RedisClient like how
  it manages the database connections and how it loads listeners have changed.
- Renamed `sake.traits` to `sake.abc`.
- Renamed `RedisClient.rest` to `app` and `.dispatch` to `.event_manager`.
- IDs are now always stored as strings.
- Renamed `rest` to `app` in `RedisClient.__init__` and moved away from inferring
  the event manager from this argument (it now must always be passed as
  `event_manager` if you want the client to listen to events to fill itself).
- `event_managed` argument to `RedisClient.__init__` to let the client be started
  based on the linked event manager.

### Removed
- `sake.marshalling` and any relevant properties and arguments.
- The utility functions in `sake.redis_iterators` are now private.
- The "set" methods have been removed from the abstract interfaces in `sake.abc`
  as these are now considered implementation detail.
- While this wasn't ever officially supported, passing `hikari.Unique` objects in
  place of IDs to RedisClients will now lead to erroneous behaviour due to an
  internal refactor in how IDs are handled.
- `window_size` parameter from `RedisClient.clear_*` methods as these are no-longer
  chunked.

[Unreleased]: https://github.com/FasterSpeeding/Sake/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/FasterSpeeding/Sake/compare/v1.0.8...v1.1.0
[1.0.8]: https://github.com/FasterSpeeding/Sake/compare/v1.0.7...v1.0.8
[1.0.7]: https://github.com/FasterSpeeding/Sake/compare/v1.0.6...v1.0.7
[1.0.6]: https://github.com/FasterSpeeding/Sake/compare/v1.0.5a1...v1.0.6
[1.0.5a1]: https://github.com/FasterSpeeding/Sake/compare/v1.0.4a1.post1...v1.0.5a1
[1.0.4a1.post1]: https://github.com/FasterSpeeding/Sake/compare/v1.0.4a1...v1.0.4a1.post1
[1.0.4a1]: https://github.com/FasterSpeeding/Sake/compare/v1.0.3a1.post1...v1.0.4a1
[1.0.3a1.post1]: https://github.com/FasterSpeeding/Sake/compare/v1.0.3a1...v1.0.3a1.post1
[1.0.3a1]: https://github.com/FasterSpeeding/Sake/compare/v1.0.2a1...v1.0.3a1
[1.0.2a1]: https://github.com/FasterSpeeding/Sake/compare/v1.0.1a1...v1.0.2a1
[1.0.1a1]: https://github.com/FasterSpeeding/Sake/compare/v1.0.0a1...v1.0.1a1
[1.0.0a1]: https://github.com/FasterSpeeding/Sake/compare/0.0.1...v1.0.0a1
