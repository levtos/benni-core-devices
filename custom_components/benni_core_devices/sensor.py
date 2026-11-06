"""SensorEntities für Benni Core · Devices.

Pro Device-Instanz:
- Haupt-Sensor `sensor.benni_device_<slug>` (immer)
- Optional Sekundär-Sensoren bei `expose_secondary_sensors=true`:
  - `sensor.benni_device_<slug>_power_state`
  - `sensor.benni_device_<slug>_watt`  (nur wenn watt_sensor konfiguriert)

binary_sensor.py liefert die boolean-Sekundär-Sensoren (powered/available).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, async_generate_entity_id
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_DISPLAY_NAME,
    CONF_GROUP_MEMBERS,
    CONF_LIGHT_GROUPS,
    DATA_HUB_DEVICE_ID,
    DOMAIN,
    POWER_STATE_SLUGS,
    combined_object_id_prefix,
    device_object_id_prefix,
    group_object_id_prefix,
    entry_profile,
    master_object_id_prefix,
    unique_id,
)
from .coordinator import (
    CombinedCoordinator,
    DeviceCoordinator,
    combined_coordinators_for_entry,
    coordinators_for_entry,
    master_coordinators_for_entry,
)
from .logic import DeviceResult


def _object_id(profile: str, slug: str, suffix: str | None = None) -> str:
    base = f"{device_object_id_prefix(profile)}{slug}"
    return f"{base}_{suffix}" if suffix else base


def _device_info(coordinator: DeviceCoordinator) -> DeviceInfo:
    """Link the device to the hub registered for this config entry."""
    hub_link: dict[str, Any]
    if "via_device_id" in DeviceInfo.__annotations__:
        entry_data = coordinator.hass.data[DOMAIN][coordinator.entry.entry_id]
        hub_link = {"via_device_id": entry_data[DATA_HUB_DEVICE_ID]}
    else:
        # Older supported Home Assistant releases only accept the identifier.
        from . import HUB_IDENTIFIER

        hub_link = {"via_device": HUB_IDENTIFIER}

    return DeviceInfo(
        identifiers={(DOMAIN, f"device:{coordinator.slug}")},
        name=coordinator.display_name,
        manufacturer="Benni Core · Devices",
        model=coordinator.model,
        **hub_link,
    )


async def async_get_entities(
    hass: HomeAssistant, entry: ConfigEntry, platform: str
) -> list[Entity]:
    if platform != Platform.SENSOR:
        return []
    out: list[Entity] = []
    for coordinator in coordinators_for_entry(hass, entry).values():
        out.append(DeviceMainSensor(coordinator, entry))
        if coordinator.expose_secondary_sensors:
            out.append(PowerStateSensor(coordinator, entry))
            if coordinator.has_watt:
                out.append(WattSensor(coordinator, entry))
    # Combined-Atomics (First-Match-Wins) — ein Sensor je Combined.
    for coordinator in combined_coordinators_for_entry(hass, entry).values():
        out.append(CombinedAtomicSensor(coordinator, entry))
    # Domain-Master: raw-source master sensors with the same engine.
    for coordinator in master_coordinators_for_entry(hass, entry).values():
        out.append(CombinedAtomicSensor(coordinator, entry))
    # Atomic Light Groups (Mengen von Lampen) — ein Sensor je Gruppe.
    groups = entry.options.get(CONF_LIGHT_GROUPS)
    if isinstance(groups, dict):
        for slug, conf in groups.items():
            members = [m for m in (conf.get(CONF_GROUP_MEMBERS) or []) if isinstance(m, str)]
            out.append(
                LightGroupSensor(
                    hass, entry, slug,
                    name=conf.get(CONF_DISPLAY_NAME, slug),
                    members=members,
                )
            )
    return out


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(await async_get_entities(hass, entry, Platform.SENSOR))


class _BaseDeviceSensor(CoordinatorEntity[DeviceCoordinator], SensorEntity):
    _attr_has_entity_name = False
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: DeviceCoordinator,
        entry: ConfigEntry,
        *,
        suffix: str,
        object_id: str,
        name: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = unique_id(entry.entry_id, suffix)
        self._attr_name = name
        self._attr_device_info = _device_info(coordinator)
        # Entity-ID deterministisch erzwingen (sonst leitet HA sie aus dem
        # Anzeigenamen ab → sensor.tv). Wir wollen sensor.benni_device_<slug>.
        self.entity_id = async_generate_entity_id(
            "sensor.{}", object_id, hass=coordinator.hass
        )

    @property
    def _result(self) -> DeviceResult | None:
        return self.coordinator.data


class DeviceMainSensor(_BaseDeviceSensor):
    """Der EINE konsolidierte Sensor pro Device."""

    _attr_icon = "mdi:chip"

    def __init__(self, coordinator: DeviceCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            suffix=f"{coordinator.slug}_main",
            object_id=_object_id(coordinator.profile_name, coordinator.slug),
            name=coordinator.display_name,
        )

    @property
    def native_value(self) -> str | None:
        r = self._result
        return r.state if r else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # Rich-Atomic-Attribut-Layer: eine Quelle der Wahrheit im Coordinator
        # (Standard-Attribute + generische Slot-Diagnose + reiche Typ-Attribute).
        return self.coordinator.main_attributes


class PowerStateSensor(_BaseDeviceSensor):
    """Optionaler Sekundär-Sensor: aus Watt-Buckets abgeleiteter power_state."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(POWER_STATE_SLUGS)
    _attr_icon = "mdi:flash"

    def __init__(self, coordinator: DeviceCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            suffix=f"{coordinator.slug}_power_state",
            object_id=_object_id(coordinator.profile_name, coordinator.slug, "power_state"),
            name=f"{coordinator.display_name} Power State",
        )

    @property
    def native_value(self) -> str | None:
        r = self._result
        return r.power_state if r else None


