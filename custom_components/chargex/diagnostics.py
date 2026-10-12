"""Redacted diagnostic snapshot; performs no extra device requests."""

from dataclasses import asdict

from homeassistant.components.diagnostics import async_redact_data


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = getattr(entry, "runtime_data", None)
    snapshot = asdict(coordinator.data) if coordinator and coordinator.data else None
    if snapshot:
        snapshot["received_at"] = snapshot["received_at"].isoformat()
    return {
        "configuration": async_redact_data(
            dict(entry.data) | dict(entry.options), {"host", "name", "point_names"}
        ),
        "identity": async_redact_data(asdict(coordinator.identity), {"serial"})
        if coordinator and coordinator.identity
        else None,
        "last_update_success": coordinator.last_update_success
        if coordinator
        else False,
        "unsupported_optional_addresses": sorted(coordinator.api.unsupported)
        if coordinator
        else [],
        "snapshot": snapshot,
        "system_error_note": "Address and bit assignments conflict in vendor PDF. Disabled by default; raw bits only.",
    }
