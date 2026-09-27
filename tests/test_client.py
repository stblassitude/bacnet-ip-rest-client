import asyncio
import json
import math

import httpx
import pytest

from bacnet_ip_rest_client import (
    Access,
    BACnetError,
    Client,
    CommunicationError,
    ForbiddenError,
    NotFoundError,
    ObjectIdentifier,
    RetryPolicy,
    UnauthorizedError,
)

NO_WAIT = RetryPolicy(attempts=3, base_delay=0, max_delay=0)


def make_client(handler, **kwargs) -> Client:
    kwargs.setdefault("retry", NO_WAIT)
    return Client("https://proxy.example", "secret", transport=httpx.MockTransport(handler), **kwargs)


def run(coro):
    return asyncio.run(coro)


def test_object_identifier_parse():
    assert ObjectIdentifier.parse("analog-value,1") == ("analog-value", 1)
    assert ObjectIdentifier.parse("analog-value:1") == ObjectIdentifier("analog-value", 1)
    assert ObjectIdentifier.parse(("binary-output", "7")) == ("binary-output", 7)
    assert ObjectIdentifier.parse("object-type(130),2").type == "object-type(130)"
    assert str(ObjectIdentifier("analog-input", 3)) == "analog-input,3"
    with pytest.raises(ValueError):
        ObjectIdentifier.parse("analog-input")


def test_list_objects_and_auth_header():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["path"] = request.url.path
        return httpx.Response(200, json=[
            {"type": "analog-input", "instance": 1, "name": "A", "units": "degrees-celsius", "access": "readonly"},
            {"type": "binary-value", "instance": 2, "name": "B", "access": "readwrite"},
        ])

    async def go():
        async with make_client(handler) as client:
            return await client.list_objects("dev")

    objects = run(go())
    assert seen == {"auth": "Bearer secret", "path": "/api/v1/devices/dev/objects"}
    assert objects[0].objid == ("analog-input", 1)
    assert objects[0].units == "degrees-celsius"
    assert objects[1].access is Access.READWRITE


def test_read_property_decodes_analog_values():
    values = {
        "/api/v1/devices/dev/objects/analog-value/1/present-value": 73,
        "/api/v1/devices/dev/objects/analog-input/12/present-value": "NaN",
        "/api/v1/devices/dev/objects/analog-value/1/priority-array": [None, 5, "Infinity"] + [None] * 13,
        "/api/v1/devices/dev/objects/binary-value/2/present-value": 1,
        "/api/v1/devices/dev/objects/analog-value/1/description": "NaN",
    }

    def handler(request):
        if request.url.params.get("index") == "0":
            return httpx.Response(200, json={"value": 16})
        return httpx.Response(200, json={"value": values[request.url.path]})

    async def go():
        async with make_client(handler) as client:
            return [
                await client.read_property("dev", "analog-value,1", "present-value"),
                await client.read_property("dev", "analog-input,12", "present-value"),
                await client.read_property("dev", "analog-value,1", "priority-array"),
                await client.read_property("dev", "analog-value,1", "priority-array", 0),
                await client.read_property("dev", "binary-value,2", "present-value"),
                await client.read_property("dev", "analog-value,1", "description"),
            ]

    pv, nan, pa, length, binary, description = run(go())
    assert pv == 73.0 and isinstance(pv, float)
    assert math.isnan(nan)
    assert pa[:3] == [None, 5.0, math.inf] and isinstance(pa[1], float)
    assert length == 16 and isinstance(length, int)
    assert binary == 1
    assert description == "NaN"


def test_retries_transient_errors():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(502, json={"error": "abort", "bacnetErrorClass": "7", "bacnetErrorCode": "30"})
        if len(calls) == 2:
            return httpx.Response(200, content=b"")
        return httpx.Response(200, json={"value": 1.5})

    async def go():
        async with make_client(handler) as client:
            return await client.read_property("dev", "analog-value,1", "present-value")

    assert run(go()) == 1.5
    assert len(calls) == 3


def test_retries_exhausted_raises_last_error():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(502, json={"error": "abort", "bacnetErrorClass": "7", "bacnetErrorCode": "30"})

    async def go():
        async with make_client(handler) as client:
            await client.read_property("dev", "analog-value,1", "present-value")

    with pytest.raises(BACnetError) as info:
        run(go())
    assert info.value.error_class == 7 and info.value.error_code == 30
    assert len(calls) == NO_WAIT.attempts


@pytest.mark.parametrize("status, body, expected", [
    (403, {"error": "denied"}, ForbiddenError),
    (401, {"error": "bad token"}, UnauthorizedError),
    (404, {"error": "no such property"}, NotFoundError),
    (502, {"error": "unknown-property", "bacnetErrorClass": "2", "bacnetErrorCode": "32"}, BACnetError),
])
def test_permanent_errors_are_not_retried(status, body, expected):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(status, json=body)

    async def go():
        async with make_client(handler) as client:
            await client.read_property("dev", "analog-output,1", "priority-array")

    with pytest.raises(expected) as info:
        run(go())
    assert len(calls) == 1
    assert info.value.message == body["error"]
    assert info.value.is_unknown_object_or_property == (status in (404, 502))


def test_transport_error_becomes_communication_error():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    async def go():
        async with make_client(handler) as client:
            await client.list_devices()

    with pytest.raises(CommunicationError) as info:
        run(go())
    assert isinstance(info.value.__cause__, httpx.ConnectError)


def test_read_properties_reports_each_outcome():
    def handler(request):
        if "/analog-input/2/" in request.url.path:
            return httpx.Response(403, json={"error": "denied"})
        return httpx.Response(200, json={"value": 20})

    async def go():
        async with make_client(handler) as client:
            return await client.read_properties("dev", [
                ("analog-input,1", "present-value"),
                (ObjectIdentifier("analog-input", 2), "present-value"),
                ("analog-value,3", "priority-array", 4),
            ])

    first, second, third = run(go())
    assert first == 20.0
    assert isinstance(second, ForbiddenError)
    assert third == 20.0


def test_read_properties_bounds_concurrency():
    in_flight = 0
    peak = 0

    async def handler(request):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return httpx.Response(200, json={"value": 1})

    async def go():
        async with make_client(handler, concurrency=3) as client:
            return await client.read_properties("dev", [(("analog-input", i), "present-value") for i in range(20)])

    assert len(run(go())) == 20
    assert peak == 3


def test_write_property():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(204)

    async def go():
        async with make_client(handler) as client:
            await client.write_property("dev", "binary-value,2", "present-value", 1, priority=10)
            await client.write_property("dev", "binary-value,2", "present-value", None, priority=10)
            await client.write_property("dev", "analog-value,1", "present-value", math.nan)
            with pytest.raises(ValueError):
                await client.write_property("dev", "analog-value,1", "present-value", 1.0, priority=17)

    run(go())
    path = "/api/v1/devices/dev/objects/binary-value/2/present-value"
    assert seen == [
        ("PUT", path, {"value": 1, "priority": 10}),
        ("PUT", path, {"value": None, "priority": 10}),
        ("PUT", "/api/v1/devices/dev/objects/analog-value/1/present-value", {"value": "NaN"}),
    ]


def test_non_json_error_body():
    def handler(request):
        return httpx.Response(403, text="<html>nope</html>")

    async def go():
        async with make_client(handler) as client:
            await client.list_objects("dev")

    with pytest.raises(ForbiddenError) as info:
        run(go())
    assert info.value.message == "<html>nope</html>"
    assert info.value.error_code is None
