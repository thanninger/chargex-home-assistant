"""Explicit local entity-registry migration; never sends Modbus commands."""

import voluptuous as vol
from homeassistant.core import SupportsResponse, callback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN


@callback
def async_register_services(hass):
    """Offer a preview and an explicit apply action for existing IDs."""

    async def prefix_entity_ids(call):
        registry = er.async_get(hass)
        own_entries = {
            entry.entry_id for entry in hass.config_entries.async_entries(DOMAIN)
        }
        proposals = []
        skipped = []
        applied = []
        for entity in list(registry.entities.values()):
            if entity.platform != DOMAIN or entity.config_entry_id not in own_entries:
                continue
            domain, object_id = entity.entity_id.split(".", 1)
            if object_id.startswith("chargex_"):
                continue
            target = f"{domain}.chargex_{object_id}"
            change = {"old": entity.entity_id, "new": target}
            if registry.async_get(target) or hass.states.get(target):
                skipped.append(change | {"reason": "target_exists"})
                continue
            proposals.append(change)
            if call.data["apply"]:
                registry.async_update_entity(entity.entity_id, new_entity_id=target)
                applied.append(change)
        return {
            "preview": not call.data["apply"],
            "changes": proposals,
            "applied": applied,
            "skipped": skipped,
        }

    hass.services.async_register(
        DOMAIN,
        "prefix_entity_ids",
        prefix_entity_ids,
        schema=vol.Schema({vol.Optional("apply", default=False): bool}),
        supports_response=SupportsResponse.ONLY,
    )
