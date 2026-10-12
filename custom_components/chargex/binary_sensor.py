"""ChargeX flags as binary sensors."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import callback

from .entity import ChargeXEntity

MODULE = [
    ("charging", "Lädt gerade", 0, "battery_charging", False),
    ("authorized", "Autorisiert", 2, None, False),
    ("vehicle_connected", "Fahrzeug angeschlossen", 3, "plug", False),
    ("charge_request", "Ladeanforderung", 4, None, False),
    ("battery_full", "Battery Full", 5, None, False),
    ("has_error", "Fehler", None, "problem", True),
]
SYSTEM = [
    ("phase_split", "Phasenaufteilung unterstützt", 0, None, True),
    (
        "internet_connected",
        "ChargeX meldet Internetverbindung",
        1,
        "connectivity",
        True,
    ),
    ("non_compliant", "Nicht eichrechtskonform", 2, None, True),
    ("meter_configured", "Zählerkonfiguration bestätigt", 3, None, True),
    ("dry_contact", "Dry Contact gesetzt", 4, None, True),
    ("low_pv", "PV-Leistung zu niedrig", 5, None, False),
    ("has_error", "Systemfehler", None, "problem", True),
]


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities([ChargeXBinarySensor(coordinator, spec) for spec in SYSTEM])
    added = set()

    @callback
    def add_modules():
        entities = []
        for index in range(len(coordinator.data.modules)):
            if index not in added:
                added.add(index)
                entities.extend(
                    ChargeXBinarySensor(coordinator, spec, index) for spec in MODULE
                )
        if entities:
            async_add_entities(entities)

    add_modules()
    entry.async_on_unload(coordinator.async_add_listener(add_modules))


class ChargeXBinarySensor(ChargeXEntity, BinarySensorEntity):
    def __init__(self, coordinator, spec, index=None):
        key, name, bit, device_class, diagnostic = spec
        super().__init__(coordinator, key, name, index)
        self.entity_id = f"binary_sensor.{self.chargex_object_id}"
        self.bit = bit
        self.source_key = "errors" if bit is None else "status"
        self._attr_device_class = (
            BinarySensorDeviceClass(device_class) if device_class else None
        )
        if diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self):
        return (
            super().available
            and self.values.get(self.source_key) is not None
            and not (
                self.source_key == "errors" and self.values.get("errors") == 0xFFFFFFFF
            )
        )

    @property
    def is_on(self):
        mask = self.values.get(self.source_key)
        if mask is None:
            return None
        return bool(mask) if self.bit is None else bool(mask & (1 << self.bit))
