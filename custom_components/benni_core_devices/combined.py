"""Combined-Atomic-Engine v0/v1 (LH §6).

Pure, HA-frei, in pytest testbar. Bildet einfache First-Match-Wins-/
Truth-Table-Logiken über mehrere Quellen ab — z. B. Opening/Fenster-Logik.

Bewusste v0-Grenzen: keine allgemeine Timer-/History-DSL. Nur:
- mehrere Quellen mit Rolle + Entity
- First-Match-Wins-Regelliste mit einfachen Bedingungen
- Default-Regel + Reason
- Output-Typen enum / code / boolean / number
- einfache abgeleitete Binary-Sensoren (Gate-/Policy-Ausgaben)
- explizit konfigurierte, kurze Power-Source-Arbitration für Device-Masters

Der Coordinator liefert die `SourceReading`s (aus HA-States); diese Datei trifft
nur reine Entscheidungen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .combined_expr import (
    ExprError,
    as_bool,
    as_num,
    eval_expr,
    func_names,
    parse,
    refs,
)
from .const import (
    COMBINED_OP_EQ,
    COMBINED_OP_GE,
    COMBINED_OP_GT,
    COMBINED_OP_LE,
    COMBINED_OP_LT,
    COMBINED_OP_NE,
    COMBINED_OP_UNAVAILABLE,
    COMBINED_OPERATOR_CHOICES,
    FAIL_SAFE_HOLD_LAST,
    FAIL_SAFE_OFF,
    FAIL_SAFE_OPEN,
    FAIL_SAFE_UNKNOWN,
    OUTPUT_TYPE_BOOLEAN,
    OUTPUT_TYPE_CODE,
    OUTPUT_TYPE_ENUM,
    OUTPUT_TYPE_NUMBER,
)

# Werte, die als "an/wahr" gelten (für boolean-Output + Derived-Sensoren).
_TRUTHY = frozenset({"on", "true", "yes", "1", "open", "home", "playing", "active"})

# v1.0 Node-Arten in derived_values[].
NODE_EXPR = "expr"
NODE_GATE = "gate"
NODE_ENUM = "enum"
NODE_HEALTH = "health"
NODE_LATCH = "latch"
NODE_PREVIOUS = "previous"
NODE_POWER_ARBITRATION = "power_arbitration"
NODE_KINDS = (
    NODE_EXPR,
    NODE_GATE,
    NODE_ENUM,
    NODE_HEALTH,
    NODE_LATCH,
    NODE_PREVIOUS,
    NODE_POWER_ARBITRATION,
)
SELF_REF = "self"
_NODE_VALUE = "__value__"
_POWER_ACTIVE_OUTPUTS = frozenset({"webos", "watt_fallback", "conflict_hold"})
_POWER_CANDIDATE_OUTPUT = "tv_candidate"
_POWER_CANDIDATE_STATES = frozenset({"off", "standby"})


# ─────────────────────────────────────────────────────────────────────────────
# DATEN-STRUKTUREN
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SourceReading:
    """Snapshot einer Combined-Quelle."""

    value: Any | None
    numeric: float | None = None
    available: bool = True
    # Attribute der Quell-Entity (für health-Node: atomic_quality/degraded/…).
    attributes: dict[str, Any] = field(default_factory=dict)
    # HA-State-Zeitpunkt; None bedeutet, dass Freshness nicht bewertbar ist.
    last_updated: datetime | None = None


@dataclass(frozen=True)
class CombinedSource:
    """Eine Eingangsquelle des Combined."""

    key: str          # eindeutig innerhalb des Combined (Referenz in Regeln)
    role: str         # fachliche Rolle (open_contact, tilt_contact, ...)
    entity: str | None = None  # Raw-Entity-ID
    attribute: str | None = None  # optional: state_attr(entity, attribute) statt State
    required: bool = True  # false = opportunistische Quelle, keine Degradation


@dataclass(frozen=True)
class CombinedRule:
    """Eine First-Match-Wins-Regel."""

    source: str            # CombinedSource.key
    op: str                # COMBINED_OP_*
    value: str | None = None
    output: Any = None
    reason: str | None = None


@dataclass(frozen=True)
class DerivedSensor:
    """Abgeleiteter Binary-Sensor (Gate-/Policy-Ausgabe)."""

    slug: str
    name: str
    object_id: str | None = None
    device_class: str | None = None
    # Ziel: "__output__", ein derived_values-Name, ein source.key oder eine Rolle.
    target: str = "__output__"
    op: str = COMBINED_OP_EQ
    value: str | None = None


@dataclass(frozen=True)
class DerivedCase:
    """Geordneter Fall für enum-Derived-Werte."""

    when: str
    output: Any


@dataclass(frozen=True)
class DerivedValue:
    """Benannter Zwischenwert (v1.0): expr | gate | enum | health | latch | previous.

    ``power_arbitration`` is deliberately configured rather than inferred from a
    master slug: it owns a short-lived conflict rule for a device power source.
    """

    name: str
    kind: str
    expr: str | None = None          # expr/gate
    cases: tuple[DerivedCase, ...] = ()  # enum
    default: Any = None              # enum
    set_expr: str | None = None      # latch
    reset_expr: str | None = None    # latch
    atomics: tuple[str, ...] = ()    # health: konsumierte source-keys
    state_source: str | None = None
    watt_source: str | None = None
    assumed_state_source: str | None = None
    active_states: tuple[str, ...] = ("on", "playing")
    threshold: float | None = None
    freshness_seconds: float | None = None
    conflict_hold_seconds: float | None = None
    fail_safe: str | None = None     # off|open|hold_last|unknown (sonst config-Default)
    expose: bool = False             # als flaches Top-Level-Attribut veröffentlichen


@dataclass(frozen=True)
class CombinedConfig:
    """Konfiguration eines Combined-Atomics."""

    slug: str
    display_name: str
    output_type: str = OUTPUT_TYPE_ENUM
    sources: tuple[CombinedSource, ...] = ()
    rules: tuple[CombinedRule, ...] = ()
    default_output: Any = None
    default_reason: str | None = None
    code_legend: dict[str, Any] = field(default_factory=dict)
    derived: tuple[DerivedSensor, ...] = ()
    # v1.0:
    derived_values: tuple[DerivedValue, ...] = ()
    exposed_attributes: tuple[str, ...] = ()
    fail_safe: str = FAIL_SAFE_UNKNOWN


@dataclass(frozen=True)
class CombinedPersisted:
    """Persistenter Zustand pro Combined (v1.0b: last_state + latch/previous)."""

    last_state: str | None = None
    node_states: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CombinedResult:
    """Auswertungsergebnis eines Combined-Atomics."""

    state: str
    output: Any
    reason: str
    matched_rule: int | None
    source_entities: dict[str, str]
    source_attributes: dict[str, str]
    source_states: dict[str, Any]
    source_available: dict[str, bool]
    missing_sources: list[str]
    degraded: bool
    degraded_reason: list[str]
    # v1.0:
    derived: dict[str, Any] = field(default_factory=dict)
    node_states: dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# OPERATOR-AUSWERTUNG
# ─────────────────────────────────────────────────────────────────────────────


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in _TRUTHY


def _match(reading: SourceReading | None, op: str, value: str | None) -> bool:
    """Wertet eine einzelne Bedingung aus."""
    unavailable = reading is None or not reading.available or reading.value is None
    if op == COMBINED_OP_UNAVAILABLE:
        return unavailable
    if unavailable:
        # eq/ne/numeric matchen nicht auf unverfügbaren Quellen.
        return False
    assert reading is not None
    if op == COMBINED_OP_EQ:
        return str(reading.value) == str(value)
    if op == COMBINED_OP_NE:
        return str(reading.value) != str(value)
    # numerische Vergleiche
    if op in (COMBINED_OP_LT, COMBINED_OP_LE, COMBINED_OP_GT, COMBINED_OP_GE):
        left = reading.numeric
        try:
            right = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
        if left is None:
            return False
        if op == COMBINED_OP_LT:
            return left < right
        if op == COMBINED_OP_LE:
            return left <= right
        if op == COMBINED_OP_GT:
            return left > right
        if op == COMBINED_OP_GE:
            return left >= right
    return False


# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT-COERCION
# ─────────────────────────────────────────────────────────────────────────────


def coerce_output(output: Any, output_type: str) -> str:
    """Wandelt einen Regel-Output in den finalen State-String."""
    if output is None:
        return "unknown"
    if output_type == OUTPUT_TYPE_BOOLEAN:
        return "on" if _truthy(output) else "off"
    if output_type == OUTPUT_TYPE_NUMBER:
        try:
            num = float(output)
        except (TypeError, ValueError):
            return str(output)
        return str(int(num)) if num.is_integer() else str(num)
    # enum / code: roher String
    return str(output)


# ─────────────────────────────────────────────────────────────────────────────
# HAUPTAUSWERTUNG
# ─────────────────────────────────────────────────────────────────────────────


def _autoscalar(reading: SourceReading | None) -> Any:
    """Quelle → Skalar für die Expression-Engine (Zahl, Rohstring oder None)."""
    if reading is None or not reading.available or reading.value is None:
        return None
    if reading.numeric is not None:
        return reading.numeric
    return reading.value


def _wrap(value: Any) -> SourceReading:
    """Derived-Wert → SourceReading-artig (für die v0-Regel-Matcher)."""
    if value is None:
        return SourceReading(value=None, available=False)
    if isinstance(value, bool):
        return SourceReading(value=("on" if value else "off"), numeric=(1.0 if value else 0.0))
    if isinstance(value, (int, float)):
        return SourceReading(value=str(value), numeric=float(value))
    return SourceReading(value=str(value), numeric=as_num(value))


def _failsafe_value(kind: str, mode: str | None, prev: Any) -> Any:
    if mode == FAIL_SAFE_HOLD_LAST:
        return prev
    if kind in (NODE_GATE, NODE_LATCH):
        if mode == FAIL_SAFE_OFF:
            return False
        if mode == FAIL_SAFE_OPEN:
            return True
    return None


def _failsafe_output(mode: str, prev: Any) -> Any:
    if mode == FAIL_SAFE_HOLD_LAST:
        return prev
    if mode == FAIL_SAFE_OFF:
        return "off"
    if mode == FAIL_SAFE_OPEN:
        return "open"
    return None


def _node_value(value: Any) -> Any:
    """Return the scalar value of a node with optional persisted metadata."""
    if isinstance(value, dict) and _NODE_VALUE in value:
        return value[_NODE_VALUE]
    return value


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _fresh(reading: SourceReading | None, now: Any, seconds: float) -> bool:
    """Return True only for an available reading with a comparable timestamp."""
    if reading is None or not reading.available or reading.value is None:
        return False
    updated = _as_datetime(reading.last_updated)
    current = _as_datetime(now)
    if updated is None or current is None:
        return False
    if (updated.tzinfo is None) != (current.tzinfo is None):
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=current.tzinfo)
        else:
            current = current.replace(tzinfo=updated.tzinfo)
    age = (current - updated).total_seconds()
    return 0 <= age <= seconds


def _power_state(
    value: str,
    conflict_since: datetime | None = None,
    confirmed_state_updated: datetime | None = None,
    candidate_since: datetime | None = None,
) -> dict[str, Any]:
    return {
        _NODE_VALUE: value,
        "conflict_since": conflict_since.isoformat() if conflict_since else None,
        "confirmed_state_updated": (
            confirmed_state_updated.isoformat()
            if confirmed_state_updated else None
        ),
        "candidate_since": candidate_since.isoformat() if candidate_since else None,
    }


def _eval_power_arbitration(
    dv: DerivedValue,
    readings: dict[str, SourceReading],
    prev_states: dict[str, Any],
    now: Any,
) -> dict[str, Any]:
    """Apply a configured integration-first power source contract.

    A fresh WebOS ``off`` remains authoritative unless it is the first half of a
    TV-only cold start: fresh high watt plus missing ``assumed_state`` then emits
    ``tv_candidate`` for the configured window. The candidate is explicit and
    does not power the master; after the window it becomes ``watt_fallback``.
    A confirmed active state still uses ``conflict_hold`` so a cold-start
    ``on → off → on`` sequence cannot flap the master.
    """
    state = readings.get(dv.state_source or "")
    watt = readings.get(dv.watt_source or "")
    assumed = readings.get(dv.assumed_state_source or "")
    threshold = 50.0 if dv.threshold is None else dv.threshold
    freshness = 20.0 if dv.freshness_seconds is None else dv.freshness_seconds
    hold_seconds = (
        20.0 if dv.conflict_hold_seconds is None else dv.conflict_hold_seconds
    )
    current = _as_datetime(now)
    watt_active = bool(
        watt
        and watt.numeric is not None
        and watt.numeric >= threshold
        and _fresh(watt, now, freshness)
    )
    state_fresh = _fresh(state, now, freshness)
    state_updated = _as_datetime(state.last_updated if state else None)
    state_value = (
        str(state.value).strip().lower()
        if state and state.available and state.value is not None
        else None
    )
    if state_value in {"", "unknown", "unavailable"}:
        state_value = None
    active_states = {str(item).strip().lower() for item in dv.active_states}
    assumed_value = as_bool(assumed.value) if assumed and assumed.available else None

    # Freshness is checked before accepting WebOS on/playing. A stale active
    # player must not keep a TV with fresh standby wattage alive.
    if state_value in active_states and state_fresh:
        return _power_state("webos")

    previous_raw = prev_states.get(dv.name)
    previous = _node_value(previous_raw)
    previous_confirmed = _as_datetime(
        previous_raw.get("confirmed_state_updated")
        if isinstance(previous_raw, dict) else None
    )
    same_confirmed_state = (
        previous in {"webos_off", "conflict_hold"}
        and state_value is not None
        and previous_confirmed is not None
        and state_updated == previous_confirmed
    )
    previous_candidate_since = _as_datetime(
        previous_raw.get("candidate_since")
        if isinstance(previous_raw, dict) else None
    )
    previous_candidate_confirmed = (
        previous == "watt_fallback" and previous_candidate_since is not None
    )
    if same_confirmed_state and previous == "webos_off":
        return _power_state("webos_off", confirmed_state_updated=previous_confirmed)
    if same_confirmed_state and previous == "conflict_hold" and current is not None:
        previous_conflict_since = _as_datetime(
            previous_raw.get("conflict_since")
            if isinstance(previous_raw, dict) else None
        )
        if (
            previous_conflict_since is not None
            and (current - previous_conflict_since).total_seconds() > hold_seconds
        ):
            return _power_state("webos_off", confirmed_state_updated=previous_confirmed)

    # Integration dropout: watt is a fallback only with fresh meter evidence.
    if state_value is None and watt_active:
        return _power_state(
            "watt_fallback",
            candidate_since=(
                previous_candidate_since
                if previous in {_POWER_CANDIDATE_OUTPUT, "watt_fallback"}
                else None
            ),
        )

    # A cold-start WebOS off/standby plus fresh high watt is provisional. It is
    # intentionally separate from is_powered so the TV consumer can run its own
    # 20-second media-context stabilization without inventing a durable power
    # truth on the first contradictory tick.
    candidate_conflict = (
        state_value in _POWER_CANDIDATE_STATES
        and watt_active
        and assumed_value is None
        and (state_fresh or state_updated is None)
    )
    if candidate_conflict:
        if previous == _POWER_CANDIDATE_OUTPUT and current is not None:
            candidate_since = previous_candidate_since or current
            if (current - candidate_since).total_seconds() >= hold_seconds:
                return _power_state(
                    "watt_fallback", candidate_since=candidate_since
                )
            return _power_state(
                _POWER_CANDIDATE_OUTPUT, candidate_since=candidate_since
            )
        if previous_candidate_confirmed:
            return _power_state(
                "watt_fallback", candidate_since=previous_candidate_since
            )

    # A non-active WebOS value that is older than the freshness window no
    # longer outranks fresh meter evidence. This is still a fallback, never an
    # override of a fresh WebOS ``off``.
    if state_value is not None and not state_fresh and watt_active:
        return _power_state("watt_fallback", candidate_since=previous_candidate_since)

    # An explicit assumed_state=True is the existing, documented fallback path.
    if assumed_value is True and watt_active:
        return _power_state("watt_fallback")

    # WebOS off/standby wins. Only a fresh high-watt conflict after a confirmed
    # active state gets a short hold; it cannot create a new active state.
    if (
        state_value is not None
        and watt_active
        and state_fresh
        and assumed_value is None
    ):
        if previous in _POWER_ACTIVE_OUTPUTS and current is not None:
            raw_since = prev_states.get(dv.name)
            conflict_since = _as_datetime(
                raw_since.get("conflict_since") if isinstance(raw_since, dict) else None
            )
            if conflict_since is None:
                conflict_since = current
            if (current - conflict_since).total_seconds() <= hold_seconds:
                return _power_state("conflict_hold", conflict_since, state_updated)

        # No previous confirmed activity: publish only a provisional candidate.
        if current is not None:
            return _power_state(_POWER_CANDIDATE_OUTPUT, candidate_since=current)

    return _power_state(
        "webos_off",
        confirmed_state_updated=state_updated if state_fresh else None,
    )


def _derived_names(config: CombinedConfig) -> set[str]:
    return {d.name for d in config.derived_values}


def exposed_derived_names(config: CombinedConfig) -> tuple[str, ...]:
    """Derived-Knoten, die als flache Sensor-Attribute veröffentlicht werden."""
    known = _derived_names(config)
    out: list[str] = []
    for name in (*config.exposed_attributes, *(d.name for d in config.derived_values if d.expose)):
        if name in known and name not in out:
            out.append(name)
    return tuple(out)


def exposed_derived_attributes(config: CombinedConfig, result: CombinedResult) -> dict[str, Any]:
    """Flache, explizit freigegebene Derived-Attribute für den HA-Sensor."""
    return {
        name: result.derived[name]
        for name in exposed_derived_names(config)
        if name in result.derived
    }


def _node_dep_refs(dv: DerivedValue) -> set[str]:
    out: set[str] = set()
    for e in (dv.expr, dv.set_expr, dv.reset_expr):
        if e:
            try:
                out |= refs(parse(e))
            except ExprError:
                pass
    for case in dv.cases:
        try:
            out |= refs(parse(case.when))
        except ExprError:
            pass
    out |= set(dv.atomics)
    out |= {
        source for source in (
            dv.state_source,
            dv.watt_source,
            dv.assumed_state_source,
        ) if source
    }
    return out


def _ordered_derived(config: CombinedConfig) -> list[DerivedValue]:
    """Topo-Sort der derived_values nach Abhängigkeiten (DAG). Zyklus → Listenreihenfolge."""
    names = _derived_names(config)
    by_name = {d.name: d for d in config.derived_values}
    order: list[DerivedValue] = []
    state: dict[str, int] = {}  # 0=visiting, 1=done

    def visit(name: str) -> None:
        if state.get(name) == 1 or name not in by_name:
            return
        if state.get(name) == 0:
            return  # Zyklus — abbrechen, validate meldet es
        state[name] = 0
        dv = by_name[name]
        for dep in _node_dep_refs(dv):
            if dep in names:
                visit(dep)
        state[name] = 1
        order.append(dv)

    for d in config.derived_values:
        visit(d.name)
    # Falls durch Zyklus etwas fehlt: anhängen.
    for d in config.derived_values:
        if d not in order:
            order.append(d)
    return order


def _eval_node(
    dv: DerivedValue, env: dict[str, Any], readings: dict[str, SourceReading],
    config: CombinedConfig, prev_states: dict[str, Any], now: Any,
) -> Any:
    fail_safe = dv.fail_safe or config.fail_safe
    prev = _node_value(prev_states.get(dv.name))
    if dv.kind == NODE_EXPR:
        try:
            v = as_num(eval_expr(dv.expr or "", env))
        except ExprError:
            v = None
        return v if v is not None else _failsafe_value(NODE_EXPR, fail_safe, prev)
    if dv.kind == NODE_GATE:
        try:
            v = as_bool(eval_expr(dv.expr or "", env))
        except ExprError:
            v = None
        return v if v is not None else _failsafe_value(NODE_GATE, fail_safe, prev)
    if dv.kind == NODE_ENUM:
        for case in dv.cases:
            try:
                matched = as_bool(eval_expr(case.when, env))
            except ExprError:
                matched = None
            if matched:
                return _maybe_ref(case.output, env)
        if dv.default is not None:
            return _maybe_ref(dv.default, env)
        return _failsafe_value(NODE_ENUM, fail_safe, prev)
    if dv.kind == NODE_HEALTH:
        worst = "ok"
        source_by_key = {source.key: source for source in config.sources}
        for key in dv.atomics:
            r = readings.get(key)
            if r is None or not r.available or r.value is None:
                source = source_by_key.get(key)
                if source is not None and not source.required:
                    continue
                worst = "problem"
                break
            q = str(r.attributes.get("atomic_quality") or "ok")
            if q == "unavailable" or r.attributes.get("missing_required"):
                worst = "problem"
                break
            if q == "degraded" or r.attributes.get("degraded"):
                worst = "degraded"
        return worst
    if dv.kind == NODE_LATCH:
        set_v = reset_v = None
        try:
            set_v = as_bool(eval_expr(dv.set_expr or "false", env))
        except ExprError:
            set_v = None
        try:
            reset_v = as_bool(eval_expr(dv.reset_expr or "false", env))
        except ExprError:
            reset_v = None
        if set_v:
            return True
        if reset_v:
            return False
        if prev is not None:
            return bool(prev)
        return _failsafe_value(NODE_LATCH, fail_safe, prev)
    if dv.kind == NODE_PREVIOUS:
        return env.get(SELF_REF)
    if dv.kind == NODE_POWER_ARBITRATION:
        return _eval_power_arbitration(dv, readings, prev_states, now)
    return None


def _resolve(ref: str, readings: dict[str, SourceReading], env: dict[str, Any]) -> SourceReading | None:
    if ref in readings:
        return readings[ref]
    if ref in env:
        return _wrap(env[ref])
    return None


def _maybe_ref(output: Any, env: dict[str, Any]) -> Any:
    """Resolve exact ``"${name}"`` outputs and interpolate enum strings."""
    if isinstance(output, str) and re.fullmatch(r"\$\{[^}]+\}", output):
        return env.get(output[2:-1].strip())
    if isinstance(output, str) and "${" in output:
        missing = False

        def repl(match: re.Match[str]) -> str:
            nonlocal missing
            value = env.get(match.group(1).strip())
            if value is None:
                missing = True
                return ""
            return str(value)

        rendered = re.sub(r"\$\{([^}]+)\}", repl, output)
        return None if missing else rendered
    return output


def evaluate_combined(
    config: CombinedConfig,
    readings: dict[str, SourceReading],
    persisted: "CombinedPersisted | None" = None,
    now: Any = None,
) -> CombinedResult:
    """Wertet derived_values (v1.0) + First-Match-Regeln (v0) aus."""
    source_entities: dict[str, str] = {}
    source_states: dict[str, Any] = {}
    source_available: dict[str, bool] = {}
    missing_sources: list[str] = []
    degraded_reason: list[str] = []

    for src in config.sources:
        if not src.entity:
            if not src.required:
                continue
            missing_sources.append(src.key)
            continue
        source_entities[src.key] = src.entity
        reading = readings.get(src.key)
        available = reading is not None and reading.available and reading.value is not None
        source_states[src.key] = reading.value if reading else None
        source_available[src.key] = available
        if not available and src.required:
            degraded_reason.append(f"{src.key}: unavailable")

    prev_state = persisted.last_state if persisted else None
    prev_nodes = dict(persisted.node_states) if persisted else {}

    # ── derived_values: env aufbauen + in Topo-Reihenfolge auswerten ────────
    env: dict[str, Any] = {src.key: _autoscalar(readings.get(src.key)) for src in config.sources}
    env[SELF_REF] = prev_state
    derived_out: dict[str, Any] = {}
    node_states: dict[str, Any] = {}
    for dv in _ordered_derived(config):
        raw_val = _eval_node(dv, env, readings, config, prev_nodes, now)
        val = _node_value(raw_val)
        env[dv.name] = val
        derived_out[dv.name] = val
        node_states[dv.name] = raw_val

    # ── Regeln (v0) — referenzieren Quellen, derived oder ${self} ───────────
    matched: int | None = None
    output = config.default_output
    reason = config.default_reason or "default"
    for index, rule in enumerate(config.rules):
        if _match(_resolve(rule.source, readings, env), rule.op, rule.value):
            matched = index
            output = rule.output
            reason = rule.reason or _auto_reason(rule)
            break

    output = _maybe_ref(output, env)
    if output is None:
        output = _failsafe_output(config.fail_safe, prev_state)

    degraded = bool(degraded_reason) or bool(missing_sources)
    for s in missing_sources:
        degraded_reason.append(f"{s}: missing entity")
    source_quality = derived_out.get("source_quality")
    if source_quality is not None and str(source_quality) != "ok":
        degraded = True
        hint = derived_out.get("degraded_reason_hint")
        if hint:
            degraded_reason.append(str(hint))
        else:
            degraded_reason.append(f"source_quality:{source_quality}")

    return CombinedResult(
        state=coerce_output(output, config.output_type),
        output=output,
        reason=reason,
        matched_rule=matched,
        source_entities=source_entities,
        source_attributes={s.key: s.attribute for s in config.sources if s.attribute},
        source_states=source_states,
        source_available=source_available,
        missing_sources=missing_sources,
        degraded=degraded,
        degraded_reason=degraded_reason,
        derived=derived_out,
        node_states=node_states,
    )


def validate_combined_v1(config: CombinedConfig) -> list[str]:
    """Dry-Run-Validierung: Parse, unbekannte Refs, Zyklen, Zeit-Latch (since=v1.1)."""
    errors: list[str] = []
    names = _derived_names(config)
    source_keys = {s.key for s in config.sources}
    allowed = source_keys | names | {SELF_REF}
    reserved_attributes = {
        "slug", "display_name", "output_type", "output", "reason", "code_legend",
        "source_entities", "source_attributes", "source_states", "source_available",
        "missing_sources", "degraded", "degraded_reason", "derived", "shadow_compare",
    }

    def check_expr(label: str, e: str | None, is_latch: bool = False) -> None:
        if not e:
            return
        try:
            ast = parse(e)
        except ExprError as err:
            errors.append(f"{label}: Parse-Fehler: {err}")
            return
        for r in refs(ast):
            if r not in allowed:
                errors.append(f"{label}: unbekannte Referenz ${{{r}}}")
        v11 = func_names(ast) & {"since"}
        if v11:
            errors.append(
                f"{label}: '{sorted(v11)[0]}' ist v1.1 (Timer/Scheduling) — in v1.0 nicht erlaubt"
            )

    for dv in config.derived_values:
        if dv.kind not in NODE_KINDS:
            errors.append(f"{dv.name}: unbekannter Node-Typ {dv.kind!r}")
            continue
        if dv.kind in (NODE_EXPR, NODE_GATE):
            check_expr(dv.name, dv.expr)
        elif dv.kind == NODE_ENUM:
            if not dv.cases and dv.default is None:
                errors.append(f"{dv.name}: enum braucht cases[] oder default")
            for index, case in enumerate(dv.cases):
                check_expr(f"{dv.name}.cases[{index}].when", case.when)
        elif dv.kind == NODE_LATCH:
            check_expr(f"{dv.name}.set", dv.set_expr, is_latch=True)
            check_expr(f"{dv.name}.reset", dv.reset_expr, is_latch=True)
        elif dv.kind == NODE_HEALTH:
            for a in dv.atomics:
                if a not in source_keys:
                    errors.append(f"{dv.name}: health-Quelle {a!r} ist keine Source")
        elif dv.kind == NODE_POWER_ARBITRATION:
            for label, source in (
                ("state_source", dv.state_source),
                ("watt_source", dv.watt_source),
                ("assumed_state_source", dv.assumed_state_source),
            ):
                if source and source not in source_keys:
                    errors.append(f"{dv.name}: {label} {source!r} ist keine Source")
            if not dv.state_source or not dv.watt_source:
                errors.append(
                    f"{dv.name}: power_arbitration braucht state_source und watt_source"
                )

    for name in config.exposed_attributes:
        if name not in names:
            errors.append(f"exposed_attributes: unbekannter derived_values-Knoten {name!r}")
    for name in exposed_derived_names(config):
        if name in reserved_attributes:
            errors.append(f"{name}: exposed attribute kollidiert mit reserviertem Sensor-Attribut")

    # Zyklus-Erkennung
    by_name = {d.name: d for d in config.derived_values}
    visiting: set[str] = set()
    done: set[str] = set()

    def dfs(name: str, stack: list[str]) -> None:
        if name in done or name not in by_name:
            return
        if name in visiting:
            errors.append(f"Zyklus in derived_values: {' → '.join(stack + [name])}")
            return
        visiting.add(name)
        for dep in _node_dep_refs(by_name[name]):
            if dep in names:
                dfs(dep, stack + [name])
        visiting.discard(name)
        done.add(name)

    for d in config.derived_values:
        dfs(d.name, [])
    return errors


def _auto_reason(rule: CombinedRule) -> str:
    if rule.op == COMBINED_OP_UNAVAILABLE:
        return f"{rule.source} unavailable"
    return f"{rule.source} {rule.op} {rule.value}"


# ─────────────────────────────────────────────────────────────────────────────
# DERIVED BINARY SENSORS
# ─────────────────────────────────────────────────────────────────────────────


def evaluate_derived(
    derived: DerivedSensor,
    config: CombinedConfig,
    readings: dict[str, SourceReading],
    result: CombinedResult,
) -> bool:
    """Wertet einen abgeleiteten Binary-Sensor aus.

    `target` kann sein:
    - "__output__" — vergleicht gegen den Combined-Output
    - ein derived_values-Name — vergleicht gegen diesen Zwischenwert
    - ein source.key — vergleicht gegen diese eine Quelle
    - eine Rolle — any-match über alle Quellen dieser Rolle
    """
    if derived.target == "__output__":
        reading = SourceReading(value=result.state, numeric=None, available=True)
        return _match(reading, derived.op, derived.value)

    if derived.target in result.derived:
        return _match(_wrap(result.derived[derived.target]), derived.op, derived.value)

    source_keys = {s.key for s in config.sources}
    if derived.target in source_keys:
        return _match(readings.get(derived.target), derived.op, derived.value)

    # Rolle → any-match
    role_keys = [s.key for s in config.sources if s.role == derived.target]
    return any(_match(readings.get(k), derived.op, derived.value) for k in role_keys)


# ─────────────────────────────────────────────────────────────────────────────
# PARSING (aus Storage / Import / WebSocket)
# ─────────────────────────────────────────────────────────────────────────────


def parse_combined(slug: str, raw: Any) -> CombinedConfig | None:
    """Parst eine gespeicherte Combined-Konfiguration robust.

    Ungültige Teile werden übersprungen statt zu crashen (LH §4: nie hart
    brechen). Returns None nur, wenn `raw` gar kein Mapping ist.
    """
    if not isinstance(raw, dict):
        return None

    def optional_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    output_type = raw.get("output_type", OUTPUT_TYPE_ENUM)
    if output_type not in (
        OUTPUT_TYPE_ENUM,
        OUTPUT_TYPE_CODE,
        OUTPUT_TYPE_BOOLEAN,
        OUTPUT_TYPE_NUMBER,
    ):
        output_type = OUTPUT_TYPE_ENUM

    sources: list[CombinedSource] = []
    for item in raw.get("sources") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or item.get("role") or "").strip()
        if not key:
            continue
        sources.append(
            CombinedSource(
                key=key,
                role=str(item.get("role") or "custom"),
                entity=(str(item["entity"]) if item.get("entity") else None),
                attribute=(
                    str(item["attribute"]).strip()
                    if item.get("attribute")
                    else None
                ),
                required=bool(item.get("required", True)),
            )
        )

    rules: list[CombinedRule] = []
    for item in raw.get("rules") or []:
        if not isinstance(item, dict):
            continue
        op = item.get("op")
        if op not in COMBINED_OPERATOR_CHOICES:
            continue
        src = str(item.get("source") or "").strip()
        if not src:
            continue
        rules.append(
            CombinedRule(
                source=src,
                op=op,
                value=(str(item["value"]) if item.get("value") is not None else None),
                output=item.get("output"),
                reason=(str(item["reason"]) if item.get("reason") else None),
            )
        )

    derived: list[DerivedSensor] = []
    for item in raw.get("derived") or []:
        if not isinstance(item, dict):
            continue
        dslug = str(item.get("slug") or "").strip()
        if not dslug:
            continue
        op = item.get("op") or COMBINED_OP_EQ
        if op not in COMBINED_OPERATOR_CHOICES:
            op = COMBINED_OP_EQ
        derived.append(
            DerivedSensor(
                slug=dslug,
                name=str(item.get("name") or dslug),
                object_id=(
                    str(item["object_id"]).strip()
                    if item.get("object_id")
                    else None
                ),
                device_class=(
                    str(item["device_class"]) if item.get("device_class") else None
                ),
                target=str(item.get("target") or "__output__"),
                op=op,
                value=(str(item["value"]) if item.get("value") is not None else None),
            )
        )

    derived_values: list[DerivedValue] = []
    for item in raw.get("derived_values") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        kind = str(item.get("kind") or "").strip()
        if not name or kind not in NODE_KINDS:
            continue
        atomics = item.get("atomics") or []
        cases: list[DerivedCase] = []
        for case in item.get("cases") or []:
            if not isinstance(case, dict):
                continue
            when = str(case.get("when") or "").strip()
            if not when:
                continue
            cases.append(
                DerivedCase(
                    when=when,
                    output=case.get("output", case.get("value")),
                )
            )
        derived_values.append(
            DerivedValue(
                name=name,
                kind=kind,
                expr=(str(item["expr"]) if item.get("expr") is not None else None),
                cases=tuple(cases),
                default=item.get("default"),
                set_expr=(str(item["set"]) if item.get("set") is not None else None),
                reset_expr=(
                    str(item["reset"]) if item.get("reset") is not None else None
                ),
                atomics=tuple(str(a) for a in atomics if a),
                state_source=(
                    str(item["state_source"]) if item.get("state_source") else None
                ),
                watt_source=(
                    str(item["watt_source"]) if item.get("watt_source") else None
                ),
                assumed_state_source=(
                    str(item["assumed_state_source"])
                    if item.get("assumed_state_source") else None
                ),
                active_states=tuple(
                    str(state)
                    for state in (item.get("active_states") or ("on", "playing"))
                ),
                threshold=optional_float(item.get("threshold")),
                freshness_seconds=optional_float(item.get("freshness_seconds")),
                conflict_hold_seconds=optional_float(item.get("conflict_hold_seconds")),
                fail_safe=(str(item["fail_safe"]) if item.get("fail_safe") else None),
                expose=bool(item.get("expose")),
            )
        )

    legend = raw.get("code_legend")
    code_legend = dict(legend) if isinstance(legend, dict) else {}
    diagnostics = raw.get("diagnostics") if isinstance(raw.get("diagnostics"), dict) else {}
    fail_safe = str(raw.get("fail_safe") or diagnostics.get("fail_safe") or FAIL_SAFE_UNKNOWN)
    exposed_raw = raw.get("exposed_attributes") or []
    exposed_attributes = tuple(str(name) for name in exposed_raw if name) if isinstance(exposed_raw, list) else ()

    return CombinedConfig(
        slug=slug,
        display_name=str(raw.get("display_name") or slug),
        output_type=output_type,
        sources=tuple(sources),
        rules=tuple(rules),
        default_output=raw.get("default_output"),
        default_reason=(
            str(raw["default_reason"]) if raw.get("default_reason") else None
        ),
        code_legend=code_legend,
        derived=tuple(derived),
        derived_values=tuple(derived_values),
        exposed_attributes=exposed_attributes,
        fail_safe=fail_safe,
    )
