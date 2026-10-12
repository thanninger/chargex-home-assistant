"""ChargeX Aqueduct read-only integration."""

from homeassistant.components.modbus import async_get_unit
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from modbus_connection import ModbusTcpParams

from .const import DEFAULT_PORT, DEFAULT_UNIT_ID, DOMAIN, PLATFORMS
from .coordinator import ChargeXCoordinator
from .services import async_register_services


async def async_setup(hass, config):
    async_register_services(hass)
    return True


async def async_setup_entry(hass, entry):
    unit = async_get_unit(
        hass,
        entry,
        ModbusTcpParams(
            host=entry.data["host"], port=entry.data.get("port", DEFAULT_PORT)
        ),
        entry.data.get("unit_id", DEFAULT_UNIT_ID),
    )
    unit.set_message_spacing(0.05)
    coordinator = ChargeXCoordinator(hass, entry, unit)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ValueError as err:
        raise ConfigEntryNotReady(
            f"ChargeX-Geräteidentifikation fehlgeschlagen: {err}"
        ) from err
    identity = coordinator.identity
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, identity.serial)},
        manufacturer=identity.manufacturer,
        model=identity.model,
        sw_version=identity.firmware,
        serial_number=identity.serial,
        name=entry.title,
    )
    coordinator.system_device_id = device.id
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)
