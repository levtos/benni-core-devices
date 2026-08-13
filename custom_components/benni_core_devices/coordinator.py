"""DataUpdateCoordinator für Benni Core · Devices v2 (rollenbasiert).

Pro Device-Instanz ein Coordinator:
- löst die konfigurierten Rollen → Entities auf und liest deren HA-States
- wählt den Compute-Pfad über ``AtomicClassSpec.power_model``:
  integration_watt_sticky → ``logic.compute_device`` (unverändert),
  passthrough_state → ``logic.compute_passthrough``,
  numeric → ``logic.compute_numeric``
- persistiert last_powered + last_state + Override über HA-Restarts
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.entity_registry import async_get as async_get_entities
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import logic
from .attributes import build_main_attributes
from .const import (
    CONF_SLUG,
    DATA_COMBINEDS,
    DATA_COORDINATORS,
    DATA_MASTERS,
    DOMAIN,
    POWER_MODEL_NUMERIC,
    POWER_MODEL_PASSTHROUGH,
    POWER_MODEL_WATT_PRIMARY_STICKY,
    STORAGE_KEY_LAST_POWERED,
    STORAGE_KEY_LAST_POWERED_CHANGE,
    STORAGE_KEY_OVERRIDE,
    STORAGE_KEY_OVERRIDE_EXPIRES_AT,
    STORAGE_KEY_OVERRIDE_POWER_STATE,
    STORAGE_KEY_OVERRIDE_POWERED,
    STORAGE_VERSION,
    TV_SOURCE_CONFLICT_HOLD_SECONDS,
    TV_SOURCE_FRESHNESS_SECONDS,
    TV_WATT_THRESHOLD_ON,
    UPDATE_INTERVAL_SECONDS,
    entry_profile,
    storage_key,
)
from .device_types import DeviceConfigV2, parse_device_config
from .logic import (
    DeviceConfig,
    DeviceInputs,
    DevicePersisted,
    DeviceResult,
    Override,
    SlotReading,
)
from .slot_reader import slot_reading_from_values

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY_LAST_STATE = "last_state"
STORAGE_KEY_LAST_WATT_ACTIVE = "last_watt_active"
STORAGE_KEY_SOURCE_CONFLICT_SINCE = "source_conflict_since"


class DeviceCoordinator(DataUpdateCoordinator[DeviceResult]):
    """Treibt einen Device-Sensor (v2, rollenbasiert)."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, conf: dict[str, Any]
    ) -> None:
        slug = str(conf[CONF_SLUG])
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}_{slug}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.entry = entry
        self._profile_name = entry_profile(entry)
        self._cfg: DeviceConfigV2 = parse_device_config(slug, conf) or DeviceConfigV2(
            slug=slug,
            display_name=str(conf.get("display_name") or slug),
            atomic_class="generic_expert",
            variant="adapter",
        )
        self._store = Store(
            hass, STORAGE_VERSION, storage_key(entry.entry_id, slug, self._profile_name)
        )
        self._persisted = DevicePersisted(
            last_powered=None, last_powered_change=None, override=None, last_state=None,
            last_watt_active=None, source_conflict_since=None,
        )
        self._unsub_listeners: list[CALLBACK_TYPE] = []
        self._boot_start: datetime = dt_util.now()
        self._last_inputs: DeviceInputs | None = None

    # ─────────────────────────────────────────────────── Config Access

    @property
    def cfg(self) -> DeviceConfigV2:
        return self._cfg

    @property
    def slug(self) -> str:
        return self._cfg.slug

    @property
    def display_name(self) -> str:
        return self._cfg.display_name

    @property
    def profile_name(self) -> str:
        return self._profile_name

    @property
    def atomic_class(self) -> str:
        return self._cfg.atomic_class

    @property
    def variant(self) -> str:
        return self._cfg.variant

    @property
    def model(self) -> str:
        return f"{self._cfg.atomic_class}/{self._cfg.variant}" if self._cfg.variant else self._cfg.atomic_class

    @property
    def power_model(self) -> str:
        spec = self._cfg.spec
        return spec.power_model if spec else POWER_MODEL_PASSTHROUGH

    @property
    def expose_secondary_sensors(self) -> bool:
        return self._cfg.expose_secondary_sensors

    @property
    def has_watt(self) -> bool:
        return self._cfg.watt_role() is not None

    @property
    def compute_entities(self) -> dict[str, str]:
        return self._cfg.compute_entities()

    # ─────────────────────────────────────────────────── Storage

    async def async_load_stored(self) -> None:
        raw = await self._store.async_load()
        if raw is None:
            return
        self._persisted = _persisted_from_dict(raw)

    async def _async_save(self) -> None:
        await self._store.async_save(_persisted_to_dict(self._persisted))

    # ─────────────────────────────────────────────────── Lifecycle

    def async_start_listeners(self) -> None:
        watched = list(self.compute_entities.values())
        if watched:
            self._unsub_listeners.append(
                async_track_state_change_event(self.hass, watched, self._async_on_slot_change)
            )

    def async_stop_listeners(self) -> None:
        for unsub in self._unsub_listeners:
            unsub()
        self._unsub_listeners.clear()

    @callback
    def _async_on_slot_change(self, _event: Event) -> None:
        self.hass.async_create_task(self._async_recompute_and_persist())

    async def _async_recompute_and_persist(self) -> None:
        result = self._compute()
        await self._persist_if_changed(result)
        self.async_set_updated_data(result)

    # ─────────────────────────────────────────────────── Service-Hooks

    async def async_set_override(
        self, powered: bool | None, power_state: str | None, expire_seconds: int | None
    ) -> DeviceResult:
        now = dt_util.now()
        override = logic.build_override(powered, power_state, expire_seconds, now)
        self._persisted = DevicePersisted(
            last_powered=self._persisted.last_powered,
            last_powered_change=self._persisted.last_powered_change,
            override=override,
            last_state=self._persisted.last_state,
            last_watt_active=self._persisted.last_watt_active,
            source_conflict_since=self._persisted.source_conflict_since,
        )
        await self._async_save()
        result = self._compute()
        self.async_set_updated_data(result)
        return result

    async def async_clear_override(self) -> DeviceResult:
        self._persisted = DevicePersisted(
            last_powered=self._persisted.last_powered,
            last_powered_change=self._persisted.last_powered_change,
            override=None,
            last_state=self._persisted.last_state,
            last_watt_active=self._persisted.last_watt_active,
            source_conflict_since=self._persisted.source_conflict_since,
        )
        await self._async_save()
        result = self._compute()
        self.async_set_updated_data(result)
        return result

    # ─────────────────────────────────────────────────── Compute

    async def _async_update_data(self) -> DeviceResult:
        return self._compute()

    def _compute(self) -> DeviceResult:
        now = dt_util.now()
        inputs = self._read_inputs(now)
        config = self._build_logic_config()
        pm = self.power_model
        if pm == POWER_MODEL_NUMERIC:
            result = logic.compute_numeric(config, inputs, self._persisted, now)
        elif pm == POWER_MODEL_PASSTHROUGH:
            result = logic.compute_passthrough(config, inputs, self._persisted, now)
        else:
            result = logic.compute_device(
                config, inputs, self._persisted, now,
                watt_primary=(pm == POWER_MODEL_WATT_PRIMARY_STICKY),
            )

        if (
            self._persisted.override is not None
            and logic.is_override_expired(self._persisted.override, now)
        ):
            self._persisted = DevicePersisted(
                last_powered=self._persisted.last_powered,
                last_powered_change=self._persisted.last_powered_change,
                override=None,
                last_state=self._persisted.last_state,
                last_watt_active=self._persisted.last_watt_active,
                source_conflict_since=self._persisted.source_conflict_since,
            )
        return result

    async def _persist_if_changed(self, result: DeviceResult) -> None:
        new_state = result.state if not result.fail_safe_active else self._persisted.last_state
        durable_changed = (
        result.powered != self._persisted.last_powered
        or result.last_powered_change != self._persisted.last_powered_change
        or new_state != self._persisted.last_state
        or result.source_conflict_since != self._persisted.source_conflict_since
        )
        # last_watt_active immer in-memory fortschreiben (das Halte-Fenster misst
        # ab letzter Aktivität), aber nur bei einer dauerhaften Änderung auf die
        # Platte schreiben — sonst I/O bei jedem Watt-Tick.
        self._persisted = DevicePersisted(
            last_powered=result.powered,
            last_powered_change=result.last_powered_change,
            override=self._persisted.override,
            last_state=new_state,
            last_watt_active=result.last_watt_active,
            source_conflict_since=result.source_conflict_since,
        )
        if durable_changed:
            await self._async_save()

    def _build_logic_config(self) -> DeviceConfig:
        is_tv = self._cfg.atomic_class == "media_device" and self._cfg.variant == "tv"
        return DeviceConfig(
            slug=self._cfg.slug,
            display_name=self._cfg.display_name,
            device_type=self._cfg.atomic_class,
            variant=self._cfg.variant,
            watt_threshold_on=TV_WATT_THRESHOLD_ON if is_tv else self._cfg.watt_threshold_on,
            watt_buckets=logic.parse_watt_buckets(list(self._cfg.watt_buckets)),
            sticky_hold_seconds=self._cfg.sticky_hold_seconds,
            area_id=self._derive_area_id(),
            configured_slots=tuple(self.compute_entities.keys()),
            fail_safe=self._cfg.fail_safe,
            source_freshness_seconds=(
                TV_SOURCE_FRESHNESS_SECONDS if is_tv else None
            ),
            source_conflict_hold_seconds=(
                TV_SOURCE_CONFLICT_HOLD_SECONDS if is_tv else 0
            ),
        )

    def _derive_area_id(self) -> str | None:
        role = self._cfg.integration_role()
        eid = self._cfg.entity_for_role(role) if role else None
        if not eid:
            srcs = self._cfg.source_entities()
            eid = next(iter(srcs.values()), None)
        if not eid:
            return None
        ent_reg = async_get_entities(self.hass)
        entry = ent_reg.async_get(eid)
        if entry is None:
            return None
        if entry.area_id:
            return entry.area_id
        if entry.device_id:
            from homeassistant.helpers.device_registry import async_get as async_get_devs

            dev_reg = async_get_devs(self.hass)
            dev = dev_reg.async_get(entry.device_id)
            if dev and dev.area_id:
                return dev.area_id
        return None

    def _read_inputs(self, now: datetime) -> DeviceInputs:
        slots: dict[str, SlotReading] = {}
        for role, binding in self._cfg.compute_bindings().items():
            slots[role] = self._read_slot(binding.entity, binding.attribute)
        pm = self.power_model
        if pm == POWER_MODEL_NUMERIC:
            integration_slot = None
            state_slot = self._cfg.numeric_role()
            watt_slot = None
        elif pm == POWER_MODEL_PASSTHROUGH:
            integration_slot = self._cfg.integration_role()
            state_slot = self._cfg.state_role() or integration_slot
            watt_slot = None
        else:
            integration_slot = self._cfg.integration_role()
            state_slot = self._cfg.state_role()
            watt_slot = self._cfg.watt_role()
        inputs = DeviceInputs(
            slots=slots,
            integration_slot=integration_slot,
            state_slot=state_slot,
            watt_slot=watt_slot,
            boot_phase_active=logic.is_boot_phase(self._boot_start, now),
        )
        self._last_inputs = inputs
        return inputs

    def _read_slot(self, entity_id: str | None, attribute: str | None = None) -> SlotReading:
        if not entity_id:
            return SlotReading(value=None)
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN, ""):
            return SlotReading(value=None)
        return slot_reading_from_values(
            state.state,
            attributes=dict(state.attributes),
            attribute=attribute,
            last_updated=state.last_updated,
        )

    @property
    def main_attributes(self) -> dict[str, Any]:
        result = self.data
        if result is None or self._last_inputs is None:
            return {}
        return build_main_attributes(self._cfg, self._last_inputs, result)


