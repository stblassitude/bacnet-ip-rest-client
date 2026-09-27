"""Translation between the proxy's JSON property values and Python.

bacpypes3 decodes a property value into its declared BACnet datatype
because it knows each object type's property definitions. This client
doesn't: the proxy sends plain JSON (enumerations as their number, Real as
a JSON number or one of the strings "NaN"/"Infinity"/"-Infinity"), so
values come back as decoded JSON. The exception is a small set of
properties whose datatype is certain -- the REAL-valued properties of
analog objects -- which are always returned as float. Without that, a
whole-number REAL arrives as int (the proxy writes 73.0 as `73`), and NaN
arrives as a string.
"""

import math
from typing import Any

ANALOG_OBJECT_TYPES = frozenset({
    "analog-input", "analog-output", "analog-value", "large-analog-value",
})

# REAL (or, for large-analog-value, Double) properties of the analog object
# types. priority-array is an array of them (or null for a relinquished
# slot).
REAL_PROPERTIES = frozenset({
    "present-value", "relinquish-default", "priority-array",
    "cov-increment", "high-limit", "low-limit", "deadband",
    "min-pres-value", "max-pres-value", "resolution",
})

_SPECIAL_REALS = {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf}


def _to_float(raw: Any) -> Any:
    if isinstance(raw, str) and raw in _SPECIAL_REALS:
        return _SPECIAL_REALS[raw]
    if isinstance(raw, int) and not isinstance(raw, bool):
        return float(raw)
    return raw


def decode_value(object_type: str, prop: str, raw: Any, array_index: int | None = None) -> Any:
    """Decode a property value as received from the proxy."""
    if object_type not in ANALOG_OBJECT_TYPES or prop not in REAL_PROPERTIES:
        return raw
    if prop == "priority-array" and array_index is None:
        return [_to_float(slot) for slot in raw] if isinstance(raw, list) else raw
    if array_index == 0:
        # Index 0 of an array property is its length, not an element.
        return raw
    return _to_float(raw)


def encode_value(value: Any) -> Any:
    """Encode a value for a write. A non-finite float becomes the string
    the proxy expects, since JSON has no NaN or Infinity."""
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"
    return value
