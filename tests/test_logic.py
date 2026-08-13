"""Unit-Tests für benni_core_devices.logic.

Deckt R-DC-01..R-DC-09 aus dem Lastenheft `device_core/lastenheft.md` v0.2 ab.
Reine Python-Tests — kein HA nötig.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcd_const as C
import bcd_logic as L


TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 5, 27, 20, 0, tzinfo=TZ)


# ─────────────────────────────────────────────────────────────────────────────
# Hilfsfunktionen
# ─────────────────────────────────────────────────────────────────────────────


def _config(
    *,
    threshold: int = 5,
    sticky: int = 30,
    buckets: tuple[L.WattBucket, ...] = (),
    configured: tuple[str, ...] = ("integration_entity",),
    freshness: int | None = None,
    conflict_hold: int = 0,
) -> L.DeviceConfig:
    return L.DeviceConfig(
        slug="x",
        display_name="X",
        device_type="tv",
        watt_threshold_on=threshold,
        watt_buckets=buckets,
        sticky_hold_seconds=sticky,
        area_id=None,
        configured_slots=configured,
        source_freshness_seconds=freshness,
        source_conflict_hold_seconds=conflict_hold,
    )


def _persisted(
    *,
    last_powered: bool | None = None,
    last_change: datetime | None = None,
    override: L.Override | None = None,
    last_watt_active: datetime | None = None,
    source_conflict_since: datetime | None = None,
) -> L.DevicePersisted:
    return L.DevicePersisted(
        last_powered=last_powered,
        last_powered_change=last_change,
        override=override,
        last_watt_active=last_watt_active,
        source_conflict_since=source_conflict_since,
    )


def _inputs(
    slots: dict[str, L.SlotReading],
    *,
    integration_slot: str | None = "integration_entity",
    state_slot: str | None = "integration_entity",
    watt_slot: str | None = None,
    boot: bool = False,
) -> L.DeviceInputs:
    return L.DeviceInputs(
        slots=slots,
        integration_slot=integration_slot,
        state_slot=state_slot,
        watt_slot=watt_slot,
        boot_phase_active=boot,
    )


def _reading(value: str | None, numeric: float | None = None, age_s: int = 0) -> L.SlotReading:
    return L.SlotReading(
        value=value,
        numeric=numeric,
        attributes={},
        last_updated=NOW - timedelta(seconds=age_s) if value is not None else None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# R-DC-01: Fallback-Hierarchie
# ─────────────────────────────────────────────────────────────────────────────


def test_integration_fresh_wins_for_powered():
    cfg = _config()
    inp = _inputs({"integration_entity": _reading("on")})
    r = L.compute_device(cfg, inp, _persisted(), NOW)
    assert r.powered is True
    assert r.power_source == C.PowerSource.INTEGRATION.value


def test_watt_fallback_when_integration_unavailable():
    cfg = _config(threshold=10, configured=("integration_entity", "watt_sensor"))
    inp = _inputs(
        {
            "integration_entity": _reading(None),
            "watt_sensor": _reading("25.0", numeric=25.0),
        },
        watt_slot="watt_sensor",
    )
    r = L.compute_device(cfg, inp, _persisted(), NOW)
    assert r.powered is True
    assert r.power_source == C.PowerSource.WATT_FALLBACK.value


def test_watt_fallback_below_threshold_is_off():
    cfg = _config(threshold=50, configured=("integration_entity", "watt_sensor"))
    inp = _inputs(
        {
            "integration_entity": _reading(None),
            "watt_sensor": _reading("3.0", numeric=3.0),
        },
        watt_slot="watt_sensor",
    )
    r = L.compute_device(cfg, inp, _persisted(), NOW)
    assert r.powered is False
    assert r.power_source == C.PowerSource.WATT_FALLBACK.value


def test_sticky_hold_when_all_unavailable():
    cfg = _config(sticky=60)
    inp = _inputs({"integration_entity": _reading(None)})
    persisted = _persisted(last_powered=True, last_change=NOW - timedelta(seconds=30))
    r = L.compute_device(cfg, inp, persisted, NOW)
    assert r.powered is True
    assert r.power_source == C.PowerSource.STICKY_HOLD.value


def test_sticky_hold_expired_falls_through():
    cfg = _config(sticky=10)
    inp = _inputs({"integration_entity": _reading(None)})
    persisted = _persisted(last_powered=True, last_change=NOW - timedelta(seconds=60))
    r = L.compute_device(cfg, inp, persisted, NOW)
    assert r.powered is None
    assert r.power_source == C.PowerSource.NONE.value


def test_all_unavailable_no_persistence_is_none():
    cfg = _config()
    inp = _inputs({"integration_entity": _reading(None)})
    r = L.compute_device(cfg, inp, _persisted(), NOW)
    assert r.powered is None
    assert r.power_source == C.PowerSource.NONE.value


# ─────────────────────────────────────────────────────────────────────────────
# R-DC-05: Konflikt Integration vs. Watt
# ─────────────────────────────────────────────────────────────────────────────


def test_integration_off_with_watt_high_flags_disagreement():
    cfg = _config(threshold=50, configured=("integration_entity", "watt_sensor"))
    inp = _inputs(
        {
            "integration_entity": _reading("off"),
            "watt_sensor": _reading("80.0", numeric=80.0),
        },
        watt_slot="watt_sensor",
    )
    r = L.compute_device(cfg, inp, _persisted(), NOW)
    assert r.powered is False
    assert r.power_source == C.PowerSource.INTEGRATION.value
    assert r.watt_disagrees is True


def test_integration_on_no_disagreement():
    cfg = _config(threshold=50, configured=("integration_entity", "watt_sensor"))
    inp = _inputs(
        {
            "integration_entity": _reading("on"),
            "watt_sensor": _reading("80.0", numeric=80.0),
        },
        watt_slot="watt_sensor",
    )
    r = L.compute_device(cfg, inp, _persisted(), NOW)
    assert r.powered is True
    assert r.watt_disagrees is False


# ─────────────────────────────────────────────────────────────────────────────
# R-DC-06: Watt-Buckets → power_state
# ─────────────────────────────────────────────────────────────────────────────


def test_power_state_buckets_operator_order():
    buckets = (
        L.WattBucket(state="off", op="<=", value=5),
        L.WattBucket(state="idle", op="<=", value=30),
        L.WattBucket(state="playing", op=">", value=30),
    )
    assert L.classify_power_state(0.5, buckets) == "off"
    assert L.classify_power_state(5, buckets) == "off"      # <=5
    assert L.classify_power_state(10, buckets) == "idle"
    assert L.classify_power_state(30, buckets) == "idle"    # <=30
    assert L.classify_power_state(100, buckets) == "playing"


def test_power_state_all_operators():
    assert L.classify_power_state(5, (L.WattBucket("x", "<", 10),)) == "x"
    assert L.classify_power_state(10, (L.WattBucket("x", "<", 10),)) == "unknown"
    assert L.classify_power_state(10, (L.WattBucket("x", "<=", 10),)) == "x"
    assert L.classify_power_state(10, (L.WattBucket("x", "=", 10),)) == "x"
    assert L.classify_power_state(11, (L.WattBucket("x", ">", 10),)) == "x"
    assert L.classify_power_state(10, (L.WattBucket("x", ">=", 10),)) == "x"


def test_power_state_catch_all():
    buckets = (
        L.WattBucket(state="off", op="<=", value=5),
        L.WattBucket(state="on", op=None, value=None),  # catch-all
    )
    assert L.classify_power_state(3, buckets) == "off"
    assert L.classify_power_state(999, buckets) == "on"


def test_power_state_without_buckets_is_unknown():
    assert L.classify_power_state(50, ()) == "unknown"


def test_power_state_without_watt_is_unknown():
    buckets = (L.WattBucket(state="off", op="<=", value=5),)
    assert L.classify_power_state(None, buckets) == "unknown"


def test_power_state_no_match_is_unknown():
    # Kein Bucket matcht und kein catch-all → unknown
    buckets = (L.WattBucket(state="off", op="<", value=5),)
    assert L.classify_power_state(100, buckets) == "unknown"


def test_power_state_always_from_watt_even_when_integration_on():
    """LH OQ-3-Auflösung: power_state immer aus Watt, unabhängig von Integration."""
    buckets = (
        L.WattBucket(state="off", op="<=", value=5),
        L.WattBucket(state="idle", op="<=", value=30),
        L.WattBucket(state="playing", op=">", value=30),
    )
    cfg = _config(buckets=buckets, configured=("integration_entity", "watt_sensor"))
    inp = _inputs(
        {
            "integration_entity": _reading("on"),
            "watt_sensor": _reading("2.0", numeric=2.0),
        },
        watt_slot="watt_sensor",
    )
    r = L.compute_device(cfg, inp, _persisted(), NOW)
    # Integration sagt powered=True, aber power_state aus Watt = "off"
    assert r.powered is True
    assert r.power_state == "off"


def test_parse_watt_buckets_keeps_order():
    parsed = L.parse_watt_buckets(
        [
            {"state": "off", "op": "<=", "value": 5},
            {"state": "idle", "op": "<=", "value": 30},
            {"state": "playing", "op": ">", "value": 30},
        ]
    )
    assert [b.state for b in parsed] == ["off", "idle", "playing"]
    assert parsed[0].op == "<=" and parsed[0].value == 5.0


def test_parse_watt_buckets_catch_all_entry():
    parsed = L.parse_watt_buckets(
        [{"state": "off", "op": "<=", "value": 5}, {"state": "on"}]
    )
    assert parsed[1].op is None and parsed[1].value is None


def test_parse_watt_buckets_robust_against_garbage():
    assert L.parse_watt_buckets(None) == ()
    assert L.parse_watt_buckets("not a list") == ()
    assert L.parse_watt_buckets([{"op": "<", "value": 5}]) == ()  # missing state
    assert L.parse_watt_buckets([{"state": "x", "op": "??", "value": 5}]) == ()  # bad op
    assert L.parse_watt_buckets([{"state": "x", "op": "<", "value": "abc"}]) == ()  # bad value


# ─────────────────────────────────────────────────────────────────────────────
# R-DC-07: Override
# ─────────────────────────────────────────────────────────────────────────────


def test_override_overrides_powered_and_source():
    override = L.build_override(powered=True, power_state="playing", expire_seconds=None, now=NOW)
    cfg = _config()
    inp = _inputs({"integration_entity": _reading("off")})
    r = L.compute_device(cfg, inp, _persisted(override=override), NOW)
    assert r.powered is True
    assert r.power_source == C.PowerSource.OVERRIDE.value
    assert r.power_state == "playing"
    assert r.override_active is True


def test_override_with_expiry_is_active_within_window():
    override = L.build_override(powered=False, power_state=None, expire_seconds=120, now=NOW)
    cfg = _config()
    inp = _inputs({"integration_entity": _reading("on")})
    r = L.compute_device(cfg, inp, _persisted(override=override), NOW + timedelta(seconds=60))
    assert r.override_active is True
    assert r.powered is False


def test_override_expired_falls_back_to_normal_logic():
    override = L.build_override(powered=False, power_state=None, expire_seconds=10, now=NOW)
    cfg = _config()
    inp = _inputs({"integration_entity": _reading("on")})
    r = L.compute_device(cfg, inp, _persisted(override=override), NOW + timedelta(seconds=30))
    assert r.override_active is False
    assert r.powered is True  # Integration übernimmt wieder
    assert r.power_source == C.PowerSource.INTEGRATION.value


def test_is_override_expired_helper():
    o = L.Override(powered=True, power_state=None, expires_at=NOW + timedelta(seconds=60))
    assert L.is_override_expired(o, NOW) is False
    assert L.is_override_expired(o, NOW + timedelta(seconds=120)) is True
    perm = L.Override(powered=True, power_state=None, expires_at=None)
    assert L.is_override_expired(perm, NOW + timedelta(days=365)) is False
    assert L.is_override_expired(None, NOW) is True


# ─────────────────────────────────────────────────────────────────────────────
# R-DC-09: Boot-Initial-Phase
# ─────────────────────────────────────────────────────────────────────────────


def test_sticky_hold_disabled_during_boot_phase():
    cfg = _config(sticky=600)
    inp = _inputs({"integration_entity": _reading(None)}, boot=True)
    persisted = _persisted(last_powered=True, last_change=NOW - timedelta(seconds=10))
    r = L.compute_device(cfg, inp, persisted, NOW)
    # In Boot-Phase greift Sticky-Hold NICHT
    assert r.powered is None
    assert r.power_source == C.PowerSource.NONE.value


def test_sticky_hold_works_outside_boot_phase():
    cfg = _config(sticky=600)
    inp = _inputs({"integration_entity": _reading(None)}, boot=False)
    persisted = _persisted(last_powered=True, last_change=NOW - timedelta(seconds=10))
    r = L.compute_device(cfg, inp, persisted, NOW)
    assert r.powered is True
    assert r.power_source == C.PowerSource.STICKY_HOLD.value


def test_is_boot_phase_helper():
    boot_start = NOW
    assert L.is_boot_phase(boot_start, NOW + timedelta(seconds=10)) is True
    assert L.is_boot_phase(boot_start, NOW + timedelta(seconds=C.BOOT_INITIAL_PHASE_SECONDS + 1)) is False


# ─────────────────────────────────────────────────────────────────────────────
# R-DC-03: available
# ─────────────────────────────────────────────────────────────────────────────


def test_available_true_when_any_slot_fresh():
    cfg = _config()
    inp = _inputs(
        {
            "integration_entity": _reading(None),
            "watt_sensor": _reading("10", numeric=10.0),
        },
        watt_slot="watt_sensor",
    )
    r = L.compute_device(cfg, inp, _persisted(), NOW)
    assert r.available is True


def test_available_false_when_all_slots_unavailable():
    cfg = _config()
    inp = _inputs({"integration_entity": _reading(None)})
    r = L.compute_device(cfg, inp, _persisted(), NOW)
    assert r.available is False


# ─────────────────────────────────────────────────────────────────────────────
# R-DC-04: State-Mapping
# ─────────────────────────────────────────────────────────────────────────────


def test_state_for_stateful_uses_raw_value():
    cfg = _config()
    inp = _inputs({"integration_entity": _reading("playing")})
    r = L.compute_device(cfg, inp, _persisted(), NOW)
    assert r.state == "playing"


def test_state_for_stateless_falls_back_to_on_off():
    cfg = _config(configured=("switch_entity",))
    inp = _inputs(
        {"switch_entity": _reading("on")},
        integration_slot="switch_entity",
        state_slot=None,
    )
    r = L.compute_device(cfg, inp, _persisted(), NOW)
    assert r.state == "on"


def test_state_unavailable_when_no_powered():
    cfg = _config()
    inp = _inputs({"integration_entity": _reading(None)})
    r = L.compute_device(cfg, inp, _persisted(), NOW)
    assert r.state == "unavailable"


# ─────────────────────────────────────────────────────────────────────────────
# last_powered_change Update
# ─────────────────────────────────────────────────────────────────────────────


def test_last_powered_change_updates_on_transition():
    cfg = _config()
    inp = _inputs({"integration_entity": _reading("on")})
    persisted = _persisted(last_powered=False, last_change=NOW - timedelta(hours=1))
    r = L.compute_device(cfg, inp, persisted, NOW)
    assert r.last_powered_change == NOW


def test_last_powered_change_persists_when_no_transition():
    prev = NOW - timedelta(hours=1)
    cfg = _config()
    inp = _inputs({"integration_entity": _reading("on")})
    persisted = _persisted(last_powered=True, last_change=prev)
    r = L.compute_device(cfg, inp, persisted, NOW)
    assert r.last_powered_change == prev


# ─────────────────────────────────────────────────────────────────────────────
# device_types Profile sanity
# ─────────────────────────────────────────────────────────────────────────────


def test_all_atomic_classes_have_roles():
    import bcd_device_types as DT

    for spec in DT.ATOMIC_CLASSES.values():
        roles = spec.default_roles or spec.required_roles
        assert roles, f"{spec.atomic_class} ohne Rollen"


def test_integration_roles_in_role_catalog():
    import bcd_device_types as DT

    for spec in DT.ATOMIC_CLASSES.values():
        for role in spec.integration_roles:
            assert role in DT.ROLE_CATALOG, f"{spec.atomic_class}: {role} fehlt"
        if spec.state_role:
            assert spec.state_role in DT.ROLE_CATALOG
        for role in spec.required_roles:
            assert role in DT.ROLE_CATALOG


def test_role_catalog_buckets_and_domains():
    import bcd_device_types as DT

    assert DT.ROLE_CATALOG["primary_state"].bucket == DT.BUCKET_SOURCES
    assert DT.ROLE_CATALOG["power_switch"].bucket == DT.BUCKET_CONTROLS
    assert DT.ROLE_CATALOG["companion_media"].bucket == DT.BUCKET_METADATA
    assert DT.ROLE_CATALOG["open_contact"].domains == ("binary_sensor",)
    assert DT.ROLE_CATALOG["primary_state"].compute_relevant is True


def test_import_validation_atomic_class():
    import bcd_device_types as DT

    assert DT.validate_import_device({"slug": "living_tv", "atomic_class": "media_device"}) is None
    assert DT.validate_import_device({"slug": "x", "atomic_class": "nope"}) is not None
    assert DT.validate_import_device({"slug": "Bad Slug", "atomic_class": "media_device"}) is not None


# ─────────────────────────────────────────────────────────────────────────────
# R-DC-08: Bulk-Import-Validierung
# ─────────────────────────────────────────────────────────────────────────────


def test_is_valid_slug():
    import bcd_device_types as DT

    assert DT.is_valid_slug("living_pc") is True
    assert DT.is_valid_slug("tv2") is True
    assert DT.is_valid_slug("Living PC") is False
    assert DT.is_valid_slug("living-pc") is False
    assert DT.is_valid_slug("") is False


def test_validate_import_device_ok_power():
    import bcd_device_types as DT

    d = {"slug": "kitchen_coffee", "atomic_class": "power_device",
         "sources": [{"role": "primary_state", "entity": "switch.x"}]}
    assert DT.validate_import_device(d) is None


def test_validate_import_device_bad_slug_and_class():
    import bcd_device_types as DT

    assert DT.validate_import_device({"slug": "Bad Slug", "atomic_class": "power_device"}) is not None
    assert DT.validate_import_device({"slug": "x", "atomic_class": "nope"}) is not None
    assert DT.validate_import_device("notadict") is not None


def test_validate_import_payload_all_or_nothing():
    import bcd_device_types as DT

    devices = [
        {"slug": "living_pc", "atomic_class": "power_device"},
        {"slug": "bad_one", "atomic_class": "nonsense"},  # invalid atomic_class
    ]
    valid, errors = DT.validate_import_payload(devices)
    assert errors
    assert any("bad_one" in e for e in errors)


def test_validate_import_payload_duplicate_slug():
    import bcd_device_types as DT

    devices = [
        {"slug": "x", "atomic_class": "power_device"},
        {"slug": "x", "atomic_class": "power_device"},
    ]
    valid, errors = DT.validate_import_payload(devices)
    assert any("doppelter slug" in e for e in errors)


def test_validate_import_payload_normalizes_and_defaults():
    import bcd_device_types as DT

    devices = [{"slug": "living_pc", "atomic_class": "power_device"}]
    valid, errors = DT.validate_import_payload(devices)
    assert not errors
    assert valid[0]["slug"] == "living_pc"
    assert valid[0]["display_name"] == "living_pc"


def test_validate_import_payload_empty_is_error():
    import bcd_device_types as DT

    valid, errors = DT.validate_import_payload([])
    assert errors
    valid, errors = DT.validate_import_payload("nope")
    assert errors


# ─────────────────────────────────────────────────────────────────────────────
# slugify / unique_slug (Single-Hub: slug aus Anzeigename)
# ─────────────────────────────────────────────────────────────────────────────


def test_slugify_basic():
    import bcd_device_types as DT

    assert DT.slugify("Wohnzimmer TV") == "wohnzimmer_tv"
    assert DT.slugify("  Küche  Kaffee ") == "kueche_kaffee"
    assert DT.slugify("PS5 / PlayStation") == "ps5_playstation"
    assert DT.slugify("LED-Stripe #1") == "led_stripe_1"
    assert DT.slugify("Über-Gerät") == "ueber_geraet"


def test_slugify_collapses_separators():
    import bcd_device_types as DT

    assert DT.slugify("a---b   c") == "a_b_c"
    assert DT.slugify("__rand__") == "rand"


def test_unique_slug_appends_counter():
    import bcd_device_types as DT

    existing = {"tv", "tv_2"}
    assert DT.unique_slug("tv", existing) == "tv_3"
    assert DT.unique_slug("pc", existing) == "pc"


# ─────────────────────────────────────────────────────────────────────────────
# Rich-Atomic-Rework: Source-Classifier (LH §5)
# ─────────────────────────────────────────────────────────────────────────────


def test_classify_source_blocks_atomic_and_combined():
    import bcd_device_types as DT

    assert DT.classify_source_entity("binary_sensor.living_open_atomic") == "atomic"
    assert DT.classify_source_entity("sensor.opening_state_combined") == "combined"
    assert DT.classify_source_entity("binary_sensor.x_gate") == "gate"
    # Rohe Quelle ist sauber
    assert DT.classify_source_entity("media_player.living_lgtv") is None
    # Nicht-String / kein Punkt
    assert DT.classify_source_entity(None) is None
    assert DT.classify_source_entity("noentity") is None


def test_classify_source_detects_own_entities():
    import bcd_device_types as DT

    own = ("benni_device_", "benni_combined_")
    assert DT.classify_source_entity("sensor.benni_device_tv", own_prefixes=own) == "own"
    assert DT.classify_source_entity("sensor.benni_combined_opening", own_prefixes=own) == "own"
    assert DT.classify_source_entity("sensor.benni_device_tv") is None  # ohne Präfixe


def test_classify_source_prefers_published_registry_exact_match():
    import bcd_device_types as DT

    own = ("benni_device_", "benni_combined_")
    published = {
        "sensor.benni_device_tv",
        "binary_sensor.benni_combined_opening_blocked",
        "binary_sensor.legacy_gate",
    }

    assert (
        DT.classify_source_entity(
            "sensor.benni_device_tv",
            own_prefixes=own,
            published_outputs=published,
        )
        == "published"
    )
    assert (
        DT.classify_source_entity(
            "binary_sensor.benni_combined_opening_blocked",
            own_prefixes=own,
            published_outputs=published,
        )
        == "published"
    )
    assert (
        DT.classify_source_entity(
            "binary_sensor.legacy_gate",
            own_prefixes=own,
            published_outputs=published,
        )
        == "published"
    )
    assert (
        DT.classify_source_entity(
            "sensor.benni_device_missing",
            own_prefixes=own,
            published_outputs=published,
        )
        == "unpublished"
    )


def test_roles_present_in_catalog():
    import bcd_device_types as DT

    for role in (
        "primary_state", "power_meter", "open_contact", "tilt_contact",
        "temperature_source", "network_presence", "status_source",
        "power_switch", "wake_mac", "companion_media", "current_meter",
    ):
        assert role in DT.ROLE_CATALOG, f"{role} fehlt im Rollenkatalog"


def test_wake_mac_text_role():
    import bcd_device_types as DT

    assert DT.ROLE_CATALOG["wake_mac"].kind == "text"
    assert DT.ROLE_CATALOG["wake_mac"].domains == ()
    assert DT.ROLE_CATALOG["wake_mac"].bucket == DT.BUCKET_CONTROLS


# ─────────────────────────────────────────────────────────────────────────────
# Climate-Typ (nur rohe Wahrheiten, keine comfort/eco-Bewertung)
# ─────────────────────────────────────────────────────────────────────────────


def test_climate_class_spec():
    import bcd_device_types as DT

    spec = DT.ATOMIC_CLASSES["climate_device"]
    assert spec.power_model == "passthrough_state"
    assert "climate_source" in spec.required_roles
    assert set(spec.extra_attributes) == {
        "current_temperature", "target_temperature", "hvac_action", "hvac_mode",
    }
    assert "comfort" not in spec.extra_attributes
    assert "eco" not in spec.extra_attributes


# ─────────────────────────────────────────────────────────────────────────────
# watt_primary: reale Leistung schlägt den Plug-Schalter (Plugs mit Energy-Meter)
# ─────────────────────────────────────────────────────────────────────────────


def _wp_inputs(switch: str | None, watt: float | None):
    """Power-Device-Slots: Plug-Schalter (integration) + Watt-Meter, state_slot=None."""
    slots = {"switch_entity": _reading(switch)}
    if watt is not None:
        slots["watt_sensor"] = _reading(str(watt), numeric=watt)
    else:
        slots["watt_sensor"] = _reading(None)
    return _inputs(
        slots,
        integration_slot="switch_entity",
        state_slot=None,
        watt_slot="watt_sensor",
    )


def test_watt_primary_plug_on_zero_watt_is_off():
    """Der Kaffee-Fall: Plug an, aber 0 W → Gerät ist aus. Normaler Idle-Zustand,
    KEINE Degradierung → watt_disagrees bleibt False."""
    cfg = _config(threshold=5)
    inp = _wp_inputs("on", 0.0)
    r = L.compute_device(cfg, inp, _persisted(), NOW, watt_primary=True)
    assert r.powered is False
    assert r.state == "off"
    assert r.power_source == C.PowerSource.WATT_PRIMARY.value
    assert r.watt_disagrees is False  # Plug an + 0 W = normal, kein Konflikt


def test_watt_primary_plug_off_but_drawing_flags_disagreement():
    """Überraschender Konflikt: Plug meldet AUS, es fließt aber Strom → flaggen."""
    cfg = _config(threshold=5)
    inp = _wp_inputs("off", 80.0)
    r = L.compute_device(cfg, inp, _persisted(), NOW, watt_primary=True)
    assert r.powered is True
    assert r.power_source == C.PowerSource.WATT_PRIMARY.value
    assert r.watt_disagrees is True


def test_watt_primary_plug_on_high_watt_is_on():
    cfg = _config(threshold=5)
    inp = _wp_inputs("on", 170.0)
    r = L.compute_device(cfg, inp, _persisted(), NOW, watt_primary=True)
    assert r.powered is True
    assert r.power_source == C.PowerSource.WATT_PRIMARY.value
    assert r.watt_disagrees is False
    assert r.last_watt_active == NOW


def test_watt_primary_hold_bridges_short_zero_watt_dip():
    """Null-Watt mitten im Zyklus: solange < sticky_hold seit letzter Aktivität,
    bleibt das Gerät on (überbrückt Einweich-/Pausenphasen)."""
    cfg = _config(threshold=5, sticky=60)
    inp = _wp_inputs("on", 0.0)
    persisted = _persisted(
        last_powered=True, last_watt_active=NOW - timedelta(seconds=30)
    )
    r = L.compute_device(cfg, inp, persisted, NOW, watt_primary=True)
    assert r.powered is True
    assert r.power_source == C.PowerSource.STICKY_HOLD.value


def test_watt_primary_hold_expires_after_window():
    cfg = _config(threshold=5, sticky=60)
    inp = _wp_inputs("on", 0.0)
    persisted = _persisted(
        last_powered=True, last_watt_active=NOW - timedelta(seconds=120)
    )
    r = L.compute_device(cfg, inp, persisted, NOW, watt_primary=True)
    assert r.powered is False
    assert r.power_source == C.PowerSource.WATT_PRIMARY.value


def test_watt_primary_falls_back_to_switch_when_meter_stale():
    """Watt-Meter unavailable → Plug-Schalter übernimmt (Fallback)."""
    cfg = _config(threshold=5)
    inp = _wp_inputs("on", None)
    r = L.compute_device(cfg, inp, _persisted(), NOW, watt_primary=True)
    assert r.powered is True
    assert r.power_source == C.PowerSource.INTEGRATION.value


def test_watt_primary_default_off_keeps_integration_first():
    """Ohne watt_primary bleibt die alte Integration-first-Hierarchie bestehen
    (Media/Audio/Console-Klassen)."""
    cfg = _config(threshold=5)
    inp = _wp_inputs("on", 0.0)
    r = L.compute_device(cfg, inp, _persisted(), NOW)  # watt_primary default False
    assert r.powered is True
    assert r.power_source == C.PowerSource.INTEGRATION.value


# ─────────────────────────────────────────────────────────────────────────────
# FLEET-83: assumed_state-Quelle (webOS-TV) → automatisch watt-primär
# ─────────────────────────────────────────────────────────────────────────────


def _media_reading(
    value: str | None, *, assumed: bool = False, updated: datetime = NOW
) -> L.SlotReading:
    """Player-Reading wie ein media_device — integration_slot == state_slot."""
    return L.SlotReading(
        value=value,
        numeric=None,
        attributes={"assumed_state": True} if assumed else {},
        last_updated=updated if value is not None else None,
    )


def _media_inputs(
    player: str | None,
    watt: float | None,
    *,
    assumed: bool,
    updated: datetime = NOW,
    watt_updated: datetime | None = None,
):
    """media_device-Slots: primary_state (Player) == integration- & state-Slot,
    plus optionaler Watt-Meter."""
    slots = {"player": _media_reading(player, assumed=assumed, updated=updated)}
    slots["watt_sensor"] = (
        L.SlotReading(
            value=str(watt), numeric=watt, last_updated=watt_updated or updated
        )
        if watt is not None
        else _reading(None)
    )
    return _inputs(
        slots,
        integration_slot="player",
        state_slot="player",
        watt_slot="watt_sensor",
    )


def test_assumed_player_off_high_watt_is_on():
    """Der TV-Blocker: webOS-Player rät dauerhaft „off", Plug zieht 440 W →
    Atomic muss watt-primär auf powered/on schalten (ohne explizites
    watt_primary, ohne Konflikt-Flag)."""
    cfg = _config(threshold=50)
    inp = _media_inputs("off", 440.0, assumed=True)
    r = L.compute_device(cfg, inp, _persisted(), NOW)  # watt_primary default False!
    assert r.powered is True
    assert r.state == "on"
    assert r.power_source == C.PowerSource.WATT_PRIMARY.value
    assert r.watt_disagrees is False  # assumed-Quelle ratet → kein Fault
    assert r.last_watt_active == NOW


def test_assumed_player_off_zero_watt_is_off():
    """TV wirklich aus (0 W) → off, sauber."""
    cfg = _config(threshold=50)
    inp = _media_inputs("off", 0.0, assumed=True)
    r = L.compute_device(cfg, inp, _persisted(), NOW)
    assert r.powered is False
    assert r.state == "off"
    assert r.power_source == C.PowerSource.WATT_PRIMARY.value


def test_assumed_player_standby_below_threshold_is_off():
    """Netz-Standby ~35 W unter Schwelle (≥50) → NICHT an."""
    cfg = _config(threshold=50)
    inp = _media_inputs("off", 35.0, assumed=True)
    r = L.compute_device(cfg, inp, _persisted(), NOW)
    assert r.powered is False
    assert r.state == "off"


def test_assumed_player_state_uses_watt_bucket_granularity():
    """Mit Watt-Buckets liefert der Haupt-State die Granularität (playing),
    nicht nur on/off — der geratene Player-State wird verworfen."""
    buckets = (
        L.WattBucket(state="off", op="<=", value=50),
        L.WattBucket(state="playing", op=">", value=50),
    )
    cfg = _config(threshold=50, buckets=buckets)
    inp = _media_inputs("off", 440.0, assumed=True)
    r = L.compute_device(cfg, inp, _persisted(), NOW)
    assert r.powered is True
    assert r.power_state == "playing"
    assert r.state == "playing"


def test_assumed_player_hold_bridges_short_watt_dip_while_player_active():
    """Kurzer Watt-Dip beim Umschalten → Halte-Fenster hält den TV on,
    solange die Player-Quelle nicht selbst frisch off meldet."""
    cfg = _config(threshold=50, sticky=60)
    inp = _media_inputs("playing", 0.0, assumed=True)
    persisted = _persisted(
        last_powered=True, last_watt_active=NOW - timedelta(seconds=20)
    )
    r = L.compute_device(cfg, inp, persisted, NOW)
    assert r.powered is True
    assert r.power_source == C.PowerSource.STICKY_HOLD.value


def test_assumed_player_off_zero_watt_does_not_sticky_hold():
    """FLEET-126: TV-Aus darf kein Phantom-on halten, wenn webOS frisch off
    meldet und der Watt-Meter schon bei 0 W ist."""
    cfg = _config(threshold=50, sticky=60)
    inp = _media_inputs("off", 0.0, assumed=True)
    persisted = _persisted(
        last_powered=True, last_watt_active=NOW - timedelta(seconds=20)
    )
    r = L.compute_device(cfg, inp, persisted, NOW)
    assert r.powered is False
    assert r.state == "off"
    assert r.power_source == C.PowerSource.WATT_PRIMARY.value


def test_assumed_player_stale_watt_falls_back_to_player():
    """assumed_state ohne Watt: Integration-Reading wird verworfen (kann nicht
    verifiziert werden) → powered=None, state='unavailable', source='none'.

    Vorher (altes Verhalten): Integration-Reading wurde trotzdem genutzt → TV
    blieb als 'on' hängen wenn webOS einen assumed-state lieferte, obwohl kein
    Plug-Watt-Sensor mehr angeschlossen war (FLEET-83 Anschluss).
    """
    cfg = _config(threshold=50)
    inp = _media_inputs("off", None, assumed=True)
    r = L.compute_device(cfg, inp, _persisted(), NOW)
    assert r.powered is None  # assumed ohne Watt = unzuverlässig → verworfen
    assert r.power_source == C.PowerSource.NONE.value
    assert r.state == "unavailable"


def test_non_assumed_player_keeps_integration_first():
    """Regression: ein NICHT-assumed Player (real lesbar) behält Integration-first
    — watt übersteuert nur bei assumed_state."""
    cfg = _config(threshold=50)
    inp = _media_inputs("off", 440.0, assumed=False)
    r = L.compute_device(cfg, inp, _persisted(), NOW)
    assert r.powered is False
    assert r.power_source == C.PowerSource.INTEGRATION.value
    # Echter Konflikt (real-off + Strom) wird hier sehr wohl geflaggt.
    assert r.watt_disagrees is True


def test_tv_fallback_under_fifty_watt_is_standby_off():
    """TV-Watt-Fallback: 49 W bleiben unter der On-Schwelle."""
    cfg = _config(threshold=50, configured=("player", "watt_sensor"), freshness=20)
    inp = _media_inputs(None, 49.0, assumed=False)
    inp = _inputs(
        inp.slots,
        integration_slot="player",
        state_slot="player",
        watt_slot="watt_sensor",
    )
    r = L.compute_device(cfg, inp, _persisted(), NOW)
    assert r.powered is False
    assert r.power_source == C.PowerSource.WATT_FALLBACK.value


def test_tv_fallback_over_fifty_watt_is_active():
    """TV-Watt-Fallback: frische 82 W aktivieren den TV."""
    cfg = _config(threshold=50, configured=("player", "watt_sensor"), freshness=20)
    inp = _media_inputs(None, 82.0, assumed=False)
    inp = _inputs(
        inp.slots,
        integration_slot="player",
        state_slot="player",
        watt_slot="watt_sensor",
    )
    r = L.compute_device(cfg, inp, _persisted(), NOW)
    assert r.powered is True
    assert r.power_source == C.PowerSource.WATT_FALLBACK.value


def test_tv_off_high_watt_missing_assumed_state_holds_previous_active():
    """Kurzzeitiges WebOS-off bei 82/121 W darf keinen Off-Flap erzeugen."""
    cfg = _config(
        threshold=50,
        configured=("player", "watt_sensor"),
        freshness=20,
        conflict_hold=20,
    )
    first = L.compute_device(
        cfg,
        _media_inputs("off", 82.0, assumed=False),
        _persisted(last_powered=True),
        NOW,
    )
    assert first.powered is True
    assert first.source_conflict_since == NOW
    assert first.watt_disagrees is True

    second = L.compute_device(
        cfg,
        _media_inputs("off", 121.0, assumed=False, updated=NOW + timedelta(seconds=10)),
        _persisted(
            last_powered=True,
            last_change=NOW - timedelta(seconds=60),
            source_conflict_since=first.source_conflict_since,
        ),
        NOW + timedelta(seconds=10),
    )
    assert second.powered is True
    assert second.watt_disagrees is True


def test_tv_fresh_webos_off_wins_after_conflict_window():
    """Ein anhaltend frisches WebOS-off wird nach dem Übergangsfenster off."""
    cfg = _config(
        threshold=50,
        configured=("player", "watt_sensor"),
        freshness=20,
        conflict_hold=20,
    )
    now = NOW + timedelta(seconds=21)
    r = L.compute_device(
        cfg,
        _media_inputs("off", 121.0, assumed=False, updated=now),
        _persisted(
            last_powered=True,
            last_change=NOW - timedelta(seconds=60),
            source_conflict_since=NOW,
        ),
        now,
    )
    assert r.powered is False
    assert r.power_source == C.PowerSource.INTEGRATION.value


def test_tv_stale_webos_off_allows_fresh_watt_fallback_after_window():
    """Stale WebOS-off blockiert einen frischen Watt-Fallback nicht dauerhaft."""
    cfg = _config(
        threshold=50,
        configured=("player", "watt_sensor"),
        freshness=20,
        conflict_hold=20,
    )
    now = NOW + timedelta(seconds=21)
    r = L.compute_device(
        cfg,
        _media_inputs(
            "off", 121.0, assumed=False, updated=NOW, watt_updated=now
        ),
        _persisted(source_conflict_since=NOW),
        now,
    )
    assert r.powered is True
    assert r.power_source == C.PowerSource.WATT_FALLBACK.value


def test_tv_delayed_watt_does_not_override_fresh_webos_off():
    """Ein verspäteter Wattwert darf einen frischen WebOS-off nicht überstimmen."""
    cfg = _config(
        threshold=50,
        configured=("player", "watt_sensor"),
        freshness=20,
        conflict_hold=20,
    )
    now = NOW + timedelta(seconds=21)
    r = L.compute_device(
        cfg,
        _media_inputs(
            "off", 121.0, assumed=False, updated=now,
            watt_updated=NOW,
        ),
        _persisted(last_powered=True, source_conflict_since=NOW),
        now,
    )
    assert r.powered is False
    assert r.power_source == C.PowerSource.INTEGRATION.value
