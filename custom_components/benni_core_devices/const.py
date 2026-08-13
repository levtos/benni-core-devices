"""Constants for the standalone Benni Core Devices integration.

This is the conservative standalone extraction of the Toolbox module
``benni_core_devices``. The HA-free rule constants, enums, config keys and
attribute contracts are intentionally kept aligned with the source module.
"""

from __future__ import annotations

from enum import Enum
from typing import Final

DOMAIN: Final = "benni_core_devices"
NAME: Final = "Benni Core Devices"
STORAGE_VERSION: Final[int] = 1

# hass.data flags / entry buckets
DATA_COORDINATORS: Final = "coordinators"
DATA_COMBINEDS: Final = "combined_coordinators"
DATA_MASTERS: Final = "master_coordinators"
DATA_WS_REGISTERED: Final = "_ws_registered"
DATA_VIEW_STATIC: Final = "_view_static_registered"
DATA_VIEW_PANEL: Final = "_view_panel_registered"


class AtomicClass(str, Enum):
    """Rollenbasierte Geräteklassen (v2). Ersetzt das alte ``device_type``.

    Die Unterart innerhalb einer Klasse heißt ``variant`` (NIEMALS ``profile`` —
    ``profile`` ist die Route benni/eltern).
    """

    MEDIA_DEVICE = "media_device"
    AUDIO_ENDPOINT = "audio_endpoint"
    CONSOLE_DEVICE = "console_device"
    POWER_DEVICE = "power_device"
    OPENING = "opening"
    ENVIRONMENT = "environment"
    LIGHT = "light"
    COVER = "cover"
    CLIMATE_DEVICE = "climate_device"
    GENERIC_EXPERT = "generic_expert"
    # Nur vorbereitet (Beta/Expert, keine Tiefe):
    PRESENCE_PERSON = "presence_person"
    MOBILE_DASHBOARD = "mobile_dashboard"
    NETWORK_SERVICE = "network_service"


ATOMIC_CLASS_SLUGS: Final[tuple[str, ...]] = tuple(c.value for c in AtomicClass)

# Power-Modelle (steuern den Compute-Pfad im Coordinator/logic).
POWER_MODEL_INTEGRATION_WATT_STICKY: Final[str] = "integration_watt_sticky"
# Watt-primär: die reale Leistung (power_meter) entscheidet über powered/active;
# der Integrations-Schalter (Plug an/aus) ist nur Fallback, wenn der Meter
# keinen frischen Wert hat. Ein Halte-Fenster (sticky_hold_seconds) überbrückt
# kurze Null-Watt-Phasen mitten im Gerätezyklus (Einweichen/Pause). Für Plugs
# mit Energy-Meter, wo „Plug bestromt" ≠ „Gerät läuft".
POWER_MODEL_WATT_PRIMARY_STICKY: Final[str] = "watt_primary_sticky"
POWER_MODEL_PASSTHROUGH: Final[str] = "passthrough_state"
POWER_MODEL_NUMERIC: Final[str] = "numeric"

# Fail-Safe-Modi (greifen nur, wenn keine Quelle frisch ist).
FAIL_SAFE_OFF: Final[str] = "off"
FAIL_SAFE_OPEN: Final[str] = "open"
FAIL_SAFE_HOLD_LAST: Final[str] = "hold_last"
FAIL_SAFE_UNKNOWN: Final[str] = "unknown"
FAIL_SAFE_CHOICES: Final[tuple[str, ...]] = (
    FAIL_SAFE_OFF,
    FAIL_SAFE_OPEN,
    FAIL_SAFE_HOLD_LAST,
    FAIL_SAFE_UNKNOWN,
)

# Availability-Regeln.
AVAILABILITY_ANY_REQUIRED_OR_ANY_SOURCE: Final[str] = "any_required_or_any_source"
DEFAULT_AVAILABILITY_RULE: Final[str] = AVAILABILITY_ANY_REQUIRED_OR_ANY_SOURCE

