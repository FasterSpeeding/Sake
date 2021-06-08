# -*- coding: utf-8 -*-
# cython: language_level=3
# BSD 3-Clause License
#
# Copyright (c) 2020-2021, Faster Speeding
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

__all__: typing.Sequence[str] = [
    "as_listener",
    "as_raw_listener",
    "convert_expire_time",
    "ExpireT",
    "find_listeners",
    "find_raw_listeners",
    "ListenerProto",
    "RawListenerProto",
    "try_find_type",
]

import datetime
import inspect
import math
import typing

from hikari import undefined

if typing.TYPE_CHECKING:
    from hikari.events import base_events
    from hikari.events import shard_events

ExpireT = typing.Union["datetime.timedelta", int, float, None]
"""A type hint used to represent expire times.

These may either be the number of seconds as an int or float (where millisecond
precision is supported) or a timedelta. `builtins.None`, float("nan") and
float("inf") all represent no expire.
"""

T = typing.TypeVar("T")
EventT_inv = typing.TypeVar("EventT_inv", bound="base_events.Event")
EventT_co = typing.TypeVar("EventT_co", bound="base_events.Event", covariant=True)
CallbackT = typing.Callable[["T", EventT_co], typing.Coroutine[typing.Any, typing.Any, None]]


def convert_expire_time(expire: ExpireT, /) -> typing.Optional[int]:
    """Convert a timedelta, int or float expire time representation to an integer."""
    if expire is None:
        return None

    if isinstance(expire, datetime.timedelta):
        return round(expire.total_seconds() * 1000)

    if isinstance(expire, int):
        return expire * 1000

    if isinstance(expire, float):
        if math.isnan(expire) or math.isinf(expire):
            return None

        return round(expire * 1000)

    raise ValueError(f"Invalid expire time passed; expected a float, int or timedelta but got a {type(expire)!r}")


@typing.runtime_checkable
class ListenerProto(typing.Protocol[EventT_inv]):
    async def __call__(self, event: EventT_inv, /) -> None:
        raise NotImplementedError

    @property
    def __sake_event_type__(self) -> typing.Type[EventT_inv]:
        raise NotImplementedError


@typing.runtime_checkable
class RawListenerProto(typing.Protocol):
    async def __call__(self, event: shard_events.ShardPayloadEvent, /) -> None:
        raise NotImplementedError

    @property
    def __sake_event_names__(self) -> typing.Sequence[str]:
        raise NotImplementedError


def as_listener(
    event_type: typing.Type[EventT_co], /
) -> typing.Callable[[CallbackT[T, EventT_co]], CallbackT[T, EventT_co]]:
    def decorator(listener: CallbackT[T, EventT_co], /) -> CallbackT[T, EventT_co]:
        listener.__sake_event_type__ = event_type  # type: ignore[attr-defined]
        assert isinstance(listener, ListenerProto), "Incorrect attributes set for listener"
        return listener  # type: ignore[unreachable]

    return decorator


def as_raw_listener(
    event_name: str, /, *event_names: str
) -> typing.Callable[[CallbackT[T, shard_events.ShardPayloadEvent]], CallbackT[T, shard_events.ShardPayloadEvent]]:
    event_names = (event_name.upper(), *(name.upper() for name in event_names))

    def decorator(
        listener: CallbackT[T, shard_events.ShardPayloadEvent], /
    ) -> CallbackT[T, shard_events.ShardPayloadEvent]:
        listener.__sake_event_names__ = event_names  # type: ignore[attr-defined]
        assert isinstance(listener, RawListenerProto), "Incorrect attributes set for raw listener"
        return listener  # type: ignore[unreachable]

    return decorator


def find_listeners(
    obj: typing.Any, /
) -> typing.Dict[typing.Type[base_events.Event], typing.List[ListenerProto[base_events.Event]]]:
    listeners: typing.Dict[typing.Type[base_events.Event], typing.List[ListenerProto[base_events.Event]]] = {}
    for _, member in inspect.getmembers(obj):
        if isinstance(member, ListenerProto):
            try:
                listeners[member.__sake_event_type__].append(member)

            except KeyError:
                listeners[member.__sake_event_type__] = [member]

    return listeners


def find_raw_listeners(obj: typing.Any, /) -> typing.Dict[str, typing.List[RawListenerProto]]:
    raw_listeners: typing.Dict[str, typing.List[RawListenerProto]] = {}
    for _, member in inspect.getmembers(obj):
        if isinstance(member, RawListenerProto):
            for name in member.__sake_event_names__:
                try:
                    raw_listeners[name].append(member)

                except KeyError:
                    raw_listeners[name] = [member]

    return raw_listeners


def try_find_type(cls: typing.Type[T], /, *values: typing.Any) -> undefined.UndefinedOr[T]:
    for value in values:
        if isinstance(value, cls):
            return value

    return undefined.UNDEFINED
