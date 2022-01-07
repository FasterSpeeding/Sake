# -*- coding: utf-8 -*-
# cython: language_level=3
# BSD 3-Clause License
#
# Copyright (c) 2020-2022, Faster Speeding
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
    "ListenerProto",
    "RawListenerProto",
]

import datetime
import inspect
import math
import typing

import hikari

ExpireT = typing.Union["datetime.timedelta", int, float, None]
"""A type hint used to represent expire times.

These may either be the number of seconds as an int or float (where millisecond
precision is supported) or a timedelta. `builtins.None`, float("nan") and
float("inf") all represent no expire.
"""

_T = typing.TypeVar("_T")
_EventT_inv = typing.TypeVar("_EventT_inv", bound=hikari.Event)
_EventT = typing.TypeVar("_EventT", bound=hikari.Event)
_CallbackT = typing.Callable[["_T", _EventT], typing.Coroutine[typing.Any, typing.Any, None]]


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
class ListenerProto(typing.Protocol[_EventT_inv]):
    async def __call__(self, event: _EventT_inv, /) -> None:
        raise NotImplementedError

    @property
    def __sake_event_type__(self) -> typing.Type[_EventT_inv]:
        raise NotImplementedError


@typing.runtime_checkable
class RawListenerProto(typing.Protocol):
    async def __call__(self, event: hikari.ShardPayloadEvent, /) -> None:
        raise NotImplementedError

    @property
    def __sake_event_names__(self) -> typing.Sequence[str]:
        raise NotImplementedError


def as_listener(
    event_type: typing.Type[_EventT], /
) -> typing.Callable[[_CallbackT[_T, _EventT]], _CallbackT[_T, _EventT]]:
    def decorator(listener: _CallbackT[_T, _EventT], /) -> _CallbackT[_T, _EventT]:
        listener.__sake_event_type__ = event_type
        assert isinstance(listener, ListenerProto), "Incorrect attributes set for listener"
        return listener

    return decorator


def as_raw_listener(
    event_name: str, /, *event_names: str
) -> typing.Callable[[_CallbackT[_T, hikari.ShardPayloadEvent]], _CallbackT[_T, hikari.ShardPayloadEvent]]:
    event_names = (event_name.upper(), *(name.upper() for name in event_names))

    def decorator(listener: _CallbackT[_T, hikari.ShardPayloadEvent], /) -> _CallbackT[_T, hikari.ShardPayloadEvent]:
        listener.__sake_event_names__ = event_names
        assert isinstance(listener, RawListenerProto), "Incorrect attributes set for raw listener"
        return listener

    return decorator


def find_listeners(
    obj: typing.Any, /
) -> tuple[
    typing.Dict[typing.Type[hikari.Event], typing.List[ListenerProto[hikari.Event]]],
    typing.Dict[str, typing.List[RawListenerProto]],
]:
    listeners: typing.Dict[typing.Type[hikari.Event], typing.List[ListenerProto[hikari.Event]]] = {}
    raw_listeners: typing.Dict[str, typing.List[RawListenerProto]] = {}
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
