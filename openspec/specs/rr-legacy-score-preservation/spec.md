# rr-legacy-score-preservation Specification

## Purpose
Diese Fähigkeit verhindert, dass ein sicherheitsbedingtes Zurückhalten uneindeutiger WHOOP-5-RR-Rohdaten
bereits vorhandene historische HRV- und Recovery-Ergebnisse beim Upgrade oder Restore zerstört.

## Requirements

### Requirement: Legacy-Ergebnisse überleben Upgrade und Restore

Das System MUST vorhandene berechnete HRV- und Recovery-Werte gemeinsam und unverändert erhalten, wenn
ein WHOOP-5-Re-Score ausschließlich deshalb kein HRV liefert, weil im exakten Lesefenster vorhandene
unmarkierte Legacy-RR-Daten durch die strikte Transportregel zurückgehalten werden.

#### Scenario: Vorhandener Snapshot bleibt erhalten

- **GIVEN** ein berechneter Tag mit HRV und Recovery sowie passende unmarkierte WHOOP-5-RR-Daten
- **WHEN** der nach Upgrade oder Restore ausgelöste Re-Score läuft
- **THEN** bleiben HRV und Recovery exakt erhalten, während andere Tagesfelder aktuell berechnet werden

#### Scenario: Gewöhnlich fehlende RR bleiben leer

- **GIVEN** ein Tag ohne zurückgehaltene Legacy-RR-Daten oder ohne vorhandenen HRV-Snapshot
- **WHEN** der Re-Score kein HRV berechnen kann
- **THEN** erfindet oder konserviert das System weder HRV noch Recovery

#### Scenario: Andere Geräte bleiben unverändert

- **GIVEN** unmarkierte RR-Daten eines bestätigten WHOOP-4-Geräts oder einer anderen Marke
- **WHEN** der Re-Score läuft
- **THEN** bleibt deren bisherige RR- und Score-Semantik unverändert

### Requirement: Sichere Neuberechnung beendet den Bestandsschutz

Das System MUST den Legacy-Bestandsschutz beenden, sobald das betroffene Fenster einen eindeutig markierten,
vom WHOOP-5-Scoring akzeptierten RR-Transport enthält.

#### Scenario: Markierter Transport ersetzt den Snapshot

- **GIVEN** ein zuvor geschützter Tag und neu synchronisierte markierte, ausreichende RR-Daten
- **WHEN** der nächste Re-Score läuft
- **THEN** ersetzen frisch berechnetes HRV und frisch berechnete Recovery den Legacy-Snapshot

#### Scenario: Unzureichende markierte Daten werden nicht kaschiert

- **GIVEN** ein zuvor gespeicherter Tageswert und markierte, aber fachlich unzureichende RR-Daten
- **WHEN** der Re-Score kein gültiges HRV ergibt
- **THEN** wird der alte Snapshot nicht durch die Legacy-Regel konserviert

#### Scenario: Folge-Re-Score bleibt stabil

- **GIVEN** ein geschützter oder nach Promotion frisch berechneter Tag ohne weitere Eingabeänderung
- **WHEN** ein weiterer Re-Score läuft
- **THEN** bleiben Ergebnis und Provenienz stabil
