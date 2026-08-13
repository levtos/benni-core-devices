"""Regression tests for the HA coordinator-to-pure-logic config boundary."""

from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime, timezone

def _install_homeassistant_stubs() -> None:
    """Load the coordinator in the HA-free test environment."""
    if "homeassistant" in sys.modules:
        return

    def module(name: str) -> types.ModuleType:
        value = types.ModuleType(name)
        sys.modules[name] = value
        return value

    homeassistant = module("homeassistant")
    homeassistant.__path__ = []

    config_entries = module("homeassistant.config_entries")
    config_entries.ConfigEntry = object

    const = module("homeassistant.const")
    const.STATE_UNAVAILABLE = "unavailable"
    const.STATE_UNKNOWN = "unknown"

    core = module("homeassistant.core")
    core.CALLBACK_TYPE = object
    core.Event = object
    core.HomeAssistant = object
    core.callback = lambda function: function

    helpers = module("homeassistant.helpers")
    helpers.__path__ = []
    entity_registry = module("homeassistant.helpers.entity_registry")
    entity_registry.async_get = lambda _hass: None
    event = module("homeassistant.helpers.event")
    event.async_track_state_change_event = lambda *_args: lambda: None
    storage = module("homeassistant.helpers.storage")

    class Store:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

    storage.Store = Store
    update_coordinator = module("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator:
        @classmethod
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, *_args, **_kwargs) -> None:
            pass

    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator

    util = module("homeassistant.util")
    util.__path__ = []
    dt_util = module("homeassistant.util.dt")
    dt_util.now = lambda: datetime.now(timezone.utc)


def test_device_coordinator_maps_v2_without_legacy_variant_argument():
    _install_homeassistant_stubs()
    coordinator_module = importlib.import_module("bcd_pure_pkg.coordinator")

    entry = types.SimpleNamespace(data={}, entry_id="test-entry")
    coordinator = coordinator_module.DeviceCoordinator(
        object(),
        entry,
        {
            "slug": "living_tv",
            "display_name": "Wohnzimmer TV",
            "atomic_class": "media_device",
            "variant": "tv",
        },
    )

    logic_config = coordinator._build_logic_config()

    assert coordinator.cfg.variant == "tv"
    assert logic_config.device_type == "media_device"
    assert logic_config.watt_threshold_on == 50
    assert logic_config.source_freshness_seconds == 20
    assert logic_config.source_conflict_hold_seconds == 20
    assert not hasattr(logic_config, "variant")
