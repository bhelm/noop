# Capability: Steps detail density

Revision: r3

## Purpose

Diese Capability definiert die plattformgleiche, kalenderbasierte Verdichtung und Balkendarstellung von Schritten in allen auswählbaren Detailzeiträumen.

## ADDED Requirements

### Requirement: Schritte als Balken

Die Schritte-Detailansicht SHALL auf Android und Apple unabhängig von der allgemeinen Diagrammpräferenz Balken darstellen, ohne den Stil anderer Metriken zu verändern.

#### Scenario: Android erzwingt Balken

- **WHEN** auf Android eine Schritte-Detailansicht bei gewählter Linienpräferenz geöffnet wird
- **THEN** wird die Schritte-Serie als Balken dargestellt
- **AND** eine Nicht-Schritte-Detailansicht behält die gewählte Linienpräferenz

#### Scenario: Apple erzwingt Balken

- **WHEN** auf Apple eine Schritte-Detailansicht bei gewählter Linienpräferenz geöffnet wird
- **THEN** wird die Schritte-Serie als Balken dargestellt
- **AND** eine Nicht-Schritte-Detailansicht behält die gewählte Linienpräferenz
- **AND** das gilt für gemessene oder importierte `steps` unabhängig von ihrer Quelle sowie für `steps_est`

#### Scenario: Einzelner gültiger Bucket bleibt ein Balken

- **GIVEN** nach Validierung und Verdichtung verbleibt genau ein Schritte-Bucket
- **WHEN** Android oder Apple die Detailansicht rendert
- **THEN** wird dieser eine Bucket als Balken statt als Nur-Letzter-Wert- oder Kein-Trend-Zustand dargestellt

### Requirement: Kalenderbasierte Zeitraumauflösung

Die Schritte-Detailansicht SHALL W, 2W, 3W und M als Tageswerte, 3M als Montag-beginnende Wochenmittel sowie 6M, 1Y und ALL als lokale Kalendermonatsmittel projizieren.

#### Scenario: Tagesauflösung für kurze Zeiträume

- **WHEN** W, 2W, 3W oder M gewählt ist
- **THEN** entspricht jeder Balken einem beobachteten lokalen Kalendertag innerhalb der inklusiven 7-, 14-, 21- beziehungsweise 30-Tage-Grenze relativ zur neuesten validen Messung

#### Scenario: Wochenauflösung für drei Monate

- **WHEN** 3M gewählt ist
- **THEN** werden Messungen innerhalb der inklusiven 90-Tage-Grenze nach lokalen Kalenderwochen von Montag bis Sonntag gruppiert

#### Scenario: Monatsauflösung für lange Zeiträume

- **WHEN** 6M, 1Y oder ALL gewählt ist
- **THEN** werden Messungen für 6M und 1Y innerhalb der inklusiven 180- beziehungsweise 365-Tage-Grenze und für ALL ohne Untergrenze nach lokalem Jahr und Monat gruppiert
- **AND** Apple lädt für ALL die vollständige Schritte- und Provenance-Historie ohne das Standardfenster

#### Scenario: Bucket-Anker sind plattformgleich

- **WHEN** Tages-, Wochen- oder Monatsbuckets projiziert werden
- **THEN** ist ihr Anzeigezeitpunkt der Tag selbst, der Montag der Kalenderwoche beziehungsweise der erste Tag des lokalen Kalendermonats

### Requirement: Beobachtungstreuer Mittelwert

Jeder verdichtete Schritte-Bucket MUST seinen Mittelwert aus der Summe vorhandener Tageswerte geteilt durch die Anzahl tatsächlich beobachteter Messtage bilden und darf fehlende Tage nicht als Nullwerte zählen.

#### Scenario: Fehlende Tage verändern den Nenner nicht

- **GIVEN** ein Wochen- oder Monatsbucket enthält Werte für drei Tage und für die übrigen Kalendertage keine Messung
- **WHEN** der Bucket verdichtet wird
- **THEN** ist sein ungerundeter Mittelwert die Summe der drei Werte geteilt durch drei
- **AND** es wird kein zusätzlicher Nullwert erzeugt

#### Scenario: Beobachtete Null ist ein Messtag

