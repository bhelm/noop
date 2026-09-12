---
type: Decision
title: Bestandsschutz für uneindeutige WHOOP-5-RR-Altdaten
description: Historische HRV- und Recovery-Ergebnisse bleiben erhalten, ohne unmarkierte RR-Rohdaten spekulativ neu auszuwerten.
tags: [whoop5, rr, hrv, recovery, upgrade, restore]
status: draft
generated:
  by: codex-orchestrator
sources:
  - id: archived-design
    type: local
    target: ../openspec/changes/archive/2026-09-12-rr-legacy-bestandsschutz/design.md
---

# Entscheidung

Unmarkierte WHOOP-5-RR-Zeilen aus Versionen vor 11.6 bleiben vom Scoring ausgeschlossen, weil ihr
Transport und damit ihre Einheit nachträglich nicht sicher bestimmbar ist. Eine pauschale Konvertierung
oder Wertebereichsheuristik ist unzulässig.

Wenn ein Re-Score ausschließlich durch dieses Gate kein HRV liefert, bewahrt NOOP ein bereits vorhandenes
berechnetes HRV-/Recovery-Paar als unveränderten Legacy-Snapshot. Alle anderen Tageswerte und Ableitungen
werden aus aktuellen, akzeptierten Eingaben berechnet. Ein markierter Scoring-Transport beendet den Schutz.

# Geltungsbereich

- Direkter Upgrade-Pfad aus 11.5 und Restore einer intakten älteren Sicherung.
- Apple und Android mit derselben konservativen Fenster- und Gerätepolicy.
- Keine Rekonstruktion von Werten, die 11.6.0 bereits geleert hat; dafür bleibt eine ältere Sicherung oder
  ein erneuter Strap-Offload erforderlich.

# Nachweis und Restgrenzen

Android-Engine-, Store-, Schema-, Transaktions- und Bytecode-Tests sowie ein plattformübergreifender
Quellvertrag sind lokal grün. Apple-Kompilation, echte Nutzersicherung und Hardware-Offload benötigen
CI/macOS beziehungsweise freiwillige Nutzertests.

[^archived-design]: [Archiviertes Design](../openspec/changes/archive/2026-09-12-rr-legacy-bestandsschutz/design.md)
