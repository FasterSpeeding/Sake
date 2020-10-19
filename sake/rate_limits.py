from __future__ import annotations

__all__: typing.Final[typing.Sequence[str]] = ["BACKOFF_ERRORS", "ExponentialBackOff"]

import asyncio
import typing

from hikari import errors as hikari_errors
from hikari.impl import rate_limits as _rate_limits


BACKOFF_ERRORS: typing.Final[typing.Sequence[BaseException]] = (
    hikari_errors.RateLimitedError,
    hikari_errors.InternalServerError,
)


class ExponentialBackOff:
    __slots__: typing.Sequence[str] = ("_backoff", "_started")

    def __init__(
        self, base: float = 2.0, maximum: float = 64.0, jitter_multiplier: float = 1.0, initial_increment: int = 0
    ) -> None:
        self._backoff = _rate_limits.ExponentialBackOff(
            base=base, maximum=maximum, jitter_multiplier=jitter_multiplier, initial_increment=initial_increment
        )
        self._started = False

    def __aiter__(self) -> ExponentialBackOff:
        return self

    async def __anext__(self) -> None:
        if not self._started:
            self._started = True
            return

        await asyncio.sleep(next(self._backoff))