# ─────────────────────────────────────────────────────────────────────────────
# Combined-Atomic-Coordinator (v0, unverändert)
# ─────────────────────────────────────────────────────────────────────────────


class CombinedCoordinator(DataUpdateCoordinator):
    """Treibt einen Combined-Atomic-Sensor (First-Match-Wins, v0)."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        slug: str,
        raw_conf: dict,
        *,
        kind: str = "combined",
    ) -> None:
        self._kind = "master" if kind == "master" else "combined"
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}_{self._kind}_{slug}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        from .combined import CombinedConfig, CombinedPersisted, parse_combined

        self.entry = entry
        self._slug = slug
        self._profile_name = entry_profile(entry)
        self._config = parse_combined(slug, raw_conf) or CombinedConfig(
            slug=slug, display_name=slug
        )
        self._shadow_of = raw_conf.get("shadow_of") if isinstance(raw_conf, dict) else None
        self._unsub_listeners: list[CALLBACK_TYPE] = []
        self._last_readings: dict[str, Any] = {}
        self._store = Store(
            hass, STORAGE_VERSION,
            storage_key(entry.entry_id, f"{self._kind}_{slug}", self._profile_name),
        )
        self._persisted = CombinedPersisted()

    async def async_load_stored(self) -> None:
        raw = await self._store.async_load()
        if isinstance(raw, dict):
            from .combined import CombinedPersisted

            self._persisted = CombinedPersisted(
                last_state=raw.get("last_state"),
                node_states=dict(raw.get("node_states") or {}),
            )

    @property
    def slug(self) -> str:
        return self._slug

    @property
    def profile_name(self) -> str:
        return self._profile_name

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def is_master(self) -> bool:
        return self._kind == "master"

    @property
    def config(self):
        return self._config

    @property
    def source_entities(self) -> list[str]:
        return [s.entity for s in self._config.sources if s.entity]

    def _read(self) -> dict[str, Any]:
        from .combined import SourceReading

        readings: dict[str, Any] = {}
        for src in self._config.sources:
            if not src.entity:
                continue
            state = self.hass.states.get(src.entity)
            if state is None:
                readings[src.key] = SourceReading(value=None, available=False)
                continue
            value: Any = state.attributes.get(src.attribute) if src.attribute else state.state
            if value is None or (not src.attribute and value in (STATE_UNAVAILABLE, STATE_UNKNOWN, "")):
                readings[src.key] = SourceReading(
                    value=None,
                    available=False,
                    attributes=dict(state.attributes),
                    last_updated=state.last_updated,
                )
                continue
            numeric: float | None
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                numeric = None
            readings[src.key] = SourceReading(
                value=value, numeric=numeric, available=True,
                attributes=dict(state.attributes),
                last_updated=state.last_updated,
            )
        return readings

    def _compute(self):
        from .combined import CombinedPersisted, evaluate_combined

        self._last_readings = self._read()
        result = evaluate_combined(
            self._config, self._last_readings, self._persisted, dt_util.now()
        )
        # v1.0b: last_state + node_states persistieren (für latch/previous/hold_last).
        new = CombinedPersisted(last_state=result.state, node_states=result.node_states)
        if new != self._persisted:
            self._persisted = new
            self.hass.async_create_task(self._store.async_save({
                "last_state": new.last_state, "node_states": new.node_states,
            }))
        return result

    async def _async_update_data(self):
        return self._compute()

    def async_start_listeners(self) -> None:
        watched = self.source_entities
        if watched:
            self._unsub_listeners.append(
                async_track_state_change_event(self.hass, watched, self._async_on_source_change)
            )

    def async_stop_listeners(self) -> None:
        for unsub in self._unsub_listeners:
            unsub()
        self._unsub_listeners.clear()

    @callback
    def _async_on_source_change(self, _event: Event) -> None:
        self.async_set_updated_data(self._compute())

    def derived_state(self, derived) -> bool | None:
        from .combined import evaluate_derived

        result = self.data
        if result is None:
            return None
        return evaluate_derived(derived, self._config, self._last_readings, result)

    @property
    def attributes(self) -> dict[str, Any]:
        from .combined import exposed_derived_attributes

        result = self.data
        if result is None:
            return {}
        return {
            "slug": self._slug,
            "kind": self._kind,
            "display_name": self._config.display_name,
            "output_type": self._config.output_type,
            "output": result.output,
            "reason": result.reason,
            "code_legend": dict(self._config.code_legend),
            "source_entities": result.source_entities,
            "source_attributes": result.source_attributes,
            "source_states": result.source_states,
            "source_available": result.source_available,
            "missing_sources": result.missing_sources,
            "degraded": result.degraded,
            "degraded_reason": result.degraded_reason,
            "derived": result.derived,
            **exposed_derived_attributes(self._config, result),
            **self._shadow_compare(result),
        }

    def _shadow_compare(self, result) -> dict[str, Any]:
        """Strangler-Vergleich gegen einen alten YAML-Sensor (LH §8)."""
        if not self._shadow_of:
            return {}
        old = self.hass.states.get(self._shadow_of)
        actual = old.state if old is not None else None
        return {"shadow_compare": {
            "of": self._shadow_of,
            "expected": result.state,
            "actual": actual,
            "diverges": actual is not None and str(actual) != str(result.state),
        }}


# ─────────────────────────────────────────────────────────────────────────────
# Lookup-Helper
# ─────────────────────────────────────────────────────────────────────────────


@callback
def combined_coordinators_for_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, "CombinedCoordinator"]:
    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not bucket:
        return {}
    coords = bucket.get(DATA_COMBINEDS)
    return coords if isinstance(coords, dict) else {}


@callback
def master_coordinators_for_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, "CombinedCoordinator"]:
    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not bucket:
        return {}
    coords = bucket.get(DATA_MASTERS)
    return coords if isinstance(coords, dict) else {}


@callback
def coordinators_for_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, DeviceCoordinator]:
    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not bucket:
        return {}
    coords = bucket.get(DATA_COORDINATORS)
    return coords if isinstance(coords, dict) else {}


@callback
def all_coordinators(hass: HomeAssistant) -> list[DeviceCoordinator]:
    out: list[DeviceCoordinator] = []
    for bucket in hass.data.get(DOMAIN, {}).values():
        if not isinstance(bucket, dict):
            continue
        coords = bucket.get(DATA_COORDINATORS)
        if isinstance(coords, dict):
            out.extend(c for c in coords.values() if isinstance(c, DeviceCoordinator))
    return out


@callback
def coordinator_by_slug(hass: HomeAssistant, slug: str) -> DeviceCoordinator | None:
    for c in all_coordinators(hass):
        if c.slug == slug:
            return c
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Persistenz-Codec
# ─────────────────────────────────────────────────────────────────────────────


def _persisted_to_dict(p: DevicePersisted) -> dict[str, Any]:
    out: dict[str, Any] = {
        STORAGE_KEY_LAST_POWERED: p.last_powered,
        STORAGE_KEY_LAST_POWERED_CHANGE: (
            p.last_powered_change.isoformat() if p.last_powered_change else None
        ),
        STORAGE_KEY_LAST_STATE: p.last_state,
        STORAGE_KEY_LAST_WATT_ACTIVE: (
            p.last_watt_active.isoformat() if p.last_watt_active else None
        ),
        STORAGE_KEY_SOURCE_CONFLICT_SINCE: (
            p.source_conflict_since.isoformat() if p.source_conflict_since else None
        ),
        STORAGE_KEY_OVERRIDE: None,
    }
    if p.override is not None:
        out[STORAGE_KEY_OVERRIDE] = {
            STORAGE_KEY_OVERRIDE_POWERED: p.override.powered,
            STORAGE_KEY_OVERRIDE_POWER_STATE: p.override.power_state,
            STORAGE_KEY_OVERRIDE_EXPIRES_AT: (
                p.override.expires_at.isoformat() if p.override.expires_at else None
            ),
        }
    return out


def _persisted_from_dict(raw: dict[str, Any]) -> DevicePersisted:
    override_raw = raw.get(STORAGE_KEY_OVERRIDE)
    override: Override | None = None
    if isinstance(override_raw, dict):
        override = Override(
            powered=override_raw.get(STORAGE_KEY_OVERRIDE_POWERED),
            power_state=override_raw.get(STORAGE_KEY_OVERRIDE_POWER_STATE),
            expires_at=_parse_iso(override_raw.get(STORAGE_KEY_OVERRIDE_EXPIRES_AT)),
        )
    return DevicePersisted(
        last_powered=raw.get(STORAGE_KEY_LAST_POWERED),
        last_powered_change=_parse_iso(raw.get(STORAGE_KEY_LAST_POWERED_CHANGE)),
        override=override,
        last_state=raw.get(STORAGE_KEY_LAST_STATE),
        last_watt_active=_parse_iso(raw.get(STORAGE_KEY_LAST_WATT_ACTIVE)),
        source_conflict_since=_parse_iso(raw.get(STORAGE_KEY_SOURCE_CONFLICT_SINCE)),
    )


def _parse_iso(v: Any) -> datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None
