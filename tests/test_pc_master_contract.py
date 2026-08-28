"""Regression tests for the watt-owned PC Device Master contract."""

from __future__ import annotations

import bcd_combined as CB


def _pc_config() -> CB.CombinedConfig:
    raw = {
        "display_name": "PC",
        "output_type": "enum",
        "sources": [
            {
                "key": "state",
                "role": "pc_supply_control",
                "entity": "switch.living_pc_plug",
                "required": False,
            },
            {
                "key": "source_watt",
                "role": "pc_watt",
                "entity": "sensor.living_pc_plug_power",
            },
        ],
        "derived_values": [
            {
                "name": "watt_active",
                "kind": "latch",
                "set": "${source_watt} >= 40",
                "reset": "${source_watt} < 20",
                "fail_safe": "off",
                "expose": True,
            },
            {
                "name": "is_active",
                "kind": "gate",
                "expr": "${watt_active}",
                "expose": True,
            },
            {
                "name": "available",
                "kind": "gate",
                "expr": "${source_watt} != null",
                "expose": True,
            },
            {
                "name": "control_available",
                "kind": "gate",
                "expr": "${state} != null",
                "expose": True,
            },
            {
                "name": "source_quality",
                "kind": "health",
                "atomics": ["state", "source_watt"],
                "expose": True,
            },
        ],
        "rules": [
            {
                "source": "source_quality",
                "op": "eq",
                "value": "problem",
                "output": "unknown",
                "reason": "pc_source_problem",
            },
            {
                "source": "is_active",
                "op": "eq",
                "value": "on",
                "output": "active",
                "reason": "pc_active",
            },
        ],
        "default_output": "off",
        "default_reason": "pc_inactive",
        "diagnostics": {"fail_safe": "unknown"},
    }
    config = CB.parse_combined("pc", raw)
    assert config is not None
    return config


def _watt(value: float) -> CB.SourceReading:
    return CB.SourceReading(value=str(value), numeric=value, available=True)


def test_pc_master_missing_control_with_active_watt_is_active():
    result = CB.evaluate_combined(_pc_config(), {"source_watt": _watt(178.0)})

    assert result.state == "active"
    assert result.reason == "pc_active"
    assert result.derived["watt_active"] is True
    assert result.derived["is_active"] is True
    assert result.derived["available"] is True
    assert result.derived["control_available"] is False
    assert result.derived["source_quality"] == "ok"
    assert result.degraded is False


def test_pc_master_missing_control_with_idle_watt_is_off():
    result = CB.evaluate_combined(_pc_config(), {"source_watt": _watt(8.0)})

    assert result.state == "off"
    assert result.reason == "pc_inactive"
    assert result.derived["watt_active"] is False
    assert result.derived["is_active"] is False
    assert result.derived["available"] is True
    assert result.derived["control_available"] is False
    assert result.derived["source_quality"] == "ok"
    assert result.degraded is False


def test_pc_master_without_control_or_watt_is_unknown_fail_safe():
    result = CB.evaluate_combined(_pc_config(), {})

    assert result.state == "unknown"
    assert result.reason == "pc_source_problem"
    assert result.derived["watt_active"] is False
    assert result.derived["is_active"] is False
    assert result.derived["available"] is False
    assert result.derived["control_available"] is False
    assert result.derived["source_quality"] == "problem"
    assert result.degraded is True


def test_pc_master_headline_and_is_active_are_consistent_with_clear_watt_evidence():
    for watt, expected_state, expected_active in (
        (165.0, "active", True),
        (5.0, "off", False),
    ):
        result = CB.evaluate_combined(_pc_config(), {"source_watt": _watt(watt)})
        assert result.state == expected_state
        assert result.derived["is_active"] is expected_active
