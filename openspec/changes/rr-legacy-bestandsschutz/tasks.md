# Aufgaben: RR-Legacy-Bestandsschutz

Revision: r1

## 1. Plattformgleicher Scoring-Bestandsschutz

Eigentum: Apple- und Android-RR-Read-Policy, Intelligence-Engine-Persistenzpfad und gezielte Tests.
Verboten: allgemeine Backupformat- oder Datenbankschemaänderungen, pauschale RR-Konvertierung,
Release-/Store-Dateien und fachfremde Änderungen.

Eingaben: bestehende strikte WHOOP-5-Policy, berechnete Tageswerte im Re-Score-Fenster und E1–E3.
Ausgabe: identische konservative Bestandsschutzentscheidung auf beiden Plattformen.
Abhängigkeiten: keine. Ressourcen: lokale Tests; kein Hardwarezugriff und keine Echtdaten erforderlich.

- [x] 1.1 Zuerst rote Regressionstests für direkten Upgrade-/Restore-Bestand, gewöhnlich fehlende RR,
  vorhandene markierte aber unzureichende RR, WHOOP 4 und Transport-Promotion ergänzen.
- [x] 1.2 Eine eng gekapselte Store-Abfrage für den exakten Legacy-zurückgehalten-Status implementieren,
  deren Prädikate dem Scoring-Read entsprechen.
- [x] 1.3 Vorhandene berechnete Tageswerte fensterbegrenzt laden und ausschließlich HRV/Recovery gemeinsam
  erhalten, wenn der Status dies erlaubt; Provenienz erhalten.
- [x] 1.4 Swift/Kotlin-Parität und stabile Folge-Re-Scores absichern.
- [x] 1.5 Betroffene Store-, Engine- und Paritätstests ausführen; vollständige App-Builds nur seriell.

Szenarien: `rr-legacy-score-preservation / Legacy-Ergebnisse überleben Upgrade und Restore / Vorhandener
Snapshot bleibt erhalten`, `rr-legacy-score-preservation / Legacy-Ergebnisse überleben Upgrade und Restore /
Gewöhnlich fehlende RR bleiben leer`, `rr-legacy-score-preservation / Sichere Neuberechnung beendet den
Bestandsschutz / Markierter Transport ersetzt den Snapshot`, `rr-legacy-score-preservation / Sichere
Neuberechnung beendet den Bestandsschutz / Unzureichende markierte Daten werden nicht kaschiert`.
