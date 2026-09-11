# Umsetzungspakete — Schritte-Detailansichten verdichten

Revision: r3

## 1. Package P1 — Shared oracle and contract

Owned: das kanonische Fixture `android/app/src/test/resources/steps_detail_density_oracle.json`, `Packages/StrandAnalytics/Sources/StrandAnalytics/` für den Swift-Vertrag, `android/app/src/main/java/com/noop/analytics/` für den Kotlin-Zwilling, deren fokussierte Tests, paarige Source-Annotationen sowie die ausschließlich durch den Guard aktualisierten abgeleiteten Dateien `Tools/parity_twin_map.json` und `Tools/parity_ledger_baseline.json`. Forbidden: UI-Dateien, Speicher-/Importpfade, Datenbankschema, allgemeine Chartkomponenten, Lockfiles, manuelle Änderungen an abgeleiteten Paritäts-Snapshots. Dependencies: keine. Outputs: spiegelbildliche pure Schritte-Projektion, ein von beiden Tests gelesenes Oracle und Guard-Zuordnung. Scenarios: `steps-detail-density / Kalenderbasierte Zeitraumauflösung / Tagesauflösung für kurze Zeiträume`, `steps-detail-density / Kalenderbasierte Zeitraumauflösung / Wochenauflösung für drei Monate`, `steps-detail-density / Kalenderbasierte Zeitraumauflösung / Monatsauflösung für lange Zeiträume`, `steps-detail-density / Beobachtungstreuer Mittelwert / Fehlende Tage verändern den Nenner nicht`, `steps-detail-density / Deterministische Grenzfälle / Doppelte Tage und positive Rundung`, `steps-detail-density / Plattformparität / Gemeinsames Oracle auf beiden Plattformen`. Tests: fokussierte Swift-Pakettests, fokussierte Kotlin-JUnit-Tests und Parity-Governance. Resource bounds: ein Implementierer, höchstens 45 Minuten; synthetische Fixtures, kein Netzwerk, kein Gradle-Daemon, Gradle nur unter globalem Lock.

- [ ] 1.1 Vor der Implementierung im kanonischen Fixture gemeinsame Oracle-Fälle für alle acht Bereiche, sparse Daten, beobachtete Nullwerte allein und gemischt mit positiven Werten, kanonische Tages-/Montags-/Monatsersten-Anker, Formatfehler und nicht existente Tage einschließlich Schaltjahr, Duplikate, Ein-Bucket-Serien und `.5`-Rundung ergänzen und den erwarteten Rotgrund festhalten; Swift liest genau den Android-Resource-Pfad über den Repository-Root.
- [ ] 1.2 Paarige pure Projektoren für kalenderbasierte Fenster, Tages-/Wochen-/Monatsbucket und observed-day mean implementieren.
- [ ] 1.3 Beide Plattformtests gegen dieselbe Fixture ausführen, paarige Source-Annotationen ergänzen und dann exakt `python3 Tools/parity_ledger.py --refresh-derived --base origin/main`, `python3 Tools/parity_ledger.py` und `python3 Tools/parity_ratchet.py --base origin/main --offline` ausführen; Map und Baseline niemals direkt bearbeiten.

## 2. Package P2 — Android detail integration

Owned: `android/app/src/main/java/com/noop/ui/HealthVitalDetailLogic.kt`, der Schritte-Zweig in `android/app/src/main/java/com/noop/ui/HealthScreen.kt` und zugehörige UI-/Logiktests. Forbidden: Shared-Oracle/Analytics-Dateien aus P1, Apple-Dateien, Datenspeicher, Imports, generische globale Chartpräferenz. Dependencies: P1. Outputs: Android-Schritte-Detailansicht konsumiert die gemeinsame Projektion und erzwingt Balken. Scenarios: `steps-detail-density / Schritte als Balken / Android erzwingt Balken`, `steps-detail-density / Konsistente Detaildarstellung / Android nutzt eine projizierte Serie`. Tests: fokussierte Android-Logiktests und `testFullDebugUnitTest`. Resource bounds: ein Implementierer, höchstens 35 Minuten; kein Netzwerk, Gradle mit `--no-daemon` unter globalem Lock.

