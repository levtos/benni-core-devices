"""Exercise hub and sensor registration without a running Home Assistant."""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TypedDict
from unittest.mock import AsyncMock, Mock, patch

import pytest

from test_coordinator_contract import _install_homeassistant_stubs


class _CoordinatorEntity:
    def __class_getitem__(cls, _item):
        return cls

    def __init__(self, coordinator):
        self.coordinator = coordinator


def _module(name, **attributes):
    module = ModuleType(name)
    vars(module).update(attributes)
    sys.modules[name] = module


@pytest.mark.parametrize(
    ("link_field", "existing"),
    [("via_device_id", True), ("via_device_id", False), ("via_device", True)],
)
def test_device_entities_keep_their_registered_hub_and_ids(monkeypatch, link_field, existing):
    package = "bcd_registry_test"
    domain = "benni_core_devices"
    hub_identifier = (domain, "hub")
    device_identifier = (domain, "device:tv")
    entry_id = "test-entry"
    hub = SimpleNamespace(id="existing-hub-id", identifiers={hub_identifier})
    device = SimpleNamespace(id="existing-device-id", identifiers={device_identifier})
    devices = {}
    if existing:
        devices.update({(entry_id, hub_identifier): hub, (entry_id, device_identifier): device})
    if link_field == "via_device_id":
        devices["other-entry", hub_identifier] = SimpleNamespace(id="other-hub-id")

    def register_device(*, config_entry_id, identifiers, **info):
        (identifier,) = identifiers
        key = (config_entry_id, identifier)
        if key not in devices:
            devices[key] = SimpleNamespace(
                id=f"device-{len(devices)}", identifiers=identifiers
            )
        registered = devices[key]
        if identifier == device_identifier:
            if link_field == "via_device_id":
                assert "via_device" not in info
                assert info["via_device_id"] == devices[config_entry_id, hub_identifier].id
                registered.via_device_id = info["via_device_id"]
            else:
                assert "via_device_id" not in info
                registered.via_device_id = devices[config_entry_id, info["via_device"]].id
        return registered

    async def forward_entry_setups(entry, platforms):
        def add_entities(entities):
            for entity in entities:
                registered = register_device(
                    config_entry_id=entry.entry_id, **entity._attr_device_info
                )
                assert entity.entity_id not in registered_entities
                registered_entities[entity.entity_id] = (
                    entity._attr_unique_id, registered.id
                )

        for platform in platforms:
            module = importlib.import_module(f"{package}.{platform}")
            await module.async_setup_entry(hass, entry, add_entities)

    registered_entities = {}
    hass = SimpleNamespace(
        data={},
        config_entries=SimpleNamespace(
            async_forward_entry_setups=forward_entry_setups,
            async_unload_platforms=AsyncMock(return_value=True),
        ),
    )
    entry = SimpleNamespace(
        data={},
        entry_id=entry_id,
        options={"devices": {"tv": {"expose_secondary_sensors": True}}},
        async_on_unload=Mock(),
        add_update_listener=Mock(),
    )

    integration_path = Path(__file__).resolve().parents[1] / "custom_components" / domain
    with patch.dict(sys.modules):
        _install_homeassistant_stubs()
        monkeypatch.setattr(
            sys.modules["homeassistant.const"], "Platform",
            SimpleNamespace(SENSOR="sensor", BINARY_SENSOR="binary_sensor"),
            raising=False,
        )
        monkeypatch.setattr(
            sys.modules["homeassistant.helpers.update_coordinator"],
            "CoordinatorEntity", _CoordinatorEntity, raising=False,
        )
        monkeypatch.setattr(
            sys.modules["homeassistant.helpers.entity_registry"],
            "async_entries_for_config_entry", lambda _registry, _entry_id: [], raising=False,
        )
        _module("homeassistant.components", __path__=[])
        _module(
            "homeassistant.components.sensor",
            SensorEntity=type("SensorEntity", (), {}),
            SensorDeviceClass=SimpleNamespace(ENUM="enum", POWER="power"),
            SensorStateClass=SimpleNamespace(MEASUREMENT="measurement"),
        )
        _module(
            "homeassistant.components.binary_sensor",
            BinarySensorEntity=type("BinarySensorEntity", (), {}),
            BinarySensorDeviceClass=object,
        )
        _module(
            "homeassistant.helpers.device_registry",
            DeviceInfo=TypedDict("DeviceInfo", {link_field: str}, total=False),
            DeviceEntryType=SimpleNamespace(SERVICE="service"),
            async_get=lambda _hass: SimpleNamespace(async_get_or_create=register_device),
            async_entries_for_config_entry=lambda _registry, config_id: [
                registered for (owner, _), registered in devices.items() if owner == config_id
            ],
        )
        _module(
            "homeassistant.helpers.entity",
            Entity=object,
            async_generate_entity_id=lambda fmt, object_id, **_: fmt.format(object_id),
        )
        _module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
        _module(f"{package}.services", async_register_services=Mock())
        _module(f"{package}.view", async_setup_view=AsyncMock())
        _module(f"{package}.websocket_api", async_setup_websocket_api=Mock())

        spec = importlib.util.spec_from_file_location(package, integration_path / "__init__.py")
        integration = importlib.util.module_from_spec(spec)
        sys.modules[package] = integration
        spec.loader.exec_module(integration)
        coordinator = integration.DeviceCoordinator
        monkeypatch.setattr(coordinator, "hass", hass, raising=False)
        monkeypatch.setattr(coordinator, "async_load_stored", AsyncMock())
        monkeypatch.setattr(coordinator, "async_config_entry_first_refresh", AsyncMock(), raising=False)
        monkeypatch.setattr(coordinator, "async_start_listeners", Mock())
        monkeypatch.setattr(coordinator, "expose_secondary_sensors", True)
        monkeypatch.setattr(coordinator, "has_watt", True)

        async def setup_and_reload():
            assert await integration.async_setup_entry(hass, entry)
            first_devices = devices.copy()
            first_entities = registered_entities.copy()
            assert await integration.async_unload_entry(hass, entry)
            registered_entities.clear()
            assert await integration.async_setup_entry(hass, entry)
            assert devices == first_devices
            assert registered_entities == first_entities

        asyncio.run(setup_and_reload())

    if existing:
        assert devices[entry_id, hub_identifier] is hub
        assert devices[entry_id, device_identifier] is device
    hub = devices[entry_id, hub_identifier]
    device = devices[entry_id, device_identifier]
    assert device.via_device_id == hub.id
    assert {identifier for owner, identifier in devices if owner == entry_id} == {
        hub_identifier, device_identifier, (domain, "groups_hub"),
        (domain, "combined_hub"), (domain, "master_hub"),
    }
    unique_id_prefix = f"{domain}_{entry_id}_tv"
    assert registered_entities == {
        "sensor.benni_device_tv": (f"{unique_id_prefix}_main", device.id),
        "sensor.benni_device_tv_power_state": (f"{unique_id_prefix}_power_state", device.id),
        "sensor.benni_device_tv_watt": (f"{unique_id_prefix}_watt", device.id),
        "binary_sensor.benni_device_tv_powered": (f"{unique_id_prefix}_powered", device.id),
        "binary_sensor.benni_device_tv_available": (f"{unique_id_prefix}_available", device.id),
    }
