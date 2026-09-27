"""The HTTP client for the BACnet/IP REST proxy."""

import asyncio
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from .errors import (
    BACnetError,
    BACnetTimeoutError,
    CommunicationError,
    ProxyError,
    status_error,
)
from .objects import PRIORITY_COUNT, ObjectIdentifier, ObjectSummary
from .values import decode_value, encode_value

log = logging.getLogger(__package__)

API_PATH = "/api/v1"

ObjectIdentifierLike = ObjectIdentifier | tuple[str, int] | str

# One request for Client.read_properties(): (objid, prop) or
# (objid, prop, array_index).
PropertyRequest = tuple[ObjectIdentifierLike, str] | tuple[ObjectIdentifierLike, str, int | None]


@dataclass(frozen=True)
class RetryPolicy:
    """How often, and how patiently, a transient failure is retried.

    `attempts` counts the first try, so `RetryPolicy(attempts=1)` disables
    retrying. The delay before retry n (1-based) is `base_delay * 2**(n-1)`,
    capped at `max_delay`: 1+2+4+8 = 15s in total with the defaults.
    """

    attempts: int = 5
    base_delay: float = 1.0
    max_delay: float = 8.0

    def delay(self, attempt: int) -> float:
        return min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)


def is_transient(exc: ProxyError) -> bool:
    """Whether a failed request is worth retrying: the proxy couldn't be
    reached or sent a garbled reply, the device timed out, or the device
    answered with an error other than "no such object/property". Any other
    status (400/401/403/404/422) is a property of the request itself and
    would fail again the same way."""
    if isinstance(exc, CommunicationError | BACnetTimeoutError):
        return True
    if isinstance(exc, BACnetError):
        return not exc.is_unknown_object_or_property
    return False


def _segment(value: Any) -> str:
    return quote(str(value), safe=":()")


