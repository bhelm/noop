# Schritte-Detailansichten verdichten

Revision: r3

## Why

Lange Schritte-Zeiträume zeigen heute tägliche Werte und werden dadurch schwer lesbar; zugleich können Android und Apple bei Fenstergrenzen voneinander abweichen. Die Ansichten sollen die zeitliche Auflösung passend zum gewählten Zeitraum verdichten, ohne fehlende Messtage als Nullwerte zu erfinden.

## What Changes

- Schritte-Detailansichten verwenden auf Android und Apple unabhängig von der allgemeinen Diagrammpräferenz immer Balken.
- W, 2W, 3W und M zeigen Tageswerte, 3M zeigt Wochenmittel, 6M, 1Y und ALL zeigen Monatsmittel.
- Wochen beginnen montags; Monate entsprechen lokalen Kalendermonaten. Jeder Mittelwert teilt ausschließlich durch tatsächlich beobachtete Messtage.
- Beide Plattformen verwenden denselben kalenderbasierten Fenster- und Verdichtungsvertrag samt gemeinsamem JSON-Orakel. Dadurch wird ein positionsbasiertes `takeLast(N)` als fachliche Fensterdefinition ausgeschlossen.
- Wochen- und Monatsbalken tragen denselben kanonischen Bucket-Anker und eine verständliche Beschriftung als Mittelwert pro beobachtetem Tag; ein einzelner gültiger Bucket bleibt ein Balkendiagramm.

## Capabilities

### New Capabilities

- `steps-detail-density`: Plattformgleiche, kalenderbasierte Fensterung, Verdichtung und Balkendarstellung für Schritte-Detailansichten.

### Modified Capabilities

Keine bestehende OpenSpec-Capability ist am Basisstand vorhanden.

## Non-goals

- Änderungen an Erfassung, Quellenpriorität, Speicherung oder Berechnung täglicher Schrittwerte.
- Verdichtung anderer Vitalwerte oder Änderungen an deren Diagrammpräferenz.
- Neue Zeiträume, Navigation, allgemeine Texte außerhalb der notwendigen Bucket-/Accessibility-Beschriftung, Datenbankfelder, Migrationen oder Telemetrie.
- Deployment, Release, Merge oder rückwirkende Datenkorrektur.

## Impact

Betroffen sind die reinen Projektions-/Fensterhelfer, Androids Schritte-Detailfluss und Apples generische Metrik-Detailansicht sowie gezielte Tests und Paritätsartefakte. Rohdaten, persistente Daten und externe Schnittstellen bleiben unverändert. Das Risiko ist hoch, weil ein sichtbarer Laufzeitvertrag auf zwei Plattformen sowie dessen Paritätsnachweis geändert werden.