- [ ] 2.1 Einen roten Test ergänzen, der Schritte trotz LINE-Präferenz als Balken, einen einzelnen Rohwert sowie mehrere Rohwerte in nur einem Bucket als einzelnen Balken und andere Vitalwerte unverändert belegt.
- [ ] 2.2 Alle Schritte-Adapter auf finite Werte `>= 0` vereinheitlichen und den Schritte-Zweig auf die P1-Projektion umstellen; Chart, periodengenaue Beschriftung, Summary und Accessibility aus derselben Bucket-Serie speisen, Tageslesungstabelle unverändert lassen.
- [ ] 2.3 Fokussierte Tests für jeden Bereich und die Entfernung positionsbasierter Schritte-Fallbacksemantik ausführen.

## 3. Package P3 — Apple detail integration

Owned: der Schritte-Zweig in `Strand/Screens/MetricExplorerView.swift` und zugehörige `StrandTests/`. Forbidden: Shared-Oracle/Analytics-Dateien aus P1, Android-Dateien, Datenspeicher, Imports, `TrendChart.swift` außer ein zuvor nachgewiesener zwingender Adapterbedarf, globale Chartpräferenz. Dependencies: P1. Outputs: Apple-Schritte-Detailansicht konsumiert die gemeinsame Projektion und erzwingt Balken. Scenarios: `steps-detail-density / Schritte als Balken / Apple erzwingt Balken`, `steps-detail-density / Konsistente Detaildarstellung / Apple nutzt eine projizierte Serie`. Tests: fokussierte Swift-Tests und Plattform-Build auf macOS-Runner. Resource bounds: ein Implementierer, höchstens 35 Minuten; kein Netzwerk; native Builds seriell.

- [ ] 3.1 Rote Tests ergänzen, die `steps` aus WHOOP-, Apple-Health- und Xiaomi-Quellen sowie `steps_est` trotz Linienpräferenz als Balken, Ein-Bucket-Fälle und andere Metriken unverändert belegen.
- [ ] 3.2 Den fachlichen Schritte-Zweig (`steps` oder `steps_est`) auf die P1-Projektion umstellen; die Bereichsauswahl in die Ladeidentität aufnehmen und für ALL `resolvedSeries(..., fullHistory: true)` als Quelle für Werte und Provenance verwenden; Chart, Hero, Latest, periodengenaue Beschriftung, Summary und Accessibility aus derselben Bucket-Serie speisen, Tageslesungstabelle unverändert lassen.
- [ ] 3.3 Den Vorperiodenvergleich für Schritte aus der unmittelbar vorhergehenden gleich langen Kalenderperiode ableiten; fokussierte sparse Tests für alle Bereiche, den produktiven Detailpfad und einen ALL-Wert jenseits des Standardfensters ausführen.

## 4. Package P4 — Serial integration and evidence

Owned: ausschließlich notwendige Konfliktauflösung in den Pfaden aus P1–P3 und Build-/Testaufrufe; keine neuen Produktpfade. Forbidden: neue Features, Schema/Migrationen, Erfassungs-/Quellenlogik, fremde aktive Changes. Dependencies: P1, P2, P3. Outputs: integrierte, nachgewiesene Plattformparität. Scenarios: alle Szenarien dieser Änderung. Tests: gemeinsame Oracle-Tests auf beiden Plattformen, vollständige fokussierte Swift-/Kotlin-Suiten, Android Full-Debug-Build, Apple macOS/iOS-Builds auf verfügbarem Runner und Parity-Governance. Resource bounds: ein serieller Integrator, höchstens 45 Minuten plus ein Infrastruktur-Retry; Gradle global gesperrt und `--no-daemon`, native Apple-Builds seriell, kein Deployment.

- [ ] 4.1 Pakete abhängigkeitsgeordnet integrieren und ausschließlich echte Überlappungskonflikte lösen.
- [ ] 4.2 Android-Suite und Full-Debug-Build unter dem globalen Lock ausführen.
- [ ] 4.3 Auf dem Apple-Runner `xcodegen generate` und danach `xcodebuild -scheme Strand -configuration Debug -destination 'platform=macOS' -only-testing:StrandTests/StepsDetailDensityTests CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO test` ausführen; anschließend Swift-Pakettests sowie macOS-/iOS-Buildnachweise ausführen und den App-Testbeleg beiden Apple-Acceptance-Zeilen zuordnen.
- [ ] 4.4 Oracle-Ausgaben beider Plattformen vergleichen, Parity-Governance ausführen und die Acceptance-Matrix mit realen Belegen aktualisieren.
- [ ] 4.5 No-schema-, Security-, Ressourcen- und Rollback-Inspektion dokumentieren; keine nicht ausgeführten Nachweise als proven markieren.