class LightGroupSensor(SensorEntity):
    """Atomic Light Group: eine Menge von Lampen als EINE Wahrheit.

    State = "on" wenn mind. eine Member-Lampe an ist, sonst "off".
    Attribute exponieren die Member (`members` + HA-Group-Style `entity_id`),
    damit Konsumenten (z.B. light_policy) sie auf die Einzellampen expandieren
    können (Scene-Presets verteilt Paletten über Einzel-Member).
    """

    _attr_should_poll = False
    _attr_has_entity_name = False
    _attr_icon = "mdi:lightbulb-group"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        slug: str,
        *,
        name: str,
        members: list[str],
    ) -> None:
        from . import GROUPS_HUB_IDENTIFIER

        self._members = list(members)
        self._attr_unique_id = unique_id(entry.entry_id, f"group_{slug}")
        self._attr_name = name
        self._attr_device_info = DeviceInfo(identifiers={GROUPS_HUB_IDENTIFIER})
        self.entity_id = async_generate_entity_id(
            "sensor.{}", f"{group_object_id_prefix(entry_profile(entry))}{slug}", hass=hass
        )
        self._unsub = None

    async def async_added_to_hass(self) -> None:
        if self._members:
            self._unsub = async_track_state_change_event(
                self.hass, self._members, self._on_member_change
            )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @callback
    def _on_member_change(self, _event) -> None:
        self.async_write_ha_state()

    def _on_members(self) -> list[str]:
        return [
            m for m in self._members
            if (st := self.hass.states.get(m)) is not None and st.state == "on"
        ]

    @property
    def native_value(self) -> str:
        return "on" if self._on_members() else "off"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        on = self._on_members()
        return {
            "members": list(self._members),
            "entity_id": list(self._members),  # HA-Group-Style für Konsumenten
            "member_count": len(self._members),
            "on_count": len(on),
            "any_on": bool(on),
        }


class CombinedAtomicSensor(CoordinatorEntity[CombinedCoordinator], SensorEntity):
    """Combined or domain-master sensor (First-Match-Wins, v0).

    Attribute spiegeln Quellen, Reason, Code-Legende und Degraded-Diagnose wider.
    """

    _attr_has_entity_name = False
    _attr_should_poll = False
    _attr_icon = "mdi:set-merge"

    def __init__(self, coordinator: CombinedCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        from . import COMBINED_HUB_IDENTIFIER, MASTER_HUB_IDENTIFIER

        kind = coordinator.kind
        prefix = (
            master_object_id_prefix(coordinator.profile_name)
            if coordinator.is_master
            else combined_object_id_prefix(coordinator.profile_name)
        )
        self._attr_unique_id = unique_id(entry.entry_id, f"{kind}_{coordinator.slug}")
        self._attr_name = coordinator.config.display_name
        self._attr_device_info = DeviceInfo(
            identifiers={MASTER_HUB_IDENTIFIER if coordinator.is_master else COMBINED_HUB_IDENTIFIER}
        )
        self._attr_icon = "mdi:hub" if coordinator.is_master else "mdi:set-merge"
        self.entity_id = async_generate_entity_id(
            "sensor.{}",
            f"{prefix}{coordinator.slug}",
            hass=coordinator.hass,
        )

    @property
    def native_value(self) -> str | None:
        r = self.coordinator.data
        return r.state if r else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.coordinator.attributes


class WattSensor(_BaseDeviceSensor):
    """Optionaler Sekundär-Sensor: numerischer Watt-Wert."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "W"
    _attr_icon = "mdi:lightning-bolt"

    def __init__(self, coordinator: DeviceCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            suffix=f"{coordinator.slug}_watt",
            object_id=_object_id(coordinator.profile_name, coordinator.slug, "watt"),
            name=f"{coordinator.display_name} Watt",
        )

    @property
    def native_value(self) -> float | None:
        r = self._result
        return r.watt if r else None
