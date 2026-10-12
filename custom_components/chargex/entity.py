"""Shared device identity, naming and availability."""

from datetime import UTC, datetime

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import DEFAULT_STALE_SECONDS, DOMAIN


class ChargeXEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key, name, index=None):
        super().__init__(coordinator)
        self.key = key
        self.index = index
        identity = coordinator.identity
        root = identity.serial
        device_id = root if index is None else f"{root}:module:{index}"
        self._attr_unique_id = f"{device_id}:{key}"
        self._attr_name = name
        entry = coordinator.entry
        names = entry.options.get("point_names", entry.data.get("point_names", ""))
        names = [n.strip() for n in names.split(",")]
        device_name = (
            entry.title
            if index is None
            else (
                names[index]
                if index < len(names) and names[index]
                else f"{entry.title} Ladepunkt {index + 1}"
            )
        )
        self.chargex_object_id = slugify(f"{DOMAIN} {device_name} {name}")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=device_name,
            manufacturer=identity.manufacturer,
            model=identity.model,
            sw_version=identity.firmware,
        )
        if index is None:
            self._attr_device_info["serial_number"] = identity.serial
        elif coordinator.system_device_id:
            self._attr_device_info["via_device_id"] = coordinator.system_device_id

    @property
    def values(self):
        if self.index is None:
            return self.coordinator.data.system
        if self.index >= len(self.coordinator.data.modules):
            return {}
        return self.coordinator.data.modules[self.index]

    @property
    def available(self):
        return super().available and (
            self.index is None or self.index < len(self.coordinator.data.modules)
        )

    @property
    def meter_stale(self):
        timestamp = self.coordinator.data.system.get("meter_timestamp")
        if not timestamp:
            return False
        limit = self.coordinator.entry.options.get(
            "stale_seconds", DEFAULT_STALE_SECONDS
        )
        age = datetime.now(UTC).timestamp() - timestamp
        return age > limit or age < -60

    @property
    def extra_state_attributes(self):
        attributes = {}
        if self.index is not None:
            attributes["module_index"] = self.index
        return attributes