# Config / options keys (v2)
CONF_ATOMIC_CLASS: Final[str] = "atomic_class"
CONF_VARIANT: Final[str] = "variant"
CONF_CONTROLS: Final[str] = "controls"
CONF_METADATA_SOURCES: Final[str] = "metadata_sources"
CONF_DIAGNOSTICS: Final[str] = "diagnostics"
CONF_FAIL_SAFE: Final[str] = "fail_safe"
CONF_AVAILABILITY_RULE: Final[str] = "availability_rule"
CONF_REQUIRED: Final[str] = "required"
CONF_VALUE: Final[str] = "value"

CONF_SLUG: Final[str] = "slug"
CONF_DISPLAY_NAME: Final[str] = "display_name"
CONF_PROFILE: Final[str] = "profile"

# Legacy (v1) — nur noch als Konstanten erhalten, nicht mehr aktiv genutzt.
# Verhindert Import-Brüche; das v2-Modell arbeitet rollenbasiert.
CONF_DEVICE_TYPE: Final[str] = "device_type"

PROFILE_BENNI: Final = "benni"
PROFILE_ELTERN: Final = "eltern"
PROFILES: Final = [PROFILE_BENNI, PROFILE_ELTERN]
DEFAULT_PROFILE: Final = PROFILE_BENNI
PROFILE_LABELS: Final = {PROFILE_BENNI: "Benni", PROFILE_ELTERN: "Eltern"}

CONF_INTEGRATION_ENTITY: Final[str] = "integration_entity"
CONF_POWER_ENTITY: Final[str] = "power_entity"
CONF_STATUS_ENTITY: Final[str] = "status_entity"
CONF_TITLE_ENTITY: Final[str] = "title_entity"
CONF_WATT_SENSOR: Final[str] = "watt_sensor"
CONF_WIFI_SENSOR: Final[str] = "wifi_sensor"
CONF_SWITCH_ENTITY: Final[str] = "switch_entity"
CONF_LIGHT_ENTITY: Final[str] = "light_entity"
CONF_COVER_ENTITY: Final[str] = "cover_entity"
CONF_POSITION_ENTITY: Final[str] = "position_entity"
CONF_CLIMATE_ENTITY: Final[str] = "climate_entity"
CONF_VALUE_ENTITY: Final[str] = "value_entity"

# Rich-Atomic-Rework: zusätzliche Roh-Mess- und Capability-Slots. Alle additiv
# und optional — bestehende Geräte ohne diese Keys bleiben unverändert gültig.
CONF_CURRENT_SENSOR: Final[str] = "current_sensor"
CONF_VOLTAGE_SENSOR: Final[str] = "voltage_sensor"
CONF_ENERGY_SENSOR: Final[str] = "energy_sensor"
CONF_NETWORK_SWITCH_ENTITY: Final[str] = "network_switch_entity"
CONF_WAKE_BUTTON_ENTITY: Final[str] = "wake_button_entity"
CONF_REMOTE_ENTITY: Final[str] = "remote_entity"
CONF_COMPANION_MEDIA_PLAYER: Final[str] = "companion_media_player"
CONF_COMPANION_TRACKER: Final[str] = "companion_tracker"
# Text-Feld (kein Entity-Slot): MAC für Wake-on-LAN.
CONF_WAKE_MAC: Final[str] = "wake_mac"

CONF_WATT_THRESHOLD_ON: Final[str] = "watt_threshold_on"
CONF_WATT_BUCKETS: Final[str] = "watt_buckets"
CONF_STICKY_HOLD_SECONDS: Final[str] = "sticky_hold_seconds"
CONF_EXPOSE_SECONDARY_SENSORS: Final[str] = "expose_secondary_sensors"
CONF_BULK_YAML: Final[str] = "bulk_yaml"
CONF_DEVICES: Final[str] = "devices"
CONF_LIGHT_GROUPS: Final[str] = "light_groups"
CONF_GROUP_MEMBERS: Final[str] = "members"
CONF_FIELDS: Final[str] = "fields"

# Combined Builder v0 — eigene Options-Sektion, additiv neben devices/groups.
CONF_COMBINEDS: Final[str] = "combineds"
CONF_MASTERS: Final[str] = "masters"
CONF_OUTPUT_TYPE: Final[str] = "output_type"
CONF_SOURCES: Final[str] = "sources"
CONF_RULES: Final[str] = "rules"
CONF_DEFAULT_OUTPUT: Final[str] = "default_output"
CONF_DEFAULT_REASON: Final[str] = "default_reason"
CONF_CODE_LEGEND: Final[str] = "code_legend"
CONF_DERIVED: Final[str] = "derived"
CONF_ROLE: Final[str] = "role"
CONF_ENTITY: Final[str] = "entity"
CONF_ATTRIBUTE: Final[str] = "attribute"

