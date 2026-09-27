"""Client for the BACnet/IP REST proxy
(https://github.com/stblassitude/bacnet-ip-rest-proxy)."""

from .client import API_PATH, Client, RetryPolicy, is_transient
from .errors import (
    ERROR_CODE_UNKNOWN_OBJECT,
    ERROR_CODE_UNKNOWN_PROPERTY,
    BACnetError,
    BACnetTimeoutError,
    BadRequestError,
    CommunicationError,
    ForbiddenError,
    NotFoundError,
    ProxyError,
    ProxyStatusError,
    UnauthorizedError,
    ValueRejectedError,
)
from .objects import PRIORITY_COUNT, Access, ObjectIdentifier, ObjectSummary
from .values import decode_value, encode_value

__all__ = [
    "API_PATH",
    "Access",
    "BACnetError",
    "BACnetTimeoutError",
    "BadRequestError",
    "Client",
    "CommunicationError",
    "ERROR_CODE_UNKNOWN_OBJECT",
    "ERROR_CODE_UNKNOWN_PROPERTY",
    "ForbiddenError",
    "NotFoundError",
    "ObjectIdentifier",
    "ObjectSummary",
    "PRIORITY_COUNT",
    "ProxyError",
    "ProxyStatusError",
    "RetryPolicy",
    "UnauthorizedError",
    "ValueRejectedError",
    "decode_value",
    "encode_value",
    "is_transient",
]
