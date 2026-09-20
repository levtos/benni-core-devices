# Issue 50: Event-based WebOS activity remains authoritative

The TV master treats an available explicit WebOS `on` or `playing` value as
authoritative activity until Home Assistant publishes a different value or the
entity becomes unavailable. The age of an unchanged state is not a liveness
failure: Home Assistant state entities are event-based and do not refresh on a
fixed cadence merely to repeat the same value.

The configured freshness window still applies to watt evidence and to the
existing off/standby conflict and fallback paths. This keeps the 50 W threshold,
cold-start candidate, conflict hold, and unavailable-source behavior intact.

This distinction prevents a running TV from becoming `off` during a short
sub-50 W content-dependent power dip while WebOS still reports `on`.