# Combined Output-Typen.
OUTPUT_TYPE_ENUM: Final[str] = "enum"
OUTPUT_TYPE_CODE: Final[str] = "code"
OUTPUT_TYPE_BOOLEAN: Final[str] = "boolean"
OUTPUT_TYPE_NUMBER: Final[str] = "number"
OUTPUT_TYPE_CHOICES: Final[tuple[str, ...]] = (
    OUTPUT_TYPE_ENUM,
    OUTPUT_TYPE_CODE,
    OUTPUT_TYPE_BOOLEAN,
    OUTPUT_TYPE_NUMBER,
)

# Combined Bedingungs-Operatoren (v0, überschaubar).
COMBINED_OP_EQ: Final[str] = "eq"
COMBINED_OP_NE: Final[str] = "ne"
COMBINED_OP_UNAVAILABLE: Final[str] = "unavailable"
COMBINED_OP_LT: Final[str] = "lt"
COMBINED_OP_LE: Final[str] = "le"
COMBINED_OP_GT: Final[str] = "gt"
COMBINED_OP_GE: Final[str] = "ge"
COMBINED_OPERATOR_CHOICES: Final[tuple[str, ...]] = (
    COMBINED_OP_EQ,
    COMBINED_OP_NE,
    COMBINED_OP_UNAVAILABLE,
    COMBINED_OP_LT,
    COMBINED_OP_LE,
    COMBINED_OP_GT,
    COMBINED_OP_GE,
)

# Beispielrollen für Combined-Quellen (frei erweiterbar, nur UX-Vorauswahl).
COMBINED_ROLE_CHOICES: Final[tuple[str, ...]] = (
    "open_contact",
    "tilt_contact",
    "media_state",
    "temperature",
    "humidity",
    "power_state",
    "custom",
)

CONF_WATT_OFF_OP: Final[str] = "watt_off_op"
CONF_WATT_OFF_VALUE: Final[str] = "watt_off_value"
CONF_WATT_IDLE_OP: Final[str] = "watt_idle_op"
CONF_WATT_IDLE_VALUE: Final[str] = "watt_idle_value"
CONF_WATT_PLAYING_OP: Final[str] = "watt_playing_op"
CONF_WATT_PLAYING_VALUE: Final[str] = "watt_playing_value"

WATT_OPERATOR_CHOICES: Final[tuple[str, ...]] = ("<", "<=", "=", ">", ">=")

DEFAULT_WATT_THRESHOLD_ON: Final[int] = 5
DEFAULT_STICKY_HOLD_SECONDS: Final[int] = 30
DEFAULT_EXPOSE_SECONDARY_SENSORS: Final[bool] = False
BOOT_INITIAL_PHASE_SECONDS: Final[int] = 30
AVAILABILITY_FRESHNESS_SECONDS: Final[int] = 600
# TV source arbitration: a conflicting WebOS-off/high-watt snapshot is
# provisional for one source window. This remains scoped to the TV variant.
TV_SOURCE_FRESHNESS_SECONDS: Final[int] = 20
TV_SOURCE_CONFLICT_HOLD_SECONDS: Final[int] = 20
TV_WATT_THRESHOLD_ON: Final[float] = 50.0


class PowerState(str, Enum):
    OFF = "off"
    STANDBY = "standby"
    IDLE = "idle"
    PLAYING = "playing"
    UNKNOWN = "unknown"


POWER_STATE_SLUGS: Final[tuple[str, ...]] = tuple(s.value for s in PowerState)


class PowerSource(str, Enum):
    INTEGRATION = "integration"
    WATT_PRIMARY = "watt_primary"
    WATT_FALLBACK = "watt_fallback"
    STICKY_HOLD = "sticky_hold"
    OVERRIDE = "override"
    NONE = "none"


