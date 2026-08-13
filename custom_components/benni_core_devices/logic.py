"""Reine Compute-Logik für Benni Core · Devices (LH device_core v0.2).

Keine HA-Imports — vollständig in pytest testbar.

Verteilung:
- `logic.py` (hier): pure Funktionen — (config, inputs, persisted, now) → result
- `coordinator.py`: HA-Integration, Storage, Event-Listener
- `sensor.py`/`binary_sensor.py`: Mapping auf Entities

Regeln R-DC-01..R-DC-09 aus dem Lastenheft.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .const import (
    AVAILABILITY_FRESHNESS_SECONDS,
    BOOT_INITIAL_PHASE_SECONDS,
    DEFAULT_STICKY_HOLD_SECONDS,
    DEFAULT_WATT_THRESHOLD_ON,
    FAIL_SAFE_HOLD_LAST,
    FAIL_SAFE_OFF,
    FAIL_SAFE_OPEN,
    FAIL_SAFE_UNKNOWN,
    PowerSource,
    PowerState,
)


# ─────────────────────────────────────────────────────────────────────────────
# DATEN-STRUKTUREN
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SlotReading:
    """Snapshot eines Slots zum Auswertungszeitpunkt.

    `value` ist der raw-State als String, oder None wenn unavailable/unknown.
    `numeric` ist der konvertierte Float (für Watt-Sensoren), sonst None.
    `last_updated` markiert wann der Slot zuletzt frische Daten lieferte.
    """

    value: str | None
    numeric: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    last_updated: datetime | None = None


@dataclass(frozen=True)
class DeviceConfig:
    """Konfiguration eines Devices (aus Config Flow)."""

    slug: str
    display_name: str
    device_type: str  # v2: generisches Label (atomic_class) — Power-Regeln nutzen es nicht
    watt_threshold_on: int = DEFAULT_WATT_THRESHOLD_ON
    watt_buckets: tuple["WattBucket", ...] = ()
    sticky_hold_seconds: int = DEFAULT_STICKY_HOLD_SECONDS
    area_id: str | None = None
    # Konfigurierte Rollen/Slots (für available-Auswertung)
    configured_slots: tuple[str, ...] = ()
    # Fail-Safe-Modus für passthrough/numeric (greift nur wenn keine Quelle frisch).
    fail_safe: str = FAIL_SAFE_HOLD_LAST
    # Optionales, variantenspezifisches Freshness-Fenster für Power-Quellen.
    # None bewahrt die bestehende value-presence Semantik anderer Devices.
    source_freshness_seconds: int | None = None
    # Kurzes Konfliktfenster für TV: WebOS off + frischer Wattwert ist zunächst
    # ein Übergang, nicht sofort ein bestätigtes Aus.
    source_conflict_hold_seconds: int = 0


@dataclass(frozen=True)
class WattBucket:
    """Bucket-Eintrag für `power_state`-Ableitung (R-DC-06).

    state: semantischer State (off/idle/playing/...).
    op:    Vergleichsoperator gegen den Watt-Wert (<, <=, =, >, >=).
           None = catch-all (matcht immer).
    value: Vergleichswert in Watt. None = catch-all.

    Auswertung: Buckets werden in Reihenfolge geprüft, erster Treffer gewinnt.
    Die Reihenfolge bestimmt der User (off → idle → playing).
    """

    state: str
    op: str | None = None
    value: float | None = None


# Erlaubte Vergleichsoperatoren für Watt-Buckets.
WATT_OPERATORS: tuple[str, ...] = ("<", "<=", "=", ">", ">=")


def _match_operator(op: str, watt: float, value: float) -> bool:
    if op == "<":
        return watt < value
    if op == "<=":
        return watt <= value
    if op == "=":
        return watt == value
    if op == ">":
        return watt > value
    if op == ">=":
        return watt >= value
    return False


@dataclass(frozen=True)
class Override:
    """Aktiver Override-Eintrag (R-DC-07)."""

    powered: bool | None
    power_state: str | None
    expires_at: datetime | None


@dataclass(frozen=True)
class DevicePersisted:
    """Persistenter Zustand pro Device (für Sticky-Hold + Override + Hold-Last)."""

    last_powered: bool | None
    last_powered_change: datetime | None
    override: Override | None
    # v2: letzter gültiger State (für fail_safe=hold_last bei passthrough/numeric).
    last_state: str | None = None
    # watt_primary: Zeitpunkt, zu dem die Leistung zuletzt ≥ Threshold war. Das
    # Halte-Fenster (sticky_hold_seconds) misst ab hier, um kurze Null-Watt-
    # Phasen mitten im Zyklus zu überbrücken.
    last_watt_active: datetime | None = None
    # Beginn eines nicht-assumed WebOS-off/high-watt-Quellenkonflikts.
    source_conflict_since: datetime | None = None


@dataclass(frozen=True)
class DeviceInputs:
    """Snapshot aller Slot-Readings + Boot-Phase-Indikator."""

    slots: dict[str, SlotReading]
    integration_slot: str | None
    state_slot: str | None
    watt_slot: str | None  # Falls vorhanden, der numerische Watt-Sensor
    boot_phase_active: bool


@dataclass(frozen=True)
class DeviceResult:
    """Vollständiges Auswertungsergebnis (was ans Sensor-Layer geht)."""

    state: str  # semantischer Haupt-State (typabhängig)
    powered: bool | None
    power_state: str  # PowerState-Slug
    power_source: str  # PowerSource-Slug
    available: bool
    last_powered_change: datetime | None
    override_active: bool
    watt_disagrees: bool
    watt: float | None
    raw_state: str | None  # raw value des state_slots
    extra: dict[str, Any] = field(default_factory=dict)
    # v2: True wenn keine Quelle frisch war und der fail_safe-Wert greift.
    fail_safe_active: bool = False
    # watt_primary: fortgeschriebener „zuletzt aktiv"-Zeitstempel fürs Halte-
    # Fenster. Der Coordinator persistiert ihn zurück in DevicePersisted.
    last_watt_active: datetime | None = None
    # Fortschreibung des TV-Quellenkonflikts für den nächsten Tick.
    source_conflict_since: datetime | None = None


# ─────────────────────────────────────────────────────────────────────────────
# HILFSFUNKTIONEN
# ─────────────────────────────────────────────────────────────────────────────


_TRUTHY = frozenset(
    {
        "on", "home", "true", "1", "yes", "active", "playing", "open", "online",
        # climate hvac_mode-Werte (Thermostat aktiv = powered)
        "heat", "cool", "auto", "heat_cool", "dry", "fan_only",
    }
)
_FALSY = frozenset({"off", "not_home", "false", "0", "no", "inactive", "idle", "closed", "offline"})


def _as_bool(value: str | None) -> bool | None:
    """Konvertiere raw-State zu bool; None bei unbekannt."""
    if value is None:
        return None
    v = value.strip().lower()
    if v in _TRUTHY:
        return True
    if v in _FALSY:
        return False
    return None


def _is_assumed(reading: SlotReading | None) -> bool:
    """Quelle meldet ``assumed_state`` (HA rät den Zustand, liest ihn nicht real).

    HA legt das Flag als State-Attribut ab; truthy ⇒ die on/off-Angabe ist
    unzuverlässig. Nur bei vorhandenem Wert relevant (None-Quelle ist ohnehin
    nicht power-bestimmend).
    """
    if reading is None or reading.value is None:
        return False
    return bool(reading.attributes.get("assumed_state"))


def _state_from_power(power_state: str, powered: bool | None) -> str:
    """Haupt-State aus realer Leistung, wenn die geratene Quelle verworfen wird.

    Nutzt die Watt-Bucket-Granularität (idle/playing/standby), fällt aber auf
    schlichtes on/off zurück, wenn die Buckets nichts Brauchbares liefern
    (unknown/off bei powered=True wäre widersprüchlich → watt hat „on" entschieden).
    """
    if powered is True:
        if power_state not in (PowerState.UNKNOWN.value, PowerState.OFF.value):
            return power_state
        return "on"
    if powered is False:
        return "off"
    return "unavailable"


def _has_value(reading: SlotReading | None) -> bool:
    """Slot hat einen gültigen Wert (nicht unavailable/unknown).

    Für passthrough/numeric: KEIN Zeitfenster — ein stabiler Kontakt/Cover/
    Climate-State (lange unverändert → altes last_updated) ist normal verfügbar.
    """
    return reading is not None and reading.value is not None


def _any_value(inputs: "DeviceInputs") -> bool:
    return any(_has_value(r) for r in inputs.slots.values())


def _is_fresh(reading: SlotReading | None, now: datetime, max_age: int) -> bool:
    """Slot frisch? Hat einen Wert UND ist nicht zu alt."""
    if reading is None or reading.value is None:
        return False
    if reading.last_updated is None:
        # Kein Timestamp → wir trauen dem Wert (HA liefert immer einen).
        return True
    return (now - reading.last_updated).total_seconds() <= max_age


def _source_is_fresh(
    reading: SlotReading | None, now: datetime, max_age: int | None
) -> bool:
    """Apply a time window only to devices that explicitly opt into it."""
    if max_age is None:
        return _has_value(reading)
    return _is_fresh(reading, now, max_age)


def classify_power_state(
    watt: float | None, buckets: tuple[WattBucket, ...]
) -> str:
    """R-DC-06: Watt → semantischer power_state.

    Ohne buckets oder watt → "unknown".
    Mit buckets: in Reihenfolge prüfen, erster Treffer gewinnt. Ein Bucket
    ohne Operator/Value gilt als catch-all (matcht immer).
    """
    if not buckets or watt is None:
        return PowerState.UNKNOWN.value
    for b in buckets:
        if b.op is None or b.value is None:
            return b.state  # catch-all
        if _match_operator(b.op, watt, b.value):
            return b.state
    return PowerState.UNKNOWN.value


# ─────────────────────────────────────────────────────────────────────────────
# HAUPTFUNKTION
# ─────────────────────────────────────────────────────────────────────────────


def compute_device(
    config: DeviceConfig,
    inputs: DeviceInputs,
    persisted: DevicePersisted,
    now: datetime,
    *,
    watt_primary: bool = False,
) -> DeviceResult:
    """Wertet alle Regeln R-DC-01..R-DC-09 aus.

    Reihenfolge:
    1. Override aktiv (R-DC-07) → direkt setzen
    2. Integration first (R-DC-01.1)
    3. Watt-Fallback (R-DC-01.2)
    4. Sticky-Hold (R-DC-01.3), aber nicht in Boot-Phase (R-DC-09)
    5. Sonst: powered=None, power_source="none"

    ``watt_primary`` kehrt 2↔3 um: ist ein frischer Watt-Wert vorhanden,
    entscheidet die reale Leistung über ``powered`` (≥ ``watt_threshold_on``),
    und der Integrations-Schalter (Plug) zählt nur als Fallback bei stalem
    Meter. Ein Halte-Fenster (``sticky_hold_seconds`` ab ``last_watt_active``)
    überbrückt kurze Null-Watt-Phasen im Gerätezyklus. Für Plugs mit
    Energy-Meter, wo „Plug bestromt" ≠ „Gerät läuft".

    FLEET-83 (generische Härtung): meldet die Integrations-/State-Quelle
    ``assumed_state`` (HA *rät* den Zustand statt ihn real zu lesen — z. B. ein
    webOS-TV-Player im Netz-Standby, der dauerhaft „off" zeigt obwohl 440 W
    fließen), ist sie als Power- UND State-Quelle unzuverlässig. Liegt ein
    frischer Watt-Wert vor, schaltet das Atomic für dieses Tick automatisch auf
    watt-primär — unabhängig vom übergebenen ``watt_primary``. ``powered`` und
    der Haupt-State folgen dann der realen Leistung (mit Halte-Fenster); der
    geratene on/off-State wird verworfen. Der erwartbare „Player aus + Strom
    fließt"-Konflikt wird NICHT als ``watt_disagrees`` geflaggt (kein Fault,
    sondern Bauart der assumed-Quelle).

    Zusätzlich immer:
    - power_state aus Watt-Buckets (R-DC-06), unabhängig von 1-4
    - available (R-DC-03)
    - watt_disagrees (R-DC-05)
    """
    integration_reading = inputs.slots.get(inputs.integration_slot) if inputs.integration_slot else None
    state_reading = inputs.slots.get(inputs.state_slot) if inputs.state_slot else None
    watt_reading = inputs.slots.get(inputs.watt_slot) if inputs.watt_slot else None

    watt = watt_reading.numeric if watt_reading is not None else None
    # v2-Optimierung bleibt für alle nicht explizit konfigurierten Devices
    # erhalten. TV erhält dagegen ein begrenztes Quellenfenster, damit ein alter
    # WebOS-off-Wert nicht dauerhaft einen frischen Watt-Fallback blockiert.
    integration_fresh = _source_is_fresh(
        integration_reading, now, config.source_freshness_seconds
    )
    integration_bool = _as_bool(integration_reading.value) if integration_reading else None
    watt_fresh = (
        watt_reading is not None
        and watt is not None
        and _source_is_fresh(watt_reading, now, config.source_freshness_seconds)
    )

    # ── FLEET-83: assumed-state-Quelle erkennen → automatisch watt-primär.
    # ``assumed_state`` steht in den HA-Attributen der Integrations-/State-Quelle
    # (für Media ist beides dieselbe primary_state-Entity). Greift nur, wenn ein
    # frischer Watt-Wert die geratene Quelle ersetzen kann.
    assumed_source = _is_assumed(integration_reading) or _is_assumed(state_reading)
    assumed_unreliable = assumed_source and watt_fresh
    effective_watt_primary = watt_primary or assumed_unreliable
    # assumed ohne Watt: Integration rät nur, aber kein Watt-Sensor kann das bestätigen
    # oder widerlegen → Reading komplett verwerfen (fällt durch zu Sticky/Unknown).
    assumed_no_watt = assumed_source and not watt_fresh

    # ── power_state: immer aus Watt (R-DC-06)
    power_state = classify_power_state(watt, config.watt_buckets)

    # ── Override (R-DC-07)
    override = persisted.override
    override_active = (
        override is not None
        and (override.expires_at is None or now < override.expires_at)
    )
    if override_active:
        assert override is not None  # for type checkers
        return DeviceResult(
            state=_compute_state(state_reading, override.powered, inputs.state_slot),
            powered=override.powered,
            power_state=override.power_state or power_state,
            power_source=PowerSource.OVERRIDE.value,
            available=_compute_available(inputs, now),
            last_powered_change=persisted.last_powered_change,
            override_active=True,
            watt_disagrees=False,
            watt=watt,
            raw_state=state_reading.value if state_reading else None,
            extra=state_reading.attributes if state_reading else {},
            last_watt_active=persisted.last_watt_active,
            source_conflict_since=persisted.source_conflict_since,
        )

    # ── R-DC-01: Fallback-Hierarchie für `powered`
    powered: bool | None = None
    source = PowerSource.NONE
    last_watt_active = persisted.last_watt_active
    watt_on = watt_fresh and watt is not None and watt >= config.watt_threshold_on

    # Issue #21: if WebOS briefly reports off while a fresh TV watt sample is
    # clearly above the on threshold, do not create an active→off→active edge.
    # The conflict is provisional for a bounded window. During that window a
    # previously active TV is held; a device without a previous active state is
    # kept off, so this rule cannot invent activation. After the window the
    # normal hierarchy applies: fresh WebOS-off still wins, while stale WebOS
    # can fall through to the fresh watt fallback.
    source_conflict = (
        config.source_conflict_hold_seconds > 0
        and not assumed_source
        and integration_fresh
        and integration_bool is False
        and watt_on
    )
    source_conflict_since = persisted.source_conflict_since
    source_conflict_age: float | None = None
    if source_conflict:
        if source_conflict_since is None:
            source_conflict_since = now
        source_conflict_age = (now - source_conflict_since).total_seconds()
    else:
        source_conflict_since = None
    in_source_conflict_hold = (
        source_conflict
        and source_conflict_age is not None
        and source_conflict_age < config.source_conflict_hold_seconds
    )

    if in_source_conflict_hold:
        powered = persisted.last_powered is True
        source = PowerSource.INTEGRATION
        if watt_on:
            last_watt_active = now
    elif effective_watt_primary and watt_fresh:
        # Watt-primär: die reale Leistung entscheidet. Der Plug-Schalter sagt
        # nur, dass Strom anliegt — nicht, dass das Gerät läuft.
        if watt_on:
            powered = True
            source = PowerSource.WATT_PRIMARY
            last_watt_active = now
        else:
            # Halte-Fenster: kurze Null-Watt-Dips (Einweichen/Pause) überbrücken,
            # solange das Gerät zuletzt innerhalb sticky_hold_seconds aktiv war.
            # Bei assumed_state-Integrationen ist ein frisches "off" plus 0 W
            # dagegen ein echter Power-Down; sonst hält der TV nach dem Ausgehen
            # ein Phantom-on und blockiert downstream Entertainment-Idle.
            allow_hold = not (assumed_unreliable and integration_bool is False)
            held = (
                allow_hold
                and
                persisted.last_powered is True
                and last_watt_active is not None
                and (now - last_watt_active).total_seconds() <= config.sticky_hold_seconds
            )
            powered = True if held else False
            source = PowerSource.STICKY_HOLD if held else PowerSource.WATT_PRIMARY
    elif not assumed_no_watt and integration_fresh and integration_bool is not None:
        powered = integration_bool
        source = PowerSource.INTEGRATION
        if watt_on:
            last_watt_active = now
    elif watt_fresh:
        powered = watt_on
        source = PowerSource.WATT_FALLBACK
        if watt_on:
            last_watt_active = now
    elif not inputs.boot_phase_active:
        # Sticky-Hold (R-DC-02), nur außerhalb Boot-Phase (R-DC-09)
        age = _sticky_age_seconds(persisted, now)
        if (
            persisted.last_powered is not None
            and age is not None
            and age <= config.sticky_hold_seconds
        ):
            powered = persisted.last_powered
            source = PowerSource.STICKY_HOLD

    # ── R-DC-05: Konflikt Integration vs. Watt
    watt_disagrees = False
    if effective_watt_primary and watt_fresh:
        # Watt-primär: NUR der überraschende Konflikt ist meldenswert — der Plug
        # meldet AUS, es fließt aber Strom (≥ Threshold). „Plug an + ~0 W" ist
        # der normale Idle-Zustand eines bestromten Geräts und KEINE Degradierung.
        # assumed-Quellen sind strukturell unzuverlässig (sie raten nur) → ihr
        # „off bei Strom" ist erwartbar, kein Fault. Nicht flaggen.
        if integration_fresh and integration_bool is False and watt_on and not assumed_source:
            watt_disagrees = True
    elif source is PowerSource.INTEGRATION and watt_fresh and watt is not None:
        # Integration sagt off, aber Watt über Threshold? → flagge
        if (powered is False or source_conflict) and watt >= config.watt_threshold_on:
            watt_disagrees = True

    # ── last_powered_change ableiten
    new_last_change = persisted.last_powered_change
    if powered != persisted.last_powered:
        new_last_change = now

    # ── Haupt-State (R-DC-04). Bei einer assumed-Quelle, die watt-primär
    # überstimmt wurde, ist der geratene Player-State („off") wertlos → State
    # aus der realen Leistung ableiten, sonst läse media_state das Atomic trotz
    # 440 W als „off" (FLEET-83).
    # assumed_no_watt: Kein Watt zum Verifizieren → raw webOS-State ebenso unbrauchbar,
    # State nur aus powered ableiten (on/off/unavailable, kein „playing"-Phantom).
    if assumed_unreliable:
        state = _state_from_power(power_state, powered)
    elif assumed_no_watt:
        state = _compute_state(None, powered, None)
    else:
        state = _compute_state(state_reading, powered, inputs.state_slot)

    return DeviceResult(
        state=state,
        powered=powered,
        power_state=power_state,
        power_source=source.value,
        available=_compute_available(inputs, now),
        last_powered_change=new_last_change,
        override_active=False,
        watt_disagrees=watt_disagrees,
        watt=watt,
        raw_state=state_reading.value if state_reading else None,
        extra=state_reading.attributes if state_reading else {},
        last_watt_active=last_watt_active,
        source_conflict_since=source_conflict_since if source_conflict else None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# AUX
# ─────────────────────────────────────────────────────────────────────────────


def _compute_state(
    state_reading: SlotReading | None,
    powered: bool | None,
    state_slot: str | None,
) -> str:
    """R-DC-04: State-Mapping.

    - Stateful (state_slot gesetzt + Reading vorhanden): nimm raw value
      des state_slots (z.B. media_player playing/paused/idle/off).
    - Sonst aus powered ableiten: on/off/unavailable.
    """
    if state_slot and state_reading is not None and state_reading.value is not None:
        return state_reading.value
    if powered is True:
        return "on"
    if powered is False:
        return "off"
    return "unavailable"


def _compute_available(inputs: DeviceInputs, now: datetime) -> bool:
    """R-DC-03: available = mindestens eine Quelle hat einen gültigen Wert.

    v2-Optimierung: Wert-Präsenz statt Zeitfenster. HA bildet echte Ausfälle als
    `unavailable`/`unknown` (= Wert None) ab; ein stabiler, lange unveränderter
    Zustand (Switch/Contact/Media off) ist normal verfügbar. Die Power-
    Entscheidungen (Sticky/Watt/Override/Buckets) bleiben davon unberührt.
    """
    return any(_has_value(r) for r in inputs.slots.values())


def _sticky_age_seconds(persisted: DevicePersisted, now: datetime) -> float | None:
    if persisted.last_powered_change is None:
        return None
    return (now - persisted.last_powered_change).total_seconds()


def is_boot_phase(boot_start: datetime, now: datetime) -> bool:
    """R-DC-09: Erste BOOT_INITIAL_PHASE_SECONDS nach Modul-Setup."""
    return (now - boot_start) < timedelta(seconds=BOOT_INITIAL_PHASE_SECONDS)


# ─────────────────────────────────────────────────────────────────────────────
# v2: passthrough_state + numeric (KEINE Watt/Sticky-Mechanik)
# ─────────────────────────────────────────────────────────────────────────────


def _apply_fail_safe(mode: str, persisted: DevicePersisted) -> tuple[str, bool | None]:
    """Fail-Safe-Wert wenn keine Quelle frisch ist → (state, powered)."""
    if mode == FAIL_SAFE_OFF:
        return ("off", False)
    if mode == FAIL_SAFE_OPEN:
        return ("open", True)
    if mode == FAIL_SAFE_HOLD_LAST:
        return (persisted.last_state or "unknown", persisted.last_powered)
    # FAIL_SAFE_UNKNOWN + Default
    return ("unknown", None)


def _active_override(persisted: DevicePersisted, now: datetime) -> Override | None:
    o = persisted.override
    if o is not None and (o.expires_at is None or now < o.expires_at):
        return o
    return None


def compute_passthrough(
    config: DeviceConfig,
    inputs: DeviceInputs,
    persisted: DevicePersisted,
    now: datetime,
) -> DeviceResult:
    """Passthrough-State: state = raw value der state_role-Quelle.

    Keine Watt-/Sticky-Mechanik. ``fail_safe`` greift nur, wenn keine Quelle
    frisch ist. Override (R-DC-07) bleibt wirksam.
    """
    available = _any_value(inputs)
    state_reading = inputs.slots.get(inputs.state_slot) if inputs.state_slot else None

    override = _active_override(persisted, now)
    if override is not None:
        state = (
            override.power_state
            or _compute_state(state_reading, override.powered, inputs.state_slot)
        )
        return DeviceResult(
            state=state, powered=override.powered,
            power_state=override.power_state or PowerState.UNKNOWN.value,
            power_source=PowerSource.OVERRIDE.value, available=available,
            last_powered_change=persisted.last_powered_change, override_active=True,
            watt_disagrees=False, watt=None,
            raw_state=state_reading.value if state_reading else None,
            extra=state_reading.attributes if state_reading else {},
            last_watt_active=persisted.last_watt_active,
        )

    fresh = _has_value(state_reading)
    if fresh and state_reading is not None and state_reading.value is not None:
        state = state_reading.value
        powered = _as_bool(state)
        source = PowerSource.INTEGRATION
        fail_safe_active = False
        extra = state_reading.attributes
        raw = state_reading.value
    else:
        state, powered = _apply_fail_safe(config.fail_safe, persisted)
        source = (
            PowerSource.STICKY_HOLD
            if config.fail_safe == FAIL_SAFE_HOLD_LAST
            else PowerSource.NONE
        )
        fail_safe_active = True
        extra = {}
        raw = None

    new_last_change = persisted.last_powered_change
    if powered != persisted.last_powered:
        new_last_change = now

    return DeviceResult(
        state=state, powered=powered, power_state=PowerState.UNKNOWN.value,
        power_source=source.value, available=available,
        last_powered_change=new_last_change, override_active=False,
        watt_disagrees=False, watt=None, raw_state=raw, extra=extra,
        fail_safe_active=fail_safe_active,
        last_watt_active=persisted.last_watt_active,
    )


def compute_numeric(
    config: DeviceConfig,
    inputs: DeviceInputs,
    persisted: DevicePersisted,
    now: datetime,
) -> DeviceResult:
    """Numeric: state = primärer Messwert (state_role). Übrige Werte → Attribute."""
    available = _any_value(inputs)
    reading = inputs.slots.get(inputs.state_slot) if inputs.state_slot else None

    override = _active_override(persisted, now)
    if override is not None:
        return DeviceResult(
            state=override.power_state or "override", powered=override.powered,
            power_state=override.power_state or PowerState.UNKNOWN.value,
            power_source=PowerSource.OVERRIDE.value, available=available,
            last_powered_change=persisted.last_powered_change, override_active=True,
            watt_disagrees=False, watt=reading.numeric if reading else None,
            raw_state=reading.value if reading else None,
            extra=reading.attributes if reading else {},
            last_watt_active=persisted.last_watt_active,
        )

    fresh = _has_value(reading)
    if fresh and reading is not None and reading.value is not None:
        state = reading.value
        fail_safe_active = False
        extra = reading.attributes
    else:
        state, _ = _apply_fail_safe(config.fail_safe, persisted)
        fail_safe_active = True
        extra = {}

    return DeviceResult(
        state=state, powered=None, power_state=PowerState.UNKNOWN.value,
        power_source=PowerSource.NONE.value, available=available,
        last_powered_change=persisted.last_powered_change, override_active=False,
        watt_disagrees=False, watt=reading.numeric if reading else None,
        raw_state=reading.value if reading else None, extra=extra,
        fail_safe_active=fail_safe_active,
        last_watt_active=persisted.last_watt_active,
    )


# ─────────────────────────────────────────────────────────────────────────────
# WATT-BUCKETS Parsing (aus Config Flow / Storage)
# ─────────────────────────────────────────────────────────────────────────────


def parse_watt_buckets(raw: Any) -> tuple[WattBucket, ...]:
    """Parst eine Bucket-Liste aus Config-Flow / Storage / Import.

    Erwartetes Format (Reihenfolge = Auswertungsreihenfolge):
        [{"state": "off", "op": "<=", "value": 5},
         {"state": "idle", "op": "<=", "value": 30},
         {"state": "playing", "op": ">", "value": 30}]

    Ein Eintrag ohne op/value gilt als catch-all. Reihenfolge bleibt erhalten
    (kein Re-Sort — der User bestimmt die Priorität).

    Robust gegen None/leere/kaputte Werte: ungültige Einträge werden
    übersprungen.
    """
    if not raw or not isinstance(raw, list):
        return ()
    out: list[WattBucket] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        state = entry.get("state")
        if not isinstance(state, str) or not state:
            continue
        op = entry.get("op")
        value_raw = entry.get("value")
        # catch-all: weder op noch value
        if op in (None, "") and value_raw in (None, ""):
            out.append(WattBucket(state=state, op=None, value=None))
            continue
        if op not in WATT_OPERATORS:
            continue
        try:
            value = float(value_raw)
        except (TypeError, ValueError):
            continue
        out.append(WattBucket(state=state, op=op, value=value))
    return tuple(out)


# ─────────────────────────────────────────────────────────────────────────────
# Override-Lifecycle
# ─────────────────────────────────────────────────────────────────────────────


def build_override(
    powered: bool | None,
    power_state: str | None,
    expire_seconds: int | None,
    now: datetime,
) -> Override:
    """Baue Override-Objekt aus Service-Parametern."""
    expires_at = now + timedelta(seconds=expire_seconds) if expire_seconds else None
    return Override(
        powered=powered,
        power_state=power_state,
        expires_at=expires_at,
    )


def is_override_expired(override: Override | None, now: datetime) -> bool:
    if override is None:
        return True
    if override.expires_at is None:
        return False
    return now >= override.expires_at
