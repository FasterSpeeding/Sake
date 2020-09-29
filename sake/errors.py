import typing

__all__: typing.Final[typing.Sequence[str]] = ("SakeException", "NotFound", "KeptAliveByReference")


class SakeException(Exception):
    __slots__: typing.Sequence[str] = ()


class NotFound(SakeException, LookupError):
    __slots__: typing.Sequence[str] = ()


class KeptAliveByReference(NotFound, ValueError):
    __slots__: typing.Sequence[str] = ()
