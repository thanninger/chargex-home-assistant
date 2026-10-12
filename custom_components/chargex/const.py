"""ChargeX constants."""

DOMAIN = "chargex"
DEFAULT_PORT = 1502
DEFAULT_UNIT_ID = 10
DEFAULT_INTERVAL = 15
DEFAULT_STALE_SECONDS = 180
MAX_MODULES = 15
PLATFORMS = ["sensor", "binary_sensor"]
MODE_NAMES = {0: "full_control", 1: "minimum_charge", 2: "no_reduction"}
MODULE_ERRORS = {
    0: "Übertemperatur",
    1: "Verriegelungsfehler",
    2: "Relais schließt nicht",
    3: "Relais öffnet nicht",
    4: "Diodentest fehlgeschlagen",
    5: "CP-Kurzschluss",
    16: "Zählerkommunikation gestört",
}
