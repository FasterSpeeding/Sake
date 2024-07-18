# -*- coding: utf-8 -*-
# BSD 3-Clause License
#
# Copyright (c) 2020-2024, Faster Speeding
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
from __future__ import annotations

__all__: list[str] = [
    "ExpireT",
    "ListenerProto",
    "RawListenerProto",
    "as_listener",
    "as_raw_listener",
    "convert_expire_time",
    "find_listeners",
]

import asyncio
import datetime
import inspect
import math
import typing
from collections import abc as collections

import hikari

if typing.TYPE_CHECKING:
    from . import redis

ExpireT = typing.Union["datetime.timedelta", int, float, None]
"""A type hint used to represent expire times.

These may either be the number of seconds as an [int][] or [float][] (where
millisecond precision is supported) or a timedelta. [None][], `float("nan")` and
`float("inf")` all represent no expire.
"""

_T = typing.TypeVar("_T")
_EventT_inv = typing.TypeVar("_EventT_inv", bound=hikari.Event)
_EventT = typing.TypeVar("_EventT", bound=hikari.Event)
_CallbackT = collections.Callable[["_T", _EventT], collections.Coroutine[typing.Any, typing.Any, None]]


def convert_expire_time(expire: ExpireT, /) -> typing.Optional[int]:
    """Convert a timedelta, int or float expire time representation to an integer."""
    if expire is None:
        return None

    if isinstance(expire, datetime.timedelta):
        return round(expire.total_seconds() * 1000)

    if isinstance(expire, int):
        return expire * 1000

    if isinstance(expire, float):  # pyright: ignore[reportUnnecessaryIsInstance]
        if math.isnan(expire) or math.isinf(expire):
            return None

        return round(expire * 1000)

    raise ValueError(f"Invalid expire time passed; expected a float, int or timedelta but got a {type(expire)!r}")


@typing.runtime_checkable
class ListenerProto(typing.Protocol[_EventT_inv]):
    """Protocol of an event listener method."""

    async def __call__(self, event: _EventT_inv, /) -> None:
        """Execute the event listener.

        Parameters
        ----------
        event
            The event object.
        """
        raise NotImplementedError

    @property
    def __sake_event_type__(self) -> type[_EventT_inv]:
        """The event type this listener is listening for."""
        raise NotImplementedError


@typing.runtime_checkable
class RawListenerProto(typing.Protocol):
    """Protocol of a raw event listener method."""

    async def __call__(self, event: hikari.ShardPayloadEvent, /) -> None:
        """Execute the raw listener.

        Parameters
        ----------
        event
            The raw event object.
        """
        raise NotImplementedError

    @property
    def __sake_event_names__(self) -> collections.Sequence[str]:
        """Sequence of the raw event names this is listening for."""
        raise NotImplementedError


def as_listener(
    event_type: type[_EventT], /
) -> collections.Callable[[_CallbackT[_T, _EventT]], _CallbackT[_T, _EventT]]:
    """Mark a method as an event listener on a client implementation.

    Parameters
    ----------
    event_type
        Type of the event this is listening for.

    Returns
    -------
    collections.abc.Callable[[_CallbackT[_T, _EventT]],  _Callback[_T, _EventT]]
        Decorator callback which marks the method as an event listener.
    """

    def decorator(listener: _CallbackT[_T, _EventT], /) -> _CallbackT[_T, _EventT]:
        listener.__sake_event_type__ = event_type  # type: ignore
        assert isinstance(listener, ListenerProto), "Incorrect attributes set for listener"
        return listener

    return decorator


def as_raw_listener(
    event_name: str, /, *event_names: str
) -> collections.Callable[[_CallbackT[_T, hikari.ShardPayloadEvent]], _CallbackT[_T, hikari.ShardPayloadEvent]]:
    """Mark a method as a raw event listener on a client implementation.

    Parameters
    ----------
    event_name
        Name of the raw event this is listening for.
    event_names
        Name of other raw events this is listening for.

    Returns
    -------
    collections.abc.Callable[[_CallbackT[_T,hikari.ShardPayloadEvent]], _CallbackT[_T,hikari.ShardPayloadEvent]]
        Decorator callback which marks the method as a raw event listener.
    """
    event_names = (event_name.upper(), *(name.upper() for name in event_names))

    def decorator(listener: _CallbackT[_T, hikari.ShardPayloadEvent], /) -> _CallbackT[_T, hikari.ShardPayloadEvent]:
        listener.__sake_event_names__ = event_names  # type: ignore
        assert isinstance(listener, RawListenerProto), "Incorrect attributes set for raw listener"
        return listener

    return decorator


def find_listeners(
    obj: typing.Any, /
) -> tuple[dict[type[hikari.Event], list[ListenerProto[hikari.Event]]], dict[str, list[RawListenerProto]]]:
    """Find all the event and raw-event listener methods on an object.

    Parameters
    ----------
    obj
        The object to find the listeners on.

    Returns
    -------
    tuple[dict[type[hikari.Event], list[ListenerProto]], dict[str, list[RawListenerProto]]]
        A tuple of two elements:

        0. A dictionary of hikari event types to the found event listener methods.
        1. A dictionary of event names to the found raw event listener methods.
    """
    listeners: dict[type[hikari.Event], list[ListenerProto[hikari.Event]]] = {}
    raw_listeners: dict[str, list[RawListenerProto]] = {}
    for _, member in inspect.getmembers(obj):
        if isinstance(member, ListenerProto):
            try:
                listeners[member.__sake_event_type__].append(member)

            except KeyError:
                listeners[member.__sake_event_type__] = [member]

        if isinstance(member, RawListenerProto):
            for name in member.__sake_event_names__:
                try:
                    raw_listeners[name].append(member)

                except KeyError:
                    raw_listeners[name] = [member]

    return listeners, raw_listeners


class OwnIDStore:
    """Helper class for tracking the current users' ID."""

    __slots__ = ("_app", "_lock", "value")

    KEY: typing.Final[str] = "OWN_ID"

    def __init__(self, app: hikari.RESTAware, /) -> None:
        self._app = app
        self._lock: typing.Optional[asyncio.Lock] = None
        self.value: typing.Optional[hikari.Snowflake] = None

    @classmethod
    def get_from_client(cls, client: redis.ResourceClient) -> OwnIDStore:
        try:
            own_id: OwnIDStore = client.config[OwnIDStore.KEY]
            assert isinstance(own_id, OwnIDStore)

        except KeyError:
            client.config[OwnIDStore.KEY] = own_id = OwnIDStore(client.app)

        return own_id

    async def await_value(self) -> hikari.Snowflake:
        if self.value is not None:
            return self.value

        if not self._lock:
            self._lock = asyncio.Lock()

        async with self._lock:
            if self.value is not None:
                return self.value

            user = await self._app.rest.fetch_my_user()
            self.value = user.id
            self._lock = None
            return self.value

    def set_value(self, value: hikari.Snowflake) -> None:
        if self._lock:
            self._lock = None

        self.value = value
