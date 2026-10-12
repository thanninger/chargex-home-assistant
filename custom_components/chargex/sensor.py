"""Read-only numeric and status sensors."""

from datetime import UTC, datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import callback

from .const import MODE_NAMES, MODULE_ERRORS
from .entity import ChargeXEntity

# key, name, device class, unit, diagnostic
COMMON = [
    ("power", "Ladeleistung", "power", "W", False),
    *[(f"current_{i}", f"Strom Phase {i}", "current", "A", False) for i in (1, 2, 3)],
    ("status", "Statusbits", None, None, True),
    ("errors", "Fehlerbits", None, None, True),
]
SYSTEM = COMMON + [
    ("required_power", "Mindestleistungsanforderung", "power", "W", True),
    ("connected_cars", "Angeschlossene Fahrzeuge", None, None, False),
    ("active_sessions", "Aktive Ladevorgänge", None, None, False),
    ("module_count", "Ladepunkte", None, None, True),
    ("max_current", "Maximalstrom", "current", "A", True),
    ("eligible_modules", "Ladeberechtigte Module", None, None, False),
    ("meter_timestamp", "Zähler aktualisiert", "timestamp", None, True),
    ("eligible_timestamp", "Ladeberechtigung aktualisiert", "timestamp", None, True),
    ("target_timeout", "Zielleistung Timeout", "duration", "s", True),
    ("default_power", "Rückfallleistung", "power", "W", True),
    ("target_power", "Zielleistung", "power", "W", True),
    ("charging_mode", "Charging Mode", "enum", None, False),
]
MODULE = COMMON + [
    ("phase_mode", "Phasenmodus", "enum", None, False),
    ("error_description", "Fehlerbeschreibung", None, None, True),
]


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities([ChargeXSensor(coordinator, spec) for spec in SYSTEM])
    added = set()

    @callback
    def add_modules():
        entities = []
        for index in range(len(coordinator.data.modules)):
            if index not in added:
                added.add(index)
                entities.extend(
                    ChargeXSensor(coordinator, spec, index) for spec in MODULE
                )
        if entities:
            async_add_entities(entities)

    add_modules()
    entry.async_on_unload(coordinator.async_add_listener(add_modules))


class ChargeXSensor(ChargeXEntity, SensorEntity):
    def __init__(self, coordinator, spec, index=None):
        key, name, device_class, unit, diagnostic = spec
        super().__init__(coordinator, key, name, index)
        self.entity_id = f"sensor.{self.chargex_object_id}"
        self._attr_device_class = (
            SensorDeviceClass(device_class) if device_class else None
        )
        self._attr_native_unit_of_measurement = unit
        if unit:
            self._attr_state_class = SensorStateClass.MEASUREMENT
        if diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if key in ("phase_mode", "charging_mode"):
            self._attr_translation_key = key
        if key == "phase_mode":
            self._attr_options = ["single_phase", "three_phase"]
        elif key == "charging_mode":
            self._attr_options = list(MODE_NAMES.values())
        self._attr_suggested_display_precision = (
            3 if unit == "A" else 0 if unit else None
        )

    @property
    def available(self):
        if not super().available:
            return False
        if self.key in ("phase_mode", "error_description"):
            return True
        if self.values.get(self.key) is None:
            return False
        if self.key == "power" or self.key.startswith("current_"):
            return not self.meter_stale
        return True

    @property
    def native_value(self):
        if self.key == "phase_mode":
            status = self.values.get("status")
            return (
                None
                if status is None
                else "three_phase"
                if status & 2
                else "single_phase"
            )
        if self.key == "error_description":
            mask = self.values.get("errors")
            if mask is None:
                return None
            if mask == 0xFFFFFFFF:
                return "Unbestätigter Fehlerwert (0xFFFFFFFF)"
            labels = [name for bit, name in MODULE_ERRORS.items() if mask & (1 << bit)]
            known = sum(1 << bit for bit in MODULE_ERRORS)
            if mask & ~known:
                labels.append(f"Unbekannte Bits: 0x{mask & ~known:08X}")
            return "; ".join(labels)[:250] if labels else "Kein Fehler"
        value = self.values.get(self.key)
        if value is None:
            return None
        if self.key == "charging_mode":
            return MODE_NAMES.get(value)
        if self.key.endswith("timestamp"):
            return datetime.fromtimestamp(value, UTC) if value else None
        if self.native_unit_of_measurement == "A":
            return value / 1000
        return value

    @property
    def extra_state_attributes(self):
        result = super().extra_state_attributes
        if self.key in ("status", "errors"):
            mask = self.values.get(self.key)
            if mask is not None:
                result["hex"] = f"0x{mask:08X}"
                result["set_bits"] = [i for i in range(32) if mask & (1 << i)]
        if self.key == "charging_mode":
            result["raw_value"] = self.values.get(self.key)
        if self.key == "power" or self.key.startswith("current_"):
            result["meter_timestamp"] = self.coordinator.data.system.get(
                "meter_timestamp"
            )
            result["stale_check_supported"] = bool(result["meter_timestamp"])
        return result