class Client:
    """A client of one BACnet/IP REST proxy, playing the part of a
    bacpypes3 Application: `read_property()` and `write_property()` take
    the same arguments as bacpypes3's, except that the device is addressed
    by the proxy's device id (a configured alias, or a hostname/IP[:port])
    instead of a BACnet Address.

    Where the proxy works differently from BACnet, so does this class:

    - Errors are raised, never returned (see errors.py).
    - `list_objects()` returns the proxy's cached object catalog, including
      each object's name and the caller's `access` to it, in one request.
    - There is no ReadPropertyMultiple. `read_properties()` issues one HTTP
      request per property instead, concurrently, and reports each one's
      outcome separately.
    - Every request goes through the proxy's authorization, based on
      `token`. A client acting for different users needs one Client per
      user token.
    - Transient failures are retried (see RetryPolicy and is_transient()),
      writes included: a WriteProperty of the same value at the same
      priority is idempotent.

    Use as an async context manager, or call `aclose()` when done.
    """

    def __init__(
        self,
        url: str,
        token: str | None = None,
        *,
        timeout: float = 30.0,
        verify: bool | str = True,
        retry: RetryPolicy = RetryPolicy(),
        concurrency: int = 10,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        """`url` is the proxy's base URL, without the /api/v1 path.

        `token` is sent as a bearer token: one of the proxy's configured
        tokens, or a JWT it accepts (e.g. a user's OIDC access token).

        `timeout` is the default per-request timeout in seconds; each call
        can override it. `verify` is passed to httpx: False to accept a
        self-signed certificate, or the path of a CA bundle.

        `concurrency` bounds how many requests this client has in flight
        at once, across all calls. The proxy serializes what it sends to
        the device itself; this keeps a large `read_properties()` from
        opening hundreds of connections to the proxy. A request waiting
        out a retry delay doesn't hold a slot.

        `transport` replaces httpx's network transport, e.g. with an
        httpx.MockTransport in tests.
        """
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = httpx.AsyncClient(
            base_url=url.rstrip("/") + API_PATH,
            headers=headers,
            timeout=timeout,
            verify=verify,
            transport=transport,
        )
        self._retry = retry
        self._slots = asyncio.Semaphore(concurrency)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "Client":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.aclose()

    async def _send(self, method: str, path: str, *, timeout: float | None, **kwargs) -> httpx.Response:
        if timeout is not None:
            kwargs["timeout"] = timeout
        async with self._slots:
            try:
                response = await self._http.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                raise CommunicationError(f"{method} {path}: {exc!r}") from exc
        if response.is_error:
            raise status_error(response)
        return response

    async def _request(self, method: str, path: str, *, timeout: float | None = None, **kwargs) -> Any:
        """Send a request with retries; return the decoded JSON body, or
        None for a response without one (204)."""
        attempt = 1
        while True:
            try:
                response = await self._send(method, path, timeout=timeout, **kwargs)
                if response.status_code == 204 or (method != "GET" and not response.content):
                    return None
                try:
                    return response.json()
                except ValueError as exc:
                    # Seen under concurrent load: a 200 with an empty or
                    # truncated body.
                    raise CommunicationError(f"{method} {path}: undecodable response body") from exc
            except ProxyError as exc:
                if attempt >= self._retry.attempts or not is_transient(exc):
                    raise
                delay = self._retry.delay(attempt)
                log.warning(
                    "%s %s: attempt %d/%d failed (%s), retrying in %.1fs",
                    method, path, attempt, self._retry.attempts, exc, delay,
                )
            await asyncio.sleep(delay)
            attempt += 1

    @staticmethod
    def _property_path(device: str, objid: ObjectIdentifier, prop: str) -> str:
        return (
            f"/devices/{_segment(device)}/objects/{_segment(objid.type)}/{objid.instance}"
            f"/{_segment(prop)}"
        )

    async def list_devices(self, *, timeout: float | None = None) -> list[str]:
        """The device ids (aliases) configured on the proxy."""
        body = await self._request("GET", "/devices", timeout=timeout)
        return [entry["id"] for entry in body]

    async def read_device(self, device: str, *, timeout: float | None = None) -> dict[str, Any]:
        """A device's identity summary, as the proxy's DeviceInfo JSON:
        `instance`, `object-name`, `vendor-name`, `model-name`, `access`."""
        return await self._request("GET", f"/devices/{_segment(device)}", timeout=timeout)

    async def list_objects(self, device: str, *, timeout: float | None = None) -> list[ObjectSummary]:
        """Every object on the device the caller may read, from the proxy's
        cache (refreshed proxy-side, by default every 60s). Objects the
        caller may not read are left out; if that is all of them, this
        raises ForbiddenError."""
        body = await self._request("GET", f"/devices/{_segment(device)}/objects", timeout=timeout)
        return [ObjectSummary.from_json(entry) for entry in body]

    async def read_object(self, device: str, objid: ObjectIdentifierLike, *, timeout: float | None = None) -> dict[str, Any]:
        """A summary of one object, read by the proxy in a single
        ReadPropertyMultiple: the proxy's ObjectInfo JSON, keyed by
        property name (`object-name`, `present-value`, `description`,
        `units`, `status-flags`), plus `access`. Properties the object
        doesn't have are left out."""
        objid = ObjectIdentifier.parse(objid)
        body = await self._request(
            "GET", f"/devices/{_segment(device)}/objects/{_segment(objid.type)}/{objid.instance}",
            timeout=timeout,
        )
        if "present-value" in body:
            body["present-value"] = decode_value(objid.type, "present-value", body["present-value"])
        return body

    async def read_property(
        self,
        device: str,
        objid: ObjectIdentifierLike,
        prop: str,
        array_index: int | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        """Read one property (bacpypes3: `read_property(address, objid,
        prop, array_index)`) and return its value, decoded as described in
        values.py. `prop` is the property name, e.g. "present-value", or
        "property(N)" for one the proxy has no name for."""
        objid = ObjectIdentifier.parse(objid)
        params = {"index": array_index} if array_index is not None else None
        body = await self._request(
            "GET", self._property_path(device, objid, prop), params=params, timeout=timeout,
        )
        return decode_value(objid.type, prop, body.get("value"), array_index)

    async def read_properties(
        self,
        device: str,
        requests: Iterable[PropertyRequest],
        *,
        timeout: float | None = None,
    ) -> list[Any]:
        """Read several properties concurrently, one request each, and
        return a list in the same order holding each value or, for a read
        that failed, its ProxyError. Unlike a BACnet ReadPropertyMultiple
        this is not a single transaction: each value is read at a
        slightly different moment, and each read succeeds or fails on
        its own."""

        async def one(request: Sequence) -> Any:
            objid, prop, *rest = request
            try:
                return await self.read_property(device, objid, prop, *rest, timeout=timeout)
            except ProxyError as exc:
                return exc

        return list(await asyncio.gather(*(one(r) for r in requests)))

    async def write_property(
        self,
        device: str,
        objid: ObjectIdentifierLike,
        prop: str,
        value: Any,
        priority: int | None = None,
        *,
        timeout: float | None = None,
    ) -> None:
        """Write one property (bacpypes3: `write_property(address, objid,
        prop, value, array_index, priority)`, minus `array_index`, which
        the proxy's write endpoint doesn't take).

        `value` uses the same JSON shapes a read returns. `None`
        relinquishes `priority` on a commandable property instead of
        writing a value. `priority` (1-16) applies to commandable
        properties only; the proxy uses 16 if it's omitted.
        """
        if priority is not None and not (1 <= priority <= PRIORITY_COUNT):
            raise ValueError(f"priority must be between 1 and {PRIORITY_COUNT}, got {priority}")
        objid = ObjectIdentifier.parse(objid)
        body: dict[str, Any] = {"value": encode_value(value)}
        if priority is not None:
            body["priority"] = priority
        await self._request("PUT", self._property_path(device, objid, prop), json=body, timeout=timeout)
