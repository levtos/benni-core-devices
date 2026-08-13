# Issue #21 — TV-Quellen-Freshness

Der TV-Master bleibt Integration-first, solange WebOS einen frischen,
widerspruchsfreien Zustand liefert. Die konfigurierte `power_arbitration`
verbindet WebOS-State, Wattquelle und `assumed_state`; `off` plus frische
Leistung ab 50 W ohne verlässliches `assumed_state` wird für 20 Sekunden als
Quellenkonflikt behandelt:

- Ein bereits aktiver TV bleibt während dieses Fensters aktiv, damit ein
  kurzer WebOS-Flap keine `active → off → active`-Kante erzeugt.
- Ohne vorherige Aktivität wird keine Aktivierung erfunden.
- Danach gewinnt frisches WebOS-`off` weiterhin; nur ein stale WebOS-Wert darf
  auf den frischen Watt-Fallback fallen.
- Unter 50 W, `unknown`/`unavailable` oder stale Watt werden nicht pauschal als
  aktiv interpretiert.
- Die Arbitration speichert nur den Konfliktbeginn im bestehenden Combined-
  Node-State; sie führt keine neue Policy-, Audio- oder Resume-Ownership ein.

Das 20-Sekunden-Fenster ist ausschließlich für `media_device/tv` konfiguriert;
andere Core-Devices behalten ihre bestehende Value-Presence-Semantik. Die
Änderung gehört zu `Levtos/benni-core-devices#41`; die konkrete TV-Master-
Konfiguration in `einhornzentrale` wird von `Levtos/benni_media_state#21`
konsumiert. Kein Policy-/Apply- oder Live-Change.
