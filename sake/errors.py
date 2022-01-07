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
"""The standard error bases which Sake implementations will be raising.

.. note::
    These supplement python's builtin exceptions but do not replace them.
"""

from __future__ import annotations

__all__: typing.Sequence[str] = [
    "BackendError",
    "CannotDelete",
    "ClosedClient",
    "EntryNotFound",
    "InvalidDataFound",
    "SakeException",
]

import typing


class SakeException(Exception):
    """Base exception for the expected exceptions raised by Sake implementations.

    Parameters
    ----------
    message : str
        The exception's message.
    base : typing.Optional[Exception]
        The exception which caused this exception if applicable else `builtins.None`.
    """

    __slots__: typing.Sequence[str] = ("base_exception", "message")

    message: str
    """The exception's message, this may be an empty string if there is no message."""

    base: typing.Optional[Exception]
    """The exception which caused this exception if applicable else `builtins.None`."""

    def __init__(self, message: str, *, exception: typing.Optional[Exception] = None) -> None:
        self.message: str = message
        self.base_exception: typing.Optional[Exception] = exception

    def __repr__(self) -> str:
        return f"{type.__name__}({self.message!r})"


class BackendError(SakeException, ValueError):
    """Error that's raised when communicating with the backend fails

    This may be a sign of underlying network or database issues.
    """

    __slots__: typing.Sequence[str] = ()


class ClosedClient(SakeException):
    """Error that's raised when an attempt to use an inactive client is made."""

    __slots__: typing.Sequence[str] = ()


class CannotDelete(SakeException, ValueError):
    """Error that's raised in response to an attempt to delete an entry which can't be deleted.

    This most likely reason for this to be raised would be due to an attempt to
    deleted a entry that's being kept alive by references without specifying to
    cascade references in a referential database.
    """

    __slots__: typing.Sequence[str] = ()


class InvalidDataFound(SakeException, LookupError):
    """Error that's raised when the retrieved data is in an unexpected format.

    This may indicate that you are running different versions of a Sake
    implementation with the same database.
    """

    __slots__: typing.Sequence[str] = ()


class EntryNotFound(SakeException, LookupError):
    """Error that's raised in response to an attempt to get an entry which doesn't exist.

    .. note::
        This shouldn't ever be raised by a delete method or iter method.
    """

    __slots__: typing.Sequence[str] = ()
