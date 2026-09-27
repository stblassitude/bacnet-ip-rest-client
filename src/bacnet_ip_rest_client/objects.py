"""Identifiers and metadata records for BACnet objects as the proxy
describes them."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, NamedTuple, Self

# A BACnet priority array always has exactly 16 slots (priority 1 highest,
# 16 lowest/default), regardless of object type.
PRIORITY_COUNT = 16


class ObjectIdentifier(NamedTuple):
    """A BACnetObjectIdentifier: object type and instance number.

    Like bacpypes3's ObjectIdentifier this is a 2-tuple, so `objtype,
    instance = objid` works the same way, and it parses bacpypes3's
    "analog-value,1" string form. Unlike bacpypes3, the type is kept as the
    string the proxy uses on the wire (e.g. "analog-value", or
    "object-type(N)" for a type without a name) rather than an ObjectType
    enumeration -- the client has no local object-type registry, the proxy
    does all of that.
    """

    type: str
    instance: int

    @classmethod
    def parse(cls, value: "ObjectIdentifier | tuple[str, int] | str") -> Self:
        """Accepts an ObjectIdentifier, a `(type, instance)` tuple, or a
        string "type,instance" (bacpypes3's form) or "type:instance"."""
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            for sep in (",", ":"):
                objtype, found, instance = value.rpartition(sep)
                if found:
                    break
            else:
                raise ValueError(f"not an object identifier: {value!r}")
            try:
                return cls(objtype.strip(), int(instance))
            except ValueError:
                raise ValueError(f"not an object identifier: {value!r}") from None
        if isinstance(value, tuple) and len(value) == 2:
            objtype, instance = value
            return cls(str(objtype), int(instance))
        raise TypeError(f"not an object identifier: {value!r}")

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        """From the proxy's `{"type", "instance"}` JSON shape."""
        return cls(data["type"], int(data["instance"]))

    def __str__(self) -> str:
        return f"{self.type},{self.instance}"


class Access(StrEnum):
    """The caller's effective right on an object, according to the proxy's
    authorization rules -- a proxy concept with no BACnet equivalent. It
    says nothing about whether the device will accept a write."""

    READONLY = "readonly"
    READWRITE = "readwrite"


def _access(value: str | None) -> Access | None:
    return Access(value) if value is not None else None


@dataclass(frozen=True)
class ObjectSummary:
    """One entry of `Client.list_objects()`: an object on the device with
    the metadata the proxy caches for it. There is no bacpypes3 equivalent;
    with plain BACnet this takes a walk of the device's object-list plus a
    read of each object's properties."""

    objid: ObjectIdentifier
    name: str | None = None
    description: str | None = None
    units: Any = None
    access: Access | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        return cls(
            objid=ObjectIdentifier.from_json(data),
            name=data.get("name"),
            description=data.get("description"),
            units=data.get("units"),
            access=_access(data.get("access")),
        )
