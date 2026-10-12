"""Read-only Aqueduct register mapping, independent of Home Assistant.

Only read_holding_registers is called. No controls or write operations exist.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from modbus_connection import (
    IllegalDataAddressError,
    IllegalFunctionError,
    ModbusExceptionError,
)

from .const import MAX_MODULES


class ReadUnit(Protocol):
    async def read_holding_registers(self, address: int, count: int) -> list[int]: ...


class InvalidDevice(ValueError):
    """Response is not a supported ChargeX system."""


class InvalidResponse(ValueError):
    """Incomplete or invalid register response."""


def u32(words: list[int], offset: int = 0) -> int:
    if len(words) < offset + 2:
        raise InvalidResponse("32-bit register response too short")
    return (words[offset] << 16) | words[offset + 1]


def text(words: list[int]) -> str:
    return b"".join(w.to_bytes(2, "big") for w in words).decode("utf-8").strip(" \x00")


@dataclass(frozen=True)
class Identity:
    manufacturer: str
    model: str
    firmware: str
    serial: str


@dataclass
class Snapshot:
    system: dict[str, int | None]
    modules: list[dict[str, int]]
    received_at: datetime


class ChargeXApi:
    def __init__(self, unit: ReadUnit, system_error_address: int = 0):
        self.unit = unit
        self.system_error_address = system_error_address
        self.unsupported: set[int] = set()

    async def read(self, address: int, count: int) -> list[int]:
        async with asyncio.timeout(5):
            words = list(await self.unit.read_holding_registers(address, count))
        if len(words) != count or any(not 0 <= w <= 65535 for w in words):
            raise InvalidResponse(
                f"Invalid response at {address}: expected {count} words"
            )
        return words

    async def identity(self) -> Identity:
        words = await self.read(2, 34)
        manufacturer = text(words[2:10])
        serial = text(words[26:34])
        if u32(words) != 10 or manufacturer.casefold() != "chargex" or not serial:
            raise InvalidDevice("Unit_ID, manufacturer or serial number is not valid")
        return Identity(manufacturer, text(words[10:18]), text(words[18:26]), serial)

    async def optional(self, address: int) -> int | None:
        if address in self.unsupported:
            return None
        try:
            return u32(await self.read(address, 2))
        except IllegalDataAddressError, IllegalFunctionError:
            self.unsupported.add(address)
            return None
        except ModbusExceptionError:
            # Busy/initializing/etc.: retry next poll; never keep an old value.
            return None

    async def update(self) -> Snapshot:
        words = await self.read(36, 20)
        keys = (
            "power",
            "required_power",
            "current_1",
            "current_2",
            "current_3",
            "connected_cars",
            "active_sessions",
            "module_count",
            "status",
            "max_current",
        )
        system = {key: u32(words, i * 2) for i, key in enumerate(keys)}
        count = system["module_count"]
        if not 1 <= count <= MAX_MODULES:
            raise InvalidResponse(f"Invalid module count: {count}")
        modules = []
        for index in range(count):
            block = await self.read(100 + index * 12, 12)
            modules.append(
                {
                    key: u32(block, i * 2)
                    for i, key in enumerate(
                        (
                            "power",
                            "current_1",
                            "current_2",
                            "current_3",
                            "status",
                            "errors",
                        )
                    )
                }
            )
        for key, address in (
            ("eligible_modules", 56),
            ("meter_timestamp", 58),
            ("eligible_timestamp", 60),
            ("target_timeout", 500),
            ("default_power", 502),
            ("target_power", 504),
            ("charging_mode", 506),
        ):
            value = await self.optional(address)
            system[key] = None if key == "eligible_modules" and value == 404 else value
        system["errors"] = (
            await self.optional(self.system_error_address)
            if self.system_error_address
            else None
        )
        return Snapshot(system, modules, datetime.now(UTC))
