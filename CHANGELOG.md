# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Changed
- `abc.ABC` is now used as the base class for the abstract interfaces in
  `sake.abc` instead of `typing.Protocol`.

## [1.0.0a1] - 2021-12-25
### Added
- Python 3.10 compatibility thx to Aioredis 2.0.0.
- Efficiency improvements brought in by the switch to listening to raw
  event dispatch.
- `ClosedClient` error which is raised in favour of `RuntimeError` when
  an operation is attempted on an inactive sake client.
- Client `is_alive` attribute (both on the abstract interface and redit impl).

### Changed
- Aioredis dependency minimum version to 2.0.0
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

[Unreleased]: https://github.com/FasterSpeeding/Sake/compare/v1.0.0a1...HEAD
[1.0.0a1]: https://github.com/FasterSpeeding/Sake/compare/0.0.1...v1.0.0a1
