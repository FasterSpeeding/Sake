from __future__ import annotations

__all__: typing.Final[typing.Sequence[str]] = [
    "CannotDelete",
    "EntryNotFound",
    "InvalidDataFound",
    "SakeException",
]

import typing


class SakeException(Exception):
    """A base exception for the expected exceptions raised by Sake implementations."""

    __slots__: typing.Sequence[str] = ("base_exception", "message")

    message: str
    base: typing.Optional[BaseException]

    def __init__(self, message: str, *, exception: typing.Optional[BaseException] = None) -> None:
        self.message = message
        self.base_exception = exception

    def __repr__(self) -> str:
        return f"{type.__name__}({self.message!r})"


class CannotDelete(SakeException, ValueError):
    __slots__: typing.Sequence[str] = ()


class InvalidDataFound(SakeException, LookupError):
    __slots__: typing.Sequence[str] = ()


class EntryNotFound(SakeException, LookupError):
    __slots__: typing.Sequence[str] = ()
