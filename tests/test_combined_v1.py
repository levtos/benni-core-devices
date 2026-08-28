"""Tests für Combined v1.0 (Expression-Engine + Nodes + Validierung)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcd_combined as CB
import bcd_combined_expr as E

# ── Expression-Engine ────────────────────────────────────────────────────────


def test_expr_arithmetic_none_div0():
    assert E.eval_expr("${a} + ${b}", {"a": 2, "b": 3}) == 5
    assert E.eval_expr("${a} / ${b}", {"a": 1, "b": 0}) is None       # div0 → None
    assert E.eval_expr("${a} + ${b}", {"a": None, "b": 3}) is None    # None-Propagation


def test_expr_functions():
    assert E.eval_expr("clamp(${x}, 0, 10)", {"x": 15}) == 10
    assert E.eval_expr("round(${x}, 1)", {"x": 3.14159}) == 3.1
    assert E.eval_expr("min(${a}, ${b}, ${c})", {"a": 3, "b": 1, "c": 2}) == 1
    assert E.eval_expr("max(${a}, ${b})", {"a": 3, "b": 7}) == 7
    assert E.eval_expr("clamp(${x}, 0, ${hi})", {"x": 5, "hi": None}) is None


def test_gate_trees():
    assert E.eval_expr("any([${a}, ${b}])", {"a": "off", "b": "on"}) is True
    assert E.eval_expr("all([${a}, ${b}])", {"a": "on", "b": "off"}) is False
    assert E.eval_expr("not(${a})", {"a": "off"}) is True
    assert E.eval_expr("${a} and ${b}", {"a": True, "b": False}) is False
    assert E.eval_expr("any([${a}, ${b}])", {"a": None, "b": "on"}) is None  # None-Prop
    assert E.eval_expr("${t} > 25", {"t": 26}) is True


def test_expr_string_compare():
    assert E.eval_expr('${m} == "tv"', {"m": "tv"}) is True
    assert E.eval_expr('${m} == "tv"', {"m": "appletv"}) is False


def test_expr_null_compare():
    assert E.eval_expr("${m} == null", {"m": None}) is True
    assert E.eval_expr("${m} != null", {"m": None}) is False
    assert E.eval_expr("${m} == null", {"m": "off"}) is False
    assert E.eval_expr("any([${m}, true])", {"m": None}) is None


def test_dew_point_formula():
    v = E.eval_expr("${t} - (100 - ${rh}) / 5", {"t": 22.0, "rh": 60.0})
    assert abs(v - 14.0) < 1e-9


def test_expr_parse_error():
    import pytest
    with pytest.raises(E.ExprError):
        E.parse("${a} +")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _src(key, role="custom", entity="x"):
    return CB.CombinedSource(key=key, role=role, entity=entity)


def _r(value, numeric=None, available=True, attrs=None, last_updated=None):
    return CB.SourceReading(
        value=value,
        numeric=numeric,
        available=available,
        attributes=attrs or {},
        last_updated=last_updated,
    )


def _cfg(**kw):
    base = dict(slug="c", display_name="C")
    base.update(kw)
    return CB.CombinedConfig(**base)


# ── gate node ────────────────────────────────────────────────────────────────


def test_gate_node_unsafe_for_climate():
    cfg = _cfg(
        output_type="boolean",
        sources=(_src("open_a", "open_contact"), _src("tilt_a", "tilt_contact")),
        derived_values=(
            CB.DerivedValue(name="any_open", kind="gate", expr="any([${open_a}])"),
            CB.DerivedValue(name="any_tilt", kind="gate", expr="any([${tilt_a}])"),
            CB.DerivedValue(name="unsafe", kind="gate", expr="any([${any_open}, ${any_tilt}])"),
        ),
        rules=(CB.CombinedRule(source="unsafe", op="eq", value="on", output=True),),
        default_output=False,
    )
    res = CB.evaluate_combined(cfg, {"open_a": _r("on"), "tilt_a": _r("off")})
    assert res.derived["unsafe"] is True
    assert res.state == "on"


# ── expr node (number output via ${ref}) ─────────────────────────────────────


def test_expr_node_number_output():
    cfg = _cfg(
        output_type="number",
        sources=(_src("t"), _src("rh")),
        derived_values=(CB.DerivedValue(name="dew", kind="expr", expr="round(${t} - (100 - ${rh})/5, 1)"),),
        rules=(),
        default_output="${dew}",
    )
    res = CB.evaluate_combined(cfg, {"t": _r("22.0", 22.0), "rh": _r("60", 60.0)})
    assert res.state == "14"


# ── enum node (string output for flat attributes) ────────────────────────────


def test_enum_node_ordered_cases_and_exposed_attribute():
    cfg = _cfg(
        output_type="enum",
        sources=(_src("open_a", "open_contact"), _src("tilt_a", "tilt_contact")),
        derived_values=(
            CB.DerivedValue(
                name="window_a",
                kind="enum",
                cases=(
                    CB.DerivedCase(when='${open_a} == null or ${tilt_a} == null', output="stale"),
                    CB.DerivedCase(when='${open_a} == "on"', output="open"),
                    CB.DerivedCase(when='${tilt_a} == "on"', output="tilted"),
                ),
                default="closed",
                expose=True,
            ),
        ),
        default_output="${window_a}",
    )

    assert CB.evaluate_combined(cfg, {"open_a": _r("on"), "tilt_a": _r("on")}).state == "open"
    assert CB.evaluate_combined(cfg, {"open_a": _r("off"), "tilt_a": _r("on")}).state == "tilted"
    stale = CB.evaluate_combined(cfg, {"open_a": _r(None, available=False), "tilt_a": _r("off")})
    assert stale.state == "stale"
    assert CB.exposed_derived_attributes(cfg, stale) == {"window_a": "stale"}


# ── health node ──────────────────────────────────────────────────────────────


def test_health_node():
    cfg = _cfg(
        output_type="enum",
        sources=(_src("a"), _src("b")),
        derived_values=(CB.DerivedValue(name="h", kind="health", atomics=("a", "b")),),
        default_output="${h}",
    )
    ok = CB.evaluate_combined(cfg, {
        "a": _r("on", attrs={"atomic_quality": "ok"}),
        "b": _r("on", attrs={"atomic_quality": "degraded"}),
    })
    assert ok.state == "degraded"
    prob = CB.evaluate_combined(cfg, {
        "a": _r("on", attrs={"atomic_quality": "ok"}),
        "b": _r(None, available=False),
    })
    assert prob.state == "problem"


def test_health_node_skips_unavailable_optional_source():
    cfg = _cfg(
        output_type="enum",
        sources=(
            CB.CombinedSource(key="control", role="control", entity="switch.optional", required=False),
            _src("watt"),
        ),
        derived_values=(
            CB.DerivedValue(name="h", kind="health", atomics=("control", "watt")),
        ),
        default_output="${h}",
    )

    result = CB.evaluate_combined(
        cfg,
        {
            "control": _r(None, available=False),
            "watt": _r("75", 75.0),
        },
    )

    assert result.state == "ok"
    assert result.degraded is False
    assert result.degraded_reason == []


def test_health_node_keeps_unavailable_required_source_problematic():
    cfg = _cfg(
        output_type="enum",
        sources=(_src("control"), _src("watt")),
        derived_values=(
            CB.DerivedValue(name="h", kind="health", atomics=("control", "watt")),
        ),
        default_output="${h}",
    )

    result = CB.evaluate_combined(
        cfg,
        {
            "control": _r(None, available=False),
            "watt": _r("75", 75.0),
        },
    )

    assert result.state == "problem"
    assert result.degraded is True


# ── latch (Schmitt-Hysterese + hold + boot fail_safe) ────────────────────────


def test_latch_hysteresis_hold_and_boot():
    cfg = _cfg(
        output_type="boolean",
        sources=(_src("lux"),),
        derived_values=(CB.DerivedValue(name="dark", kind="latch",
                                        set_expr="${lux} < 50", reset_expr="${lux} >= 100",
                                        fail_safe="off"),),
        default_output="${dark}",
    )
    r1 = CB.evaluate_combined(cfg, {"lux": _r("30", 30.0)})
    assert r1.state == "on"
    p1 = CB.CombinedPersisted(last_state=r1.state, node_states=r1.node_states)
    r2 = CB.evaluate_combined(cfg, {"lux": _r("70", 70.0)}, persisted=p1)
    assert r2.state == "on"  # hält zwischen den Schwellen
    p2 = CB.CombinedPersisted(last_state=r2.state, node_states=r2.node_states)
    r3 = CB.evaluate_combined(cfg, {"lux": _r("120", 120.0)}, persisted=p2)
    assert r3.state == "off"
    # Boot ohne Persistenz, zwischen den Schwellen → fail_safe off
    r0 = CB.evaluate_combined(cfg, {"lux": _r("70", 70.0)})
    assert r0.state == "off"


# ── previous (${self}) über simulierten Restart ──────────────────────────────


def test_previous_self_sticky():
    cfg = _cfg(
        output_type="enum",
        sources=(_src("trigger"),),
        rules=(
            CB.CombinedRule(source="trigger", op="eq", value="on", output="active"),
            CB.CombinedRule(source="self", op="ne", value="", output="${self}"),
        ),
        default_output="idle",
    )
    r1 = CB.evaluate_combined(cfg, {"trigger": _r("on")})
    assert r1.state == "active"
    # Restart-Simulation: persisted last_state weiterreichen, trigger off
    p = CB.CombinedPersisted(last_state="active", node_states=r1.node_states)
    r2 = CB.evaluate_combined(cfg, {"trigger": _r("off")}, persisted=p)
    assert r2.state == "active"


# ── TV power source arbitration (Issue #21 / Core Devices #41) ──────────────


def _tv_power_config():
    raw = {
        "display_name": "TV",
        "output_type": "enum",
        "sources": [
            {"key": "state", "role": "tv_player", "entity": "media_player.tv"},
            {"key": "source_watt", "role": "tv_watt", "entity": "sensor.tv_watt"},
            {
                "key": "source_assumed_state",
                "role": "tv_assumed_state",
                "entity": "media_player.tv",
                "attribute": "assumed_state",
            },
        ],
        "derived_values": [
            {
                "name": "tv_power_source",
                "kind": "power_arbitration",
                "state_source": "state",
                "watt_source": "source_watt",
                "assumed_state_source": "source_assumed_state",
                "active_states": ["on", "playing"],
                "threshold": 50,
                "freshness_seconds": 20,
                "conflict_hold_seconds": 20,
                "expose": True,
            },
            {
                "name": "is_powered",
                "kind": "gate",
                "expr": (
                    '${tv_power_source} == "webos" or '
                    '${tv_power_source} == "watt_fallback" or '
                    '${tv_power_source} == "conflict_hold"'
                ),
                "expose": True,
            },
            {
                "name": "tv_candidate",
                "kind": "gate",
                "expr": '${tv_power_source} == "tv_candidate"',
                "expose": True,
            },
            {
                "name": "media_context",
                "kind": "enum",
                "cases": [{"when": "${is_powered}", "output": "tv"}],
                "default": "idle",
                "expose": True,
            },
        ],
        "default_output": "${tv_power_source}",
    }
    config = CB.parse_combined("tv", raw)
    assert config is not None
    assert CB.validate_combined_v1(config) == []
    return config


def _tv_readings(state, watt, assumed, stamp, *, watt_stamp=None):
    return {
        "state": _r(state, available=state is not None, last_updated=stamp),
        "source_watt": _r(
            watt,
            numeric=float(watt) if watt is not None else None,
            available=watt is not None,
            last_updated=watt_stamp or stamp,
        ),
        "source_assumed_state": _r(
            assumed,
            available=assumed is not None,
            last_updated=stamp,
        ),
    }


def test_tv_power_arbitration_standby_below_fifty_watt_is_off():
    now = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
    result = CB.evaluate_combined(
        _tv_power_config(),
        _tv_readings("off", 33, False, now),
        now=now,
    )
    assert result.derived["tv_power_source"] == "webos_off"
    assert result.derived["is_powered"] is False


def test_tv_power_arbitration_unknown_player_uses_fresh_watt_fallback_over_fifty():
    now = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
    result = CB.evaluate_combined(
        _tv_power_config(),
        _tv_readings(None, 121, None, now),
        now=now,
    )
    assert result.derived["tv_power_source"] == "watt_fallback"
    assert result.derived["is_powered"] is True


def test_tv_power_arbitration_cold_start_off_high_watt_is_only_a_candidate():
    now = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
    result = CB.evaluate_combined(
        _tv_power_config(),
        _tv_readings("off", 121, None, now),
        now=now,
    )
    assert result.derived["tv_power_source"] == "tv_candidate"
    assert result.derived["tv_candidate"] is True
    assert result.derived["is_powered"] is False
    assert result.derived["media_context"] == "idle"

    persisted = CB.CombinedPersisted(last_state=result.state, node_states=result.node_states)
    after_window = now + timedelta(seconds=20)
    confirmed = CB.evaluate_combined(
        _tv_power_config(),
        _tv_readings("off", 121, None, after_window),
        persisted=persisted,
        now=after_window,
    )
    assert confirmed.derived["tv_power_source"] == "watt_fallback"
    assert confirmed.derived["tv_candidate"] is False
    assert confirmed.derived["is_powered"] is True
    assert confirmed.derived["media_context"] == "tv"


def test_tv_power_arbitration_candidate_is_discarded_below_fifty_watt():
    now = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
    candidate = CB.evaluate_combined(
        _tv_power_config(),
        _tv_readings("off", 121, None, now),
        now=now,
    )
    persisted = CB.CombinedPersisted(
        last_state=candidate.state,
        node_states=candidate.node_states,
    )
    result = CB.evaluate_combined(
        _tv_power_config(),
        _tv_readings("off", 33, None, now + timedelta(seconds=1)),
        persisted=persisted,
        now=now + timedelta(seconds=1),
    )
    assert result.derived["tv_power_source"] == "webos_off"
    assert result.derived["tv_candidate"] is False
    assert result.derived["is_powered"] is False


def test_tv_power_arbitration_holds_confirmed_active_during_webos_off_flap():
    config = _tv_power_config()
    t0 = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
    active = CB.evaluate_combined(config, _tv_readings("on", 121, None, t0), now=t0)
    assert active.derived["tv_power_source"] == "webos"
    assert active.derived["is_powered"] is True
    persisted = CB.CombinedPersisted(
        last_state=active.state,
        node_states=active.node_states,
    )

    t1 = t0 + timedelta(seconds=1)
    flap = CB.evaluate_combined(
        config,
        _tv_readings("off", 121, None, t1),
        persisted=persisted,
        now=t1,
    )
    assert flap.derived["tv_power_source"] == "conflict_hold"
    assert flap.derived["is_powered"] is True

    persisted = CB.CombinedPersisted(
        last_state=flap.state,
        node_states=flap.node_states,
    )
    after_window = t1 + timedelta(seconds=21)
    off = CB.evaluate_combined(
        config,
        _tv_readings("off", 121, None, t1, watt_stamp=after_window),
        persisted=persisted,
        now=after_window,
    )
    assert off.derived["tv_power_source"] == "webos_off"
    assert off.derived["is_powered"] is False

    persisted = CB.CombinedPersisted(last_state=off.state, node_states=off.node_states)
    later = after_window + timedelta(seconds=21)
    stable_off = CB.evaluate_combined(
        config,
        _tv_readings("off", 121, None, t1, watt_stamp=later),
        persisted=persisted,
        now=later,
    )
    assert stable_off.derived["tv_power_source"] == "webos_off"
    assert stable_off.derived["is_powered"] is False


def test_tv_power_arbitration_stale_or_missing_watt_never_overrides_webos_off():
    config = _tv_power_config()
    t0 = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
    active = CB.evaluate_combined(config, _tv_readings("on", 121, None, t0), now=t0)
    persisted = CB.CombinedPersisted(
        last_state=active.state,
        node_states=active.node_states,
    )
    t1 = t0 + timedelta(seconds=1)

    stale = CB.evaluate_combined(
        config,
        _tv_readings("off", 121, None, t1, watt_stamp=t0 - timedelta(seconds=21)),
        persisted=persisted,
        now=t1,
    )
    missing = CB.evaluate_combined(
        config,
        _tv_readings("off", None, None, t1),
        persisted=persisted,
        now=t1,
    )
    assert stale.derived["tv_power_source"] == "webos_off"
    assert missing.derived["tv_power_source"] == "webos_off"


def test_tv_power_arbitration_stale_webos_off_allows_fresh_watt_fallback():
    now = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
    stale_webos = now - timedelta(seconds=21)
    result = CB.evaluate_combined(
        _tv_power_config(),
        _tv_readings("off", 121, None, stale_webos, watt_stamp=now),
        now=now,
    )
    assert result.derived["tv_power_source"] == "watt_fallback"
    assert result.derived["is_powered"] is True


def test_tv_power_arbitration_stale_webos_active_with_fresh_standby_watt_is_off():
    now = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
    stale_webos = now - timedelta(seconds=21)
    for state in ("on", "playing"):
        result = CB.evaluate_combined(
            _tv_power_config(),
            _tv_readings(state, 0, None, stale_webos, watt_stamp=now),
            now=now,
        )
        assert result.derived["tv_power_source"] == "webos_off"
        assert result.derived["is_powered"] is False


# ── Validierung (Dry-Run) ────────────────────────────────────────────────────


def test_validate_rejects_cycle():
    cfg = _cfg(
        sources=(_src("a"),),
        derived_values=(
            CB.DerivedValue(name="x", kind="gate", expr="${y}"),
            CB.DerivedValue(name="y", kind="gate", expr="${x}"),
        ),
    )
    assert any("Zyklus" in e for e in CB.validate_combined_v1(cfg))


def test_validate_rejects_unknown_ref_and_parse():
    cfg = _cfg(
        sources=(_src("a"),),
        derived_values=(
            CB.DerivedValue(name="x", kind="gate", expr="${nope}"),
            CB.DerivedValue(name="y", kind="expr", expr="${a} +"),
        ),
    )
    errs = CB.validate_combined_v1(cfg)
    assert any("nope" in e for e in errs)
    assert any("Parse" in e for e in errs)


def test_validate_enum_cases():
    unknown = _cfg(
        sources=(_src("a"),),
        derived_values=(CB.DerivedValue(
            name="mode",
            kind="enum",
            cases=(CB.DerivedCase(when="${nope}", output="bad"),),
        ),),
    )
    empty = _cfg(
        sources=(_src("a"),),
        derived_values=(CB.DerivedValue(name="mode", kind="enum"),),
    )

    assert any("nope" in e for e in CB.validate_combined_v1(unknown))
    assert any("enum braucht cases" in e for e in CB.validate_combined_v1(empty))


def test_validate_rejects_time_latch():
    cfg = _cfg(
        sources=(_src("a"),),
        derived_values=(CB.DerivedValue(name="l", kind="latch",
                                        set_expr="${a}", reset_expr="since(${a}) >= 3600"),),
    )
    errs = CB.validate_combined_v1(cfg)
    # Klare v1.1-Meldung, KEIN kryptischer Parse-Fehler
    assert any("v1.1" in e and "l.reset" in e for e in errs)
    assert not any("Parse" in e for e in errs)


def test_validate_clean_config_no_errors():
    cfg = _cfg(
        sources=(_src("open_a", "open_contact"),),
        derived_values=(CB.DerivedValue(name="any_open", kind="gate", expr="any([${open_a}])"),),
    )
    assert CB.validate_combined_v1(cfg) == []


def test_exposed_derived_attributes_are_flat_and_explicit():
    cfg = _cfg(
        sources=(_src("open_a", "open_contact"), _src("tilt_a", "tilt_contact")),
        derived_values=(
            CB.DerivedValue(name="any_open", kind="gate", expr="any([${open_a}])", expose=True),
            CB.DerivedValue(name="any_tilted", kind="gate", expr="any([${tilt_a}])"),
        ),
        exposed_attributes=("any_tilted",),
    )

    res = CB.evaluate_combined(cfg, {"open_a": _r("off"), "tilt_a": _r("on")})

    assert CB.exposed_derived_names(cfg) == ("any_tilted", "any_open")
    assert CB.exposed_derived_attributes(cfg, res) == {
        "any_tilted": True,
        "any_open": False,
    }


def test_combined_master_can_publish_entity_attribute_sources():
    raw = {
        "display_name": "PS5",
        "output_type": "enum",
        "sources": [
            {"key": "state", "role": "ps5_device", "entity": "sensor.benni_device_ps5"},
            {"key": "source_powered", "role": "ps5_powered", "entity": "sensor.benni_device_ps5", "attribute": "powered"},
            {"key": "source_watt", "role": "ps5_watt", "entity": "sensor.benni_device_ps5", "attribute": "watt"},
            {"key": "source_title", "role": "ps5_title", "entity": "sensor.benni_device_ps5", "attribute": "title"},
            {"key": "source_media_context", "role": "media_context", "entity": "sensor.benni_combined_media_context"},
        ],
        "derived_values": [
            {"name": "is_powered", "kind": "gate", "expr": "${source_powered}", "expose": True},
            {"name": "watt", "kind": "expr", "expr": "${source_watt}", "expose": True},
            {"name": "title", "kind": "enum", "default": "${source_title}", "expose": True},
            {"name": "media_context", "kind": "enum", "default": "${source_media_context}", "expose": True},
        ],
        "default_output": "${state}",
        "default_reason": "ps5_device_state",
    }

    cfg = CB.parse_combined("ps5", raw)
    assert cfg is not None
    assert cfg.sources[1].attribute == "powered"
    assert CB.validate_combined_v1(cfg) == []

    res = CB.evaluate_combined(
        cfg,
        {
            "state": _r("playing"),
            "source_powered": _r(True, numeric=1.0),
            "source_watt": _r(42.4, numeric=42.4),
            "source_title": _r("Astro Bot"),
            "source_media_context": _r("gaming"),
        },
    )

    assert res.state == "playing"
    assert res.source_attributes == {
        "source_powered": "powered",
        "source_watt": "watt",
        "source_title": "title",
    }
    assert CB.exposed_derived_attributes(cfg, res) == {
        "is_powered": True,
        "watt": 42.4,
        "title": "Astro Bot",
        "media_context": "gaming",
    }


def test_living_rollo_master_contract_exposes_raw_context_cover_and_plug_facts():
    raw = {
        "display_name": "Living Blind",
        "output_type": "enum",
        "sources": [
            {"key": "cover_state", "role": "cover_state", "entity": "cover.wohnbereich_thermo_verdunklungsrollo"},
            {
                "key": "current_position",
                "role": "cover_position",
                "entity": "cover.wohnbereich_thermo_verdunklungsrollo",
                "attribute": "current_position",
            },
            {"key": "battery", "role": "cover_battery", "entity": "sensor.wohnbereich_thermo_verdunklungsrollo_battery"},
            {"key": "charging", "role": "cover_charging", "entity": "binary_sensor.wohnbereich_thermo_verdunklungsrollo_charging_status"},
            {"key": "running", "role": "cover_running", "entity": "binary_sensor.wohnbereich_thermo_verdunklungsrollo_running"},
            {"key": "living_left_open", "role": "opening_contact", "entity": "binary_sensor.living_window_left_open_contact"},
            {"key": "living_left_tilt", "role": "tilt_contact", "entity": "binary_sensor.living_window_left_tilt_contact"},
            {"key": "living_right_open", "role": "opening_contact", "entity": "binary_sensor.living_window_right_open_contact"},
            {"key": "living_right_tilt", "role": "tilt_contact", "entity": "binary_sensor.living_window_right_tilt_contact"},
            {"key": "source_bio_state", "role": "policy_bio_state", "entity": "sensor.benni_core_state_bio_state"},
            {"key": "source_day_state", "role": "policy_day_state", "entity": "sensor.benni_core_state_day_state"},
            {"key": "source_day_context", "role": "policy_day_context", "entity": "sensor.benni_core_state_day_context"},
            {"key": "source_presence_household", "role": "policy_presence_household", "entity": "sensor.benni_core_state_presence_household"},
            {"key": "source_lux", "role": "policy_lux", "entity": "sensor.garden_light_sensor_illuminance"},
            {"key": "source_sun_state", "role": "sun_state", "entity": "sun.sun"},
            {"key": "source_sun_elevation", "role": "policy_sun_elevation", "entity": "sun.sun", "attribute": "elevation"},
            {"key": "source_media_scenario", "role": "policy_media_scenario", "entity": "sensor.benni_media_state_media_context"},
            {"key": "source_gaming_source", "role": "policy_gaming_source", "entity": "sensor.benni_media_state_gaming_source"},
            {"key": "source_weather_condition", "role": "policy_weather_condition", "entity": "weather.dwd_home"},
            {"key": "source_outdoor_temp", "role": "policy_outdoor_temp", "entity": "sensor.climate_effective_outdoor_temperature"},
            {"key": "plug_switch", "role": "charger_switch", "entity": "switch.living_blind_plug"},
            {"key": "plug_decision", "role": "plug_policy_decision", "entity": "sensor.rollo_lader_rollo_lader_decision"},
            {
                "key": "plug_desired",
                "role": "plug_policy_desired",
                "entity": "sensor.rollo_lader_rollo_lader_decision",
                "attribute": "desired_switch_state",
            },
        ],
        "derived_values": [
            {"name": "cover_entity_id", "kind": "enum", "default": "cover.wohnbereich_thermo_verdunklungsrollo", "expose": True},
            {"name": "current_cover_position", "kind": "expr", "expr": "${current_position}", "expose": True},
            {
                "name": "opening_state",
                "kind": "enum",
                "cases": [
                    {"when": "${living_left_open} == \"on\" or ${living_right_open} == \"on\"", "output": "open"},
                    {"when": "${living_left_tilt} == \"on\" or ${living_right_tilt} == \"on\"", "output": "tilted"},
                ],
                "default": "closed",
                "expose": True,
            },
            {"name": "window_open", "kind": "gate", "expr": "${opening_state} == \"open\"", "expose": True},
            {"name": "bio_state", "kind": "enum", "default": "${source_bio_state}", "expose": True},
            {"name": "day_state", "kind": "enum", "default": "${source_day_state}", "expose": True},
            {"name": "day_context", "kind": "enum", "default": "${source_day_context}", "expose": True},
            {"name": "presence_household", "kind": "enum", "default": "${source_presence_household}", "expose": True},
            {"name": "lux", "kind": "expr", "expr": "${source_lux}", "expose": True},
            {"name": "sun_elevation", "kind": "expr", "expr": "${source_sun_elevation}", "expose": True},
            {"name": "sun_state", "kind": "enum", "default": "${source_sun_state}", "expose": True},
            {"name": "media_scenario", "kind": "enum", "default": "${source_media_scenario}", "expose": True},
            {"name": "gaming_source", "kind": "enum", "default": "${source_gaming_source}", "expose": True},
            {"name": "weather_condition", "kind": "enum", "default": "${source_weather_condition}", "expose": True},
            {"name": "outdoor_temp", "kind": "expr", "expr": "${source_outdoor_temp}", "expose": True},
            {
                "name": "heat_candidate",
                "kind": "gate",
                "expr": "${source_weather_condition} == \"sunny\" and ${source_outdoor_temp} != null and ${source_outdoor_temp} >= 24 and ${source_sun_elevation} != null and ${source_sun_elevation} > 5 and any([${source_day_state} == \"late_morning\", ${source_day_state} == \"forenoon\", ${source_day_state} == \"afternoon\"])",
                "expose": True,
            },
            {"name": "battery_pct", "kind": "expr", "expr": "${battery}", "expose": True},
            {"name": "charging_active", "kind": "gate", "expr": "${charging} == \"on\"", "expose": True},
            {"name": "cover_running", "kind": "gate", "expr": "${running} == \"on\" or ${cover_state} == \"opening\" or ${cover_state} == \"closing\"", "expose": True},
            {"name": "plug_switch_on", "kind": "gate", "expr": "${plug_switch} == \"on\"", "expose": True},
            {"name": "plug_policy_decision", "kind": "enum", "default": "${plug_decision}", "expose": True},
            {"name": "plug_desired_switch_state", "kind": "enum", "default": "${plug_desired}", "expose": True},
        ],
        "rules": [
            {"source": "cover_state", "op": "unavailable", "output": "blocked", "reason": "cover_unavailable"},
            {"source": "opening_state", "op": "eq", "value": "open", "output": "window_open", "reason": "living_window_open"},
        ],
        "default_output": "ready",
        "default_reason": "blind_master_ready",
    }

    cfg = CB.parse_combined("living_rollo", raw)
    assert cfg is not None
    assert CB.validate_combined_v1(cfg) == []

    res = CB.evaluate_combined(
        cfg,
        {
            "cover_state": _r("open"),
            "current_position": _r(60, numeric=60.0),
            "battery": _r("83", numeric=83.0),
            "charging": _r("on"),
            "running": _r("off"),
            "living_left_open": _r("off"),
            "living_left_tilt": _r("off"),
            "living_right_open": _r("off"),
            "living_right_tilt": _r("off"),
            "source_bio_state": _r("awake"),
            "source_day_state": _r("afternoon"),
            "source_day_context": _r("werktag"),
            "source_presence_household": _r("nicht_leer"),
            "source_lux": _r("25000", numeric=25000.0),
            "source_sun_state": _r("above_horizon"),
            "source_sun_elevation": _r("18", numeric=18.0),
            "source_media_scenario": _r("idle"),
            "source_gaming_source": _r("none"),
            "source_weather_condition": _r("sunny"),
            "source_outdoor_temp": _r("26", numeric=26.0),
            "plug_switch": _r("on"),
            "plug_decision": _r("keep"),
            "plug_desired": _r("off"),
        },
    )

    assert res.state == "ready"
    assert CB.exposed_derived_attributes(cfg, res) == {
        "cover_entity_id": "cover.wohnbereich_thermo_verdunklungsrollo",
        "current_cover_position": 60.0,
        "opening_state": "closed",
        "window_open": False,
        "bio_state": "awake",
        "day_state": "afternoon",
        "day_context": "werktag",
        "presence_household": "nicht_leer",
        "lux": 25000.0,
        "sun_elevation": 18.0,
        "sun_state": "above_horizon",
        "media_scenario": "idle",
        "gaming_source": "none",
        "weather_condition": "sunny",
        "outdoor_temp": 26.0,
        "heat_candidate": True,
        "battery_pct": 83.0,
        "charging_active": True,
        "cover_running": False,
        "plug_switch_on": True,
        "plug_policy_decision": "keep",
        "plug_desired_switch_state": "off",
    }


def test_exposed_derived_attributes_validate_names_and_reserved_keys():
    unknown = _cfg(
        sources=(_src("a"),),
        derived_values=(CB.DerivedValue(name="ok", kind="gate", expr="${a}"),),
        exposed_attributes=("missing",),
    )
    reserved = _cfg(
        sources=(_src("a"),),
        derived_values=(CB.DerivedValue(name="reason", kind="gate", expr="${a}", expose=True),),
    )

    assert any("unbekannter derived_values-Knoten 'missing'" in e for e in CB.validate_combined_v1(unknown))
    assert any("reserviertem Sensor-Attribut" in e for e in CB.validate_combined_v1(reserved))


# ── Parsing ──────────────────────────────────────────────────────────────────


def test_parse_derived_values_and_fail_safe():
    raw = {
        "display_name": "X", "output_type": "boolean",
        "sources": [{"key": "a", "role": "custom", "entity": "binary_sensor.a"}],
        "derived_values": [
            {"name": "g", "kind": "gate", "expr": "any([${a}])"},
            {"name": "e", "kind": "enum", "cases": [{"when": '${a} == "on"', "output": "active"}], "default": "idle"},
            {"name": "l", "kind": "latch", "set": "${a}", "reset": "not(${a})", "fail_safe": "off", "expose": True},
            {"name": "bad", "kind": "nope"},  # ungültig → übersprungen
        ],
        "exposed_attributes": ["g"],
        "fail_safe": "hold_last",
    }
    cfg = CB.parse_combined("x", raw)
    assert cfg.fail_safe == "hold_last"
    assert len(cfg.derived_values) == 3
    assert cfg.derived_values[0].kind == "gate"
    assert cfg.derived_values[1].kind == "enum"
    assert cfg.derived_values[1].cases[0].output == "active"
    assert cfg.derived_values[1].default == "idle"
    assert cfg.derived_values[2].set_expr == "${a}"
    assert cfg.derived_values[2].expose is True
    assert cfg.exposed_attributes == ("g",)