STORAGE_KEY_LAST_POWERED: Final[str] = "last_powered"
STORAGE_KEY_LAST_POWERED_CHANGE: Final[str] = "last_powered_change"
STORAGE_KEY_OVERRIDE: Final[str] = "override"
STORAGE_KEY_OVERRIDE_POWERED: Final[str] = "powered"
STORAGE_KEY_OVERRIDE_POWER_STATE: Final[str] = "power_state"
STORAGE_KEY_OVERRIDE_EXPIRES_AT: Final[str] = "expires_at"

SERVICE_SET_OVERRIDE: Final[str] = "set_override"
SERVICE_CLEAR_OVERRIDE: Final[str] = "clear_override"
# Agenten-/MCP-fähige Import-/Export-Services (spiegeln die WS-Commands).
SERVICE_BULK_IMPORT: Final[str] = "bulk_import"
SERVICE_EXPORT_CONFIG: Final[str] = "export_config"
SERVICE_IMPORT_FILE_DRY_RUN: Final[str] = "import_file_dry_run"
SERVICE_IMPORT_FILE_APPLY: Final[str] = "import_file_apply"
ATTR_PAYLOAD: Final[str] = "payload"
ATTR_DRY_RUN: Final[str] = "dry_run"
ATTR_REPLACE: Final[str] = "replace"
ATTR_SLUG: Final[str] = "slug"
ATTR_POWERED: Final[str] = "powered"
ATTR_POWER_STATE: Final[str] = "power_state"
ATTR_EXPIRE_SECONDS: Final[str] = "expire_seconds"

UPDATE_INTERVAL_SECONDS: Final[int] = 60


def device_object_id_prefix(profile: str) -> str:
    """Object-id prefix for consolidated device sensors."""
    return f"{profile}_device_"


def group_object_id_prefix(profile: str) -> str:
    """Object-id prefix for light-group sensors."""
    return f"{profile}_light_group_"


def combined_object_id_prefix(profile: str) -> str:
    """Object-id prefix for combined-atomic sensors."""
    return f"{profile}_combined_"


def master_object_id_prefix(profile: str) -> str:
    """Object-id prefix for raw-source domain master sensors."""
    return f"{profile}_master_"


def entry_profile(entry) -> str:
    """Return the stored profile for a config entry."""
    profile = entry.data.get(CONF_PROFILE, DEFAULT_PROFILE)
    return profile if profile in PROFILES else DEFAULT_PROFILE


def storage_key(entry_id: str, slug: str, profile: str) -> str:
    """Profile- and entry-scoped storage key for one device."""
    return f"{DOMAIN}_{profile}_state_{entry_id}_{slug}"


def unique_id(entry_id: str, *parts: str) -> str:
    """Standalone unique_id with a domain prefix."""
    suffix = "_".join(str(p) for p in parts if p is not None)
    return f"{DOMAIN}_{entry_id}_{suffix}"


PANEL_URL_PATH = "benni_core_devices"
PANEL_TITLE = "Core Devices"
PANEL_ICON = "mdi:devices"
FRONTEND_DIR_URL = "/benni_core_devices_app"
FRONTEND_ENTRY = f"{FRONTEND_DIR_URL}/main.js"
PANEL_ELEMENT = "bcd-app"

WS_GET_STATUS = f"{DOMAIN}/get_status"
WS_GET_CATALOG = f"{DOMAIN}/get_catalog"
WS_GET_CONTRACT_CATALOG = f"{DOMAIN}/get_contract_catalog"
WS_GET_RAW_ENTITY_CATALOG = f"{DOMAIN}/get_raw_entity_catalog"
WS_SET_DEVICE = f"{DOMAIN}/set_device"
WS_REMOVE_DEVICE = f"{DOMAIN}/remove_device"
WS_SET_GROUP = f"{DOMAIN}/set_group"
WS_REMOVE_GROUP = f"{DOMAIN}/remove_group"
WS_BULK_IMPORT = f"{DOMAIN}/bulk_import"
WS_SET_COMBINED = f"{DOMAIN}/set_combined"
WS_REMOVE_COMBINED = f"{DOMAIN}/remove_combined"
WS_EXPORT_CONFIG = f"{DOMAIN}/export_config"
WS_AGENT_SPEC = f"{DOMAIN}/get_agent_spec"

