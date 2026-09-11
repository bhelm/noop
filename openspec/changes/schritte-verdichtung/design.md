# Design — Schritte-Detailansichten verdichten

Revision: r3

## Context

Android baut die Vital-Detailansicht über `HealthVitalDetailLogic.kt` und `HealthScreen.kt` und zeichnet über `Charts.kt`; Apple baut die generische Ansicht in `MetricExplorerView.swift` und zeichnet über `TrendChart.swift`. Tagesfenster existieren bereits paarig in `LocalDayWindows`, und `ActivityHeatmap` etabliert Montag als Wochenanfang. Der heutige Android-Fallback `takeLast(N)` ist positionsbasiert, während Apple seine Fenster über Datumsgrenzen bestimmt; für sparse Daten ist das kein gleichwertiger Vertrag.

## Entscheidungsvorgaben

- E1: Alle Schritte-Detailansichten auf Android und Apple erzwingen Balken. Quelle: Nutzeranforderung im Handoff.
- E2: W, 2W, 3W und M verwenden Tageswerte; 3M Wochenwerte; 6M, 1Y und ALL Monatswerte. Quelle: Nutzeranforderung im Handoff.
- E3: Wochen- und Monatswerte sind durchschnittliche Schritte pro tatsächlich beobachtetem Messtag; fehlende Tage sind niemals Null. Quelle: Nutzeranforderung im Handoff.
- E4: Plattformparität ist bindend und wird nach bestehenden Guard-/Oracle-Konventionen bewiesen. Quelle: Nutzeranforderung im Handoff.

## Architecture

Eine kleine, pure und auf beiden Plattformen spiegelbildliche Schritte-Projektion liegt zwischen den bereits zusammengeführten Tageslesungen und dem Chartmodell. Sie erhält sortierte Tageslesungen, Auswahl und Ankertag und liefert Balkenpunkte mit Bucket-Schlüssel, Anzeigezeitpunkt, Summe, Zahl beobachteter Tage und gerundetem Mittel. Die UI entscheidet nur: Schritte nutzen diese Projektion und Balken; alle anderen Metriken behalten den bestehenden Pfad.

Der Kalendervertrag arbeitet auf strikt validierten proleptisch-gregorianischen lokalen `yyyy-MM-dd`-Schlüsseln. Endlicher Zeitraum bedeutet die inklusive Folge lokaler Kalendertage bis zum neuesten validen Messdatum: 7, 14, 21, 30, 90, 180 oder 365 Tage. ALL hat keine Untergrenze. Apple lädt bei Auswahlwechsel neu und verwendet für Schritte-ALL `resolvedSeries(..., fullHistory: true)` als gemeinsame Quelle für Werte und Provenance; damit wird der begrenzte In-Memory-Tagescache umgangen. Das ersetzt weder die vorhandene Auswahlfreigabe noch die Quellenpriorität. Formatfehler und nicht existente Kalendertage werden weder Anker noch Bucket; sie bleiben im bestehenden Roh-/Tabellenpfad sichtbar, sind aber aus der Chartprojektion ausgeschlossen.

## Data and control flow

