# bacnet-ip-rest-client

An async Python client for the
[BACnet/IP REST proxy](https://github.com/stblassitude/bacnet-ip-rest-proxy).
It is built on [httpx](https://www.python-httpx.org/).

```python
from bacnet_ip_rest_client import Client, ProxyError

async with Client("https://bacnet-proxy.example.net", token) as client:
    for obj in await client.list_objects("my-device"):
        print(obj.objid, obj.name, obj.access)

    temp = await client.read_property("my-device", "analog-input,1", "present-value")
    slots = await client.read_property("my-device", "analog-value,5", "priority-array")
    await client.write_property("my-device", "analog-value,5", "present-value", 21.5, priority=10)
    await client.write_property("my-device", "analog-value,5", "present-value", None, priority=10)  # relinquish
```

## Authentication

Every request goes through the proxy's
[authentication and authorization](https://stblassitude.github.io/bacnet-ip-rest-proxy/configuration/#authentication).
Pass `token` to `Client` to send it as `Authorization: Bearer <token>`. The
token can be one of the opaque tokens configured on the proxy, or a JWT it
accepts, such as a user's OIDC access token. Without a token, the proxy
only allows a request if its rules grant access without one, for example
by the client's IP address. A request the proxy denies raises
`UnauthorizedError` or `ForbiddenError`.

## Relationship to bacpypes3

`Client` has the same role as a bacpypes3 `Application`, and
`read_property()` and `write_property()` take bacpypes3's arguments, with
some exceptions. It doesn't try to hide the ways an HTTP proxy works
differently from a BACnet/IP stack:

| | bacpypes3 | this client |
|---|---|---|
| Addressing a device | BACnet `Address` | the proxy's device id: a configured alias, or a hostname/IP[:port] |
| Object identifiers | `ObjectIdentifier` with an `ObjectType` enum | `ObjectIdentifier(type: str, instance: int)`, also a 2-tuple; parses `"analog-value,1"` |
| Values | decoded into the property's BACnet datatype | JSON as the proxy sends it: enumerations as numbers, bit strings as lists of bools. REAL properties of analog objects are always `float`, including NaN/±Infinity |
| Errors | `ErrorPDU`/`RejectPDU`/`AbortPDU`, which derive from `BaseException`, and are sometimes returned instead of raised | always raised, all derived from `ProxyError(Exception)`, see below |
| Object discovery | read `object-list`, then each object's properties | `list_objects()`: the proxy's cached catalog with names, units and access, in one request |
| ReadPropertyMultiple | one request, one transaction | none. `read_properties()` sends concurrent single reads, each with its own result |
| WriteProperty with array index | supported | not supported by the proxy |
| Authorization | none | a bearer token per `Client`. The proxy's rules decide what it may read and write (`ObjectSummary.access`) |

## Errors

| Exception | Cause | Retried |
|---|---|---|
| `CommunicationError` | proxy unreachable, or a reply that couldn't be decoded | yes |
| `BadRequestError` | 400, malformed request | no |
| `UnauthorizedError` | 401, token not accepted | no |
| `ForbiddenError` | 403, the proxy's authorization denies the request | no |
| `NotFoundError` | 404, unknown device, object or property | no |
| `ValueRejectedError` | 422, the device rejected the written value | no |
| `BACnetError` | 502, BACnet Error/Reject/Abort from the device; `error_class`/`error_code` hold its numeric Error_Class/Error_Code | yes, except unknown-object/unknown-property |
| `BACnetTimeoutError` | 504, the device didn't answer | yes |

`ProxyStatusError.is_unknown_object_or_property` is true when the proxy
reports a missing object or property, whether it does so as a 404 or as a
502 from the device. Code that probes for optional properties, such as
`priority-array`, can check this one attribute.

Retries use exponential backoff as configured by `RetryPolicy` (default:
5 attempts, 1s doubling to at most 8s). Retry warnings are logged to the
`bacnet_ip_rest_client` logger. Writes are retried too, since writing the
same value at the same priority again has no further effect.

`concurrency` (default 10) caps the number of requests one `Client` has in
flight. The proxy serializes traffic to the device itself; this cap only
keeps a large `read_properties()` from opening hundreds of connections to
the proxy.

## Development

```sh
uv run pytest
```