- **GIVEN** ein Bucket enthält einen beobachteten Tageswert von null und mindestens einen positiven Tageswert
- **WHEN** der Bucket verdichtet wird
- **THEN** zählt der Nullwert als beobachteter Messtag im Nenner
- **AND** nur ein tatsächlich fehlender Tag bleibt ungezählt

### Requirement: Deterministische Grenzfälle

Die Schritte-Projektion SHALL pro lokalem Tag den letzten finiten nichtnegativen Eingabewert verwenden und positive Bucket-Mittelwerte nach der Regel `floor(value + 0.5)` auf ganze Schritte runden.

#### Scenario: Doppelte Tage und positive Rundung

- **GIVEN** ein Tag erscheint mehrfach und der letzte gültige Wert führt für seinen Bucket zu einem positiven Mittelwert mit Nachkommaanteil genau 0,5
- **WHEN** die Serie projiziert wird
- **THEN** ersetzt der letzte gültige Tageswert den früheren Wert
- **AND** der Mittelwert wird zur nächsthöheren ganzen Schrittzahl gerundet

#### Scenario: Ungültige Tageskennungen werden ausgeschlossen

- **GIVEN** Eingaben enthalten Formatfehler, nicht existente Kalendertage und valide Schaltjahrestage
- **WHEN** die Serie projiziert wird
- **THEN** werden nur strikt valide proleptisch-gregorianische `yyyy-MM-dd`-Tage als Anker oder Bucket verwendet

### Requirement: Plattformparität

Android und Apple MUST denselben versionierten JSON-Oracle-Vertrag für Fenstergrenzen, Buckets, beobachtete Tage, Duplikate und Rundung erfüllen und durch die bestehende Parity-Governance als Zwillinge erfasst sein.

#### Scenario: Gemeinsames Oracle auf beiden Plattformen

- **WHEN** dieselben synthetischen Tageswerte und Bereichsauswahlen durch die Kotlin- und Swift-Projektion laufen
- **THEN** stimmen Bucket-Schlüssel, Anzeigezeitpunkte, Balkenwerte, Summen und Zähler beobachteter Tage für jeden Fall exakt überein

### Requirement: Konsistente Detaildarstellung

Jede Plattform SHALL Diagramm, sichtbare Bucket-Zusammenfassung und Accessibility-Zusammenfassung aus derselben projizierten Serie ableiten, während die Lesungstabelle die zugrunde liegenden Tagesmessungen beibehält.

#### Scenario: Android nutzt eine projizierte Serie

- **WHEN** Android eine verdichtete Schritte-Ansicht rendert
- **THEN** stimmen Balken, Hero, Latest-Wert und alle Bucket-Zusammenfassungen in Anzahl und Wert überein
- **AND** die Lesungstabelle gibt weiterhin die vorhandenen Tagesmessungen statt erfundener Bucket-Messungen aus
- **AND** Wochen-/Monatsbalken benennen Zeitraum und Wert als durchschnittliche Schritte pro beobachtetem Tag

#### Scenario: Apple nutzt eine projizierte Serie

- **WHEN** Apple eine verdichtete Schritte-Ansicht rendert
- **THEN** stimmen Balken und alle Bucket-Zusammenfassungen in Anzahl und Wert überein
- **AND** die Lesungstabelle gibt weiterhin die vorhandenen Tagesmessungen statt erfundener Bucket-Messungen aus
- **AND** Wochen-/Monatsbalken benennen Zeitraum und Wert als durchschnittliche Schritte pro beobachtetem Tag

### Requirement: Kalendergleicher Vorperiodenvergleich

Die Apple-Schritte-Detailansicht SHALL für endliche Zeiträume den sichtbaren Vorperiodenvergleich aus der unmittelbar vorhergehenden gleich langen lokalen Kalenderperiode und derselben Verdichtungsregel ableiten.

#### Scenario: Sparse Vorperiode behält Kalendersemantik

- **GIVEN** aktuelle und vorhergehende Periode enthalten unterschiedlich viele beobachtete Tage
- **WHEN** Apple den Vergleich für 3M, 6M oder 1Y berechnet
- **THEN** werden beide Perioden über gleich lange angrenzende Kalendergrenzen statt über gleiche Punktzahlen bestimmt
- **AND** für ALL wird kein Vorperiodenvergleich angezeigt
