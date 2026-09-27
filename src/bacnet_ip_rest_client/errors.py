"""Exceptions raised by Client.

Everything derives from ProxyError, an ordinary Exception. This differs
from bacpypes3, whose ErrorPDU/RejectPDU/AbortPDU derive from
BaseException and so slip past `except Exception:`.

The proxy's HTTP status codes map onto a subclass each. A BACnet Error,
Reject or Abort from the device arrives as a 502 and becomes BACnetError,
carrying the device's numeric Error_Class/Error_Code the way bacpypes3's
ErrorPDU carries errorClass/errorCode. A Reject or Abort is not told apart
from an Error: the proxy reports all three the same way.
"""

from typing import Any

import httpx

# ASHRAE 135 clause 21 Error_Code values meaning "no such object" and "the
# object has no such property".
ERROR_CODE_UNKNOWN_OBJECT = 31
ERROR_CODE_UNKNOWN_PROPERTY = 32


class ProxyError(Exception):
    """Base class: a request to the proxy did not succeed."""


class CommunicationError(ProxyError):
    """The proxy could not be reached, or its reply could not be decoded
    (e.g. an empty or truncated 200 body). The underlying exception is
    chained as __cause__."""


class ProxyStatusError(ProxyError):
    """The proxy answered with an HTTP error status."""

    status_code: int
    message: str
    error_class: int | None
    error_code: int | None

    def __init__(self, status_code: int, message: str, error_class: int | None = None, error_code: int | None = None):
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.error_class = error_class
        self.error_code = error_code

    @property
    def is_unknown_object_or_property(self) -> bool:
        """Whether this means the object or property doesn't exist. The
        proxy says so either with a 404, when it can tell without asking
        the device, or with a 502 carrying the device's unknown-object or
        unknown-property Error_Code."""
        return self.status_code == 404 or self.error_code in (
            ERROR_CODE_UNKNOWN_OBJECT, ERROR_CODE_UNKNOWN_PROPERTY,
        )


class BadRequestError(ProxyStatusError):
    """400: the request was malformed (e.g. an unknown object type name)."""


class UnauthorizedError(ProxyStatusError):
    """401: the proxy did not accept the credentials (missing, invalid or
    expired token)."""


class ForbiddenError(ProxyStatusError):
    """403: the credentials were accepted, but the proxy's authorization
    rules deny this request -- e.g. a write to a variable the token only
    has readonly access to."""


class NotFoundError(ProxyStatusError):
    """404: unknown device, object or property."""


class ValueRejectedError(ProxyStatusError):
    """422: the device rejected the value written (e.g. out of range)."""


class BACnetError(ProxyStatusError):
    """502: the device answered with a BACnet Error, Reject or Abort."""


class BACnetTimeoutError(ProxyStatusError):
    """504: the device did not respond in time."""


_STATUS_CLASSES: dict[int, type[ProxyStatusError]] = {
    400: BadRequestError,
    401: UnauthorizedError,
    403: ForbiddenError,
    404: NotFoundError,
    422: ValueRejectedError,
    502: BACnetError,
    504: BACnetTimeoutError,
}


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def status_error(response: httpx.Response) -> ProxyStatusError:
    """Build the exception for an error response. The body is the proxy's
    Error schema; anything else (e.g. an HTML error page from a reverse
    proxy in front of it) is kept as a truncated message."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        message = str(body.get("error") or response.reason_phrase)
        error_class = _int_or_none(body.get("bacnetErrorClass"))
        error_code = _int_or_none(body.get("bacnetErrorCode"))
    else:
        message = response.text[:200] or response.reason_phrase
        error_class = error_code = None
    cls = _STATUS_CLASSES.get(response.status_code, ProxyStatusError)
    return cls(response.status_code, message, error_class, error_code)