1. Der vorhandene Plattformpfad führt die Schrittquellen zu höchstens einer fachlichen Lesung je Tag zusammen.
2. Die gemeinsame Fenstersemantik filtert anhand inklusiver lokaler Tagesgrenzen relativ zum neuesten validen Tag, nicht anhand der Zahl vorhandener Messungen.
3. W bis M erzeugen einen Bucket pro beobachtetem Tag. 3M gruppiert Montag bis Sonntag und verwendet den Montag als kanonischen Anzeigezeitpunkt. 6M bis ALL gruppieren nach lokalem Jahr und Monat und verwenden dessen ersten Tag. Ein Tagesbucket verwendet seinen Tag. Diese Anker sind Teil des JSON-Orakels.
4. Ein Bucket enthält nur vorhandene Tageslesungen. `mean = sum / observedDayCount`; ein komplett leerer Bucket wird nicht als Nullbalken erzeugt.
5. Der positive Mittelwert wird mit `floor(x + 0.5)` auf ganze Schritte gerundet. Damit sind `.5`-Fälle unabhängig von sprachspezifischen Banker-Rounding-Defaults gleich und entsprechen vorhandenen positiven Schritte-Rundungserwartungen.
6. Chart, Hero, Latest-Wert, sichtbare Statistik und Accessibility-Zusammenfassung konsumieren dieselbe projizierte Serie. Wochen-/Monatsbalken benennen ihren Zeitraum und den Wert als durchschnittliche Schritte pro beobachtetem Tag; die Plattformtests prüfen diese Bedeutung mit sparse Buckets. Die Lesungstabelle darf die zugrunde liegenden Tageslesungen weiter zeigen; sie darf verdichtete Balken nicht als einzelne Messungen ausgeben.
7. Null gültige Buckets zeigen den bestehenden Leerzustand. Ab einem gültigen Bucket wird ein Balkendiagramm gerendert, auch wenn nur eine Rohlesung oder nach Verdichtung nur ein Bucket verbleibt.
8. Apples `Delta vs prev` verwendet für Schritte die unmittelbar vorhergehende gleich lange lokale Kalenderperiode, die unabhängig vom aktuellen Bucket- oder Punktzähler gefenstert und anschließend mit derselben Projektion verdichtet wird. Für ALL bleibt der Vergleich wie bisher aus.

## Compatibility

Andere Vitalwerte sowie Schritte-Erfassung, Quellenauflösung und Tageswerte bleiben unverändert. Der erzwungene Balkenstil gilt nur für die fachliche Schritte-Metrik. Apple behandelt dafür `steps` und `steps_est` als Schritte-Detail, bei `steps` unabhängig von der Quelle einschließlich WHOOP, Apple Health und Xiaomi; Android wendet denselben fachlichen statt quellenbezogenen Vertrag an. Vorhandene Bereichsbezeichner und Freischaltlogik bleiben erhalten.

## Decisions

### D1 — Kalenderfenster statt Anzahl vorhandener Punkte

Gemäß E2/E3 verwenden beide Plattformen inklusive lokale Datumsgrenzen. Ein `takeLast(N)` würde bei Lücken mehr als N Kalendertage einschließen und die Semantik der Auswahl verändern. Revisit: nur wenn das Produkt die Auswahl ausdrücklich als „letzte N Messungen“ neu definiert.

### D2 — Montag und lokaler Kalendermonat

Wochen folgen der bereits plattformgleichen `ActivityHeatmap`-Konvention Montag–Sonntag; Monate folgen Jahr-Monat des lokalen Tages. Alternative rollierende Siebentagesblöcke wurden verworfen, weil „Wochenwerte“ sonst beim Ankertag ihre Bedeutung wechseln. Revisit: nur bei einer produktweiten, nutzerkonfigurierbaren Wochenkonvention.

### D3 — Deduplizierung: letzter Eintrag gewinnt

Falls ein vorgeschalteter Pfad entgegen seinem Vertrag mehrere Werte desselben Tages liefert, gewinnt nach stabiler Eingabereihenfolge der letzte finite, nichtnegative Wert. Summieren würde doppelt zählen; Mittelung würde einen erfundenen Tageswert schaffen. Der Default ist deterministisch, lokal begrenzt und verdeckt die bestehende Quellenpriorität nicht. Tests machen den Fall sichtbar. Revisit: wenn Tageslesungen eine explizite Versions- oder Qualitätskennung erhalten.

### D4 — Positive Rundung: half-up

Mittelwerte werden erst nach Division mit `floor(x + 0.5)` gerundet. Trunkierung verzerrt systematisch nach unten, plattformspezifisches Standardrunden kann `.5` unterschiedlich behandeln. Da Schritte nichtnegativ sind, genügt die positive Definition. Rohsumme und Zähler bleiben für Tests erhalten. Revisit: bei produktweiter Einführung dezimaler Schritteanzeigen.

