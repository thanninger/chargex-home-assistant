"""UI setup and read-only options."""

import asyncio
import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.modbus import async_get_temporary_unit
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.exceptions import HomeAssistantError
from modbus_connection import ModbusError, ModbusTcpParams

from .api import ChargeXApi, InvalidDevice, InvalidResponse
from .const import (
    DEFAULT_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_STALE_SECONDS,
    DEFAULT_UNIT_ID,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def user_schema(values):
    return vol.Schema(
        {
            vol.Required("name", default=values.get("name", "ChargeX")): str,
            vol.Required("host", default=values.get("host", "")): str,
            vol.Required("port", default=values.get("port", DEFAULT_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Required(
                "unit_id", default=values.get("unit_id", DEFAULT_UNIT_ID)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
            vol.Required(
                "scan_interval", default=values.get("scan_interval", DEFAULT_INTERVAL)
            ): vol.All(vol.Coerce(int), vol.Range(min=10, max=3600)),
            vol.Optional("point_names", default=values.get("point_names", "")): str,
        }
    )


class ChargeXConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            user_input = dict(user_input)
            user_input["host"] = user_input["host"].strip()
            user_input["name"] = user_input["name"].strip()
            if not user_input["host"] or not user_input["name"]:
                errors["base"] = "invalid_input"
            else:
                self._async_abort_entries_match(
                    {
                        "host": user_input["host"],
                        "port": user_input["port"],
                        "unit_id": user_input["unit_id"],
                    }
                )
                try:
                    async with asyncio.timeout(45):
                        async with async_get_temporary_unit(
                            self.hass,
                            ModbusTcpParams(
                                host=user_input["host"], port=user_input["port"]
                            ),
                            user_input["unit_id"],
                        ) as unit:
                            unit.set_message_spacing(0.05)
                            api = ChargeXApi(unit)
                            identity = await api.identity()
                            count = await api.read(50, 2)
                            count = (count[0] << 16) | count[1]
                            if not 1 <= count <= 15:
                                raise InvalidResponse("Invalid module count")
                    names = user_input.get("point_names", "").strip()
                    if names and (
                        len(names.split(",")) != count
                        or any(not n.strip() for n in names.split(","))
                    ):
                        errors["point_names"] = "wrong_point_names"
                    else:
                        await self.async_set_unique_id(identity.serial)
                        self._abort_if_unique_id_configured()
                        user_input["point_names"] = names
                        return self.async_create_entry(
                            title=user_input["name"], data=user_input
                        )
                except AbortFlow:
                    raise
                except InvalidDevice:
                    errors["base"] = "invalid_device"
                except ModbusError, OSError, TimeoutError, HomeAssistantError:
                    errors["base"] = "cannot_connect"
                except ValueError, UnicodeError:
                    errors["base"] = "invalid_response"
                except Exception:
                    _LOGGER.exception("Unexpected ChargeX setup error")
                    errors["base"] = "unknown"
        return self.async_show_form(
            step_id="user", data_schema=user_schema(user_input or {}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ChargeXOptionsFlow()


class ChargeXOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            names = user_input["point_names"].strip()
            coordinator = getattr(self.config_entry, "runtime_data", None)
            count = (
                len(coordinator.data.modules)
                if coordinator and coordinator.data
                else None
            )
            if names and (
                any(not n.strip() for n in names.split(","))
                or (count and len(names.split(",")) != count)
            ):
                errors["point_names"] = "wrong_point_names"
            else:
                return self.async_create_entry(title="", data=user_input)
        values = dict(self.config_entry.data) | dict(self.config_entry.options)
        schema = vol.Schema(
            {
                vol.Required(
                    "scan_interval",
                    default=values.get("scan_interval", DEFAULT_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=10, max=3600)),
                vol.Required("point_names", default=values.get("point_names", "")): str,
                vol.Required(
                    "stale_seconds",
                    default=values.get("stale_seconds", DEFAULT_STALE_SECONDS),
                ): vol.All(vol.Coerce(int), vol.Range(min=60, max=3600)),
                vol.Required(
                    "system_error_address",
                    default=values.get("system_error_address", 0),
                ): vol.In([0, 62, 63]),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
