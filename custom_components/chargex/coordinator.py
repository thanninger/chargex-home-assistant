"""One coordinated poll per system."""

import asyncio
import logging
from datetime import timedelta

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from modbus_connection import ModbusError

from .api import ChargeXApi
from .const import DEFAULT_INTERVAL

_LOGGER = logging.getLogger(__name__)


class ChargeXCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, unit):
        super().__init__(
            hass,
            _LOGGER,
            name=entry.title,
            config_entry=entry,
            update_interval=timedelta(
                seconds=entry.options.get(
                    "scan_interval", entry.data.get("scan_interval", DEFAULT_INTERVAL)
                )
            ),
        )
        self.entry = entry
        self.unit = unit
        self.api = ChargeXApi(unit, entry.options.get("system_error_address", 0))
        self.identity = None
        self.system_device_id = None

    async def _async_setup(self):
        try:
            async with asyncio.timeout(15):
                self.identity = await self.api.identity()
            if self.identity.serial != self.entry.unique_id:
                raise ValueError(
                    "Seriennummer stimmt nicht mit eingerichtetem System überein"
                )
        except (ModbusError, OSError, ValueError, TimeoutError) as err:
            await self.unit.disconnect()
            raise UpdateFailed(f"ChargeX-Identifikation fehlgeschlagen: {err}") from err

    async def _async_update_data(self):
        try:
            async with asyncio.timeout(45):
                return await self.api.update()
        except (ModbusError, OSError, ValueError, TimeoutError) as err:
            # A socket that remains open but no longer answers is recycled too.
            await self.unit.disconnect()
            raise UpdateFailed(f"ChargeX-Abfrage fehlgeschlagen: {err}") from err