### D5 — Gemeinsames Oracle plus Guard

Gemäß E4 ist `android/app/src/test/resources/steps_detail_density_oracle.json` das eine kanonische versionierte JSON-Fixture für Fenster-, Bucket-, Anzeigeanker-, Lücken-, Nullwert-, strikt ungültige Kalenderdaten-, Duplikat- und Rundungsfälle. Der Swift-Test liest wie der bestehende `LocalDayWindowsTests`-Vertrag genau diese Datei über den Repository-Root; dadurch ist keine zweite Kopie und keine Swift-Package-Ressource nötig. Kotlin liest dieselbe Test-Resource. Paarige Source-Annotationen werden ergänzt; abgeleitete Paritäts-Snapshots werden ausschließlich über den Guard-Refresh erzeugt und danach mit Ledger und Offline-Ratchet geprüft. Rein gleichnamige Unit-Tests genügen nicht. Revisit: wenn ein generierter gemeinsamer Kern beide Implementierungen ersetzt.

### D6 — Vorperiodenvergleich bleibt kalenderbasiert

Für Schritte wird die Vorperiode aus derselben Zahl lokaler Kalendertage unmittelbar vor der aktuellen Periode gebildet, nicht aus derselben Zahl vorhandener Punkte. Dadurch bleibt die sichtbare Aussage bei sparse Wochen-/Monatsdaten wahr. ALL hat keine endliche Vorperiode und zeigt keinen Vergleich. Revisit: wenn der Vorperiodenvergleich produktweit neu definiert wird.

## Security and privacy

Keine neue Berechtigung, kein Netzwerkzugriff und keine zusätzliche Persistenz. Fixtures enthalten ausschließlich synthetische Tageswerte. Es entsteht keine neue Sicherheits- oder Datenschutzgrenze.

## Migration

Keine Schema-, Daten- oder Konfigurationsmigration. Bereits gespeicherte Tageswerte werden nur bei der Darstellung neu projiziert.

## Rollout and rollback

Rollout erfolgt mit dem normalen App-Build nach grünen Plattform- und Paritätsnachweisen. Rollback ist die gemeinsame Rücknahme der Shared-, Android- und Apple-Änderungen; keine Daten müssen zurückmigriert werden. Eine einseitige Plattformrücknahme ist wegen E4 nicht zulässig.

## Observability

Keine Produktionstelemetrie. Nachweis sind deterministische Unit-/Oracle-Tests, Parity-Governance und je ein Plattformtest am produktiven Detailpfad. Sichtbare manuelle Stichprobe: dieselben synthetischen sparse Daten ergeben gleiche Balkenzahl und Werte.

## Risks

- Zeitzonen-/DST-Grenzen könnten bei `Date`/`Instant`-Umwegen abweichen; deshalb bleibt die Bucketlogik auf lokalen Tageskennungen und bestehenden Kalenderprimitiven.
- Generische Statistik oder Hover-/Accessibility-Texte könnten weiterhin Tagespunkte statt Buckets lesen; deshalb muss je Plattform ein Integrationstest denselben Projektionsoutput am Renderer-Rand belegen.
- ALL kann sehr lange Serien umfassen; Monatsaggregation ist linear in der Eingabe und materialisiert höchstens einen Balken pro beobachtetem Monat.
- Fehlerhafte Duplikate könnten durch „letzter gewinnt“ verdeckt werden; ein gezielter Test und die unveränderte vorgelagerte Merge-Verantwortung begrenzen dieses Risiko.

## Completion criteria

Alle Szenarien der Acceptance-Matrix sind proven, die Shared-Fixtures laufen auf beiden Plattformen, die produktiven Detailpfade erzwingen nur für Schritte Balken, und weder P0/P1-Findings noch unaufgelöste Entscheidungen bleiben offen. Migration, Sicherheit und Rollback sind als nicht betroffen beziehungsweise reversibel belegt.
