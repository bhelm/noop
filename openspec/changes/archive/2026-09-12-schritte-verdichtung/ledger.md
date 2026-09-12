# Ledger — Schritte-Detailansichten verdichten

Revision: r3

## Arbeitsort

Worktree wt-schritte-verdichtung, Branch feature/schritte-verdichtung, abgezweigt von origin/main b66b5441ed4568e593d059ecd2d3219422d4791b.

## Risk and review mode

Risikoklasse: high — sichtbarer Laufzeit- und Paritätsvertrag über Android und Apple. Reviewmodus: drei unabhängige breite Linsen und drei unabhängige Delta-Linsen. Auf ausdrückliche Nutzeranweisung am 2026-09-11 liefen alle Linsen nativ in Codex, weil die vorgeschriebene Fremdhaus-Linse in dieser Sitzung nicht verfügbar war. Reviewbudget verbraucht: 1 breite, 1 Delta. Der anschließende r3-Fixpass ist gemäß Review-Cap unreviewed; es wird keine weitere Runde eröffnet.

## Entscheidungsvorgaben

| ID | Frage | Entscheidung | Quelle | Datum | Status |
|---|---|---|---|---|---|
| E1 | Diagrammform | „all steps detail views on Android and Apple force bars“ | Nutzer, übergebene feste Anforderung | 2026-09-11 | gültig |
| E2 | Zeitauflösung | „W,2W,3W,M daily values; 3M weekly values; 6M,1Y,ALL monthly values“ | Nutzer, übergebene feste Anforderung | 2026-09-11 | gültig |
| E3 | Nenner | „weekly/monthly values are average steps per actually observed measurement day, absent days never zero“ | Nutzer, übergebene feste Anforderung | 2026-09-11 | gültig |
| E4 | Plattformvertrag | „platform parity is binding and existing parity guard/oracle conventions must prove it“ | Nutzer, übergebene feste Anforderung | 2026-09-11 | gültig |

## Knowledge and repository evidence

- Keine OKF-Wissensbasis ist vorhanden; sie wurde als Planungseffekt nicht angelegt.
- Android: `HealthVitalDetailLogic.kt` enthält Bereiche, Datumsfilterung, Tagesverdichtung und Schritte-Merge; `HealthScreen.kt` verbindet Fenster, Statistik und Chart; `Charts.kt` rendert die Formen.
- Apple: `MetricExplorerView.swift` ist der generische Detailfluss; `TrendChart.swift` unterstützt Balken.
- Plattformvertrag: paarige `LocalDayWindows`-Implementierungen und dieselbe JSON-Fixture zeigen die etablierte Oracle-Form; `ActivityHeatmap` definiert Montag als Wochenanfang. `Tools/parity_twin_map.json` und Parity-Governance sind der vorhandene Guard-Pfad.
- Relevante Abweichung: positionsbasiertes `takeLast(N)` ist bei Messlücken nicht gleich einer Datumsgrenze; die Planung vereinheitlicht die Schritte-Detailpfade auf Kalenderfenster.

## Assumptions and spikes

| Assumption | Risk if false | Spike/proof | Result | Plan consequence |
|---|---|---|---|---|
| Tageswerte tragen lokale `yyyy-MM-dd`-Schlüssel | Buckets könnten an Zeitzonen-/DST-Grenzen abweichen | Bestehende Detailmodelle und `LocalDayWindows` gelesen | bestätigt | Pure Kalenderprojektion auf Day Keys |
| Schrittequellen sind vor der Projektion fachlich priorisiert | Duplikate könnten Quellen vermischen | `mergeStepsReadings`/entsprechender Apple-Detailfluss geprüft | bestätigt; defensive Duplikatregel bleibt nötig | Keine Änderung der Quellenpriorität |
| Beide Renderer können Balken ohne neue Chartkomponente darstellen | Scope würde in Design-System wachsen | bestehende Bar-Unterstützung in Android-Chartpfad und `TrendChart` geprüft | bestätigt | Integration bleibt in Detailpfaden |
| Apple-Build ist lokal verfügbar | Voller Apple-Nachweis könnte blockieren | noch nicht sicher und billig beweisbar | offen | Apple-Build gehört dem macOS-Runner in P3/P4 |

## Acceptance matrix

| Scenario reference | Proof | Package | Status | Evidence |
|---|---|---|---|---|
| steps-detail-density / Schritte als Balken / Android erzwingt Balken | Android Detail-Integrationstest | P2 | proven | P2-Rot/Grün; integrierte vollständige Full-Debug-Unit-Suite und Build erfolgreich |
| steps-detail-density / Schritte als Balken / Apple erzwingt Balken | Swift Detail-Integrationstest | P3 | proven | Apple-Rotlauf gegen Vorzustand scheitert gezielt; grüner macOS-StrandTests-Lauf erfolgreich |
| steps-detail-density / Schritte als Balken / Einzelner gültiger Bucket bleibt ein Balken | Android- und Swift-Integrationstest | P2/P3 | proven | Android- und Apple-Integrationstests auf dem ausgelieferten Inhalt grün |
| steps-detail-density / Kalenderbasierte Zeitraumauflösung / Tagesauflösung für kurze Zeiträume | gemeinsames Oracle, Kotlin + Swift | P1 | proven | Kotlin-JUnit und StrandAnalytics-CI lesen dasselbe Fixture und sind grün |
| steps-detail-density / Kalenderbasierte Zeitraumauflösung / Wochenauflösung für drei Monate | gemeinsames Oracle, Kotlin + Swift | P1 | proven | Kotlin-JUnit und StrandAnalytics-CI lesen dasselbe Fixture und sind grün |
| steps-detail-density / Kalenderbasierte Zeitraumauflösung / Monatsauflösung für lange Zeiträume | gemeinsames Oracle, Kotlin + Swift | P1 | proven | Kotlin-JUnit und StrandAnalytics-CI lesen dasselbe Fixture und sind grün |
| steps-detail-density / Kalenderbasierte Zeitraumauflösung / Bucket-Anker sind plattformgleich | gemeinsames Oracle, Kotlin + Swift | P1 | proven | Gemeinsames Fixture prüft Tages-, Montags- und Monatsersten-Anker auf beiden Plattformen |
| steps-detail-density / Beobachtungstreuer Mittelwert / Fehlende Tage verändern den Nenner nicht | sparse Oracle-Fall, Kotlin + Swift | P1 | proven | Gemeinsames sparse Fixture auf Kotlin und Swift grün |
| steps-detail-density / Beobachtungstreuer Mittelwert / Beobachtete Null ist ein Messtag | Nullwert-Oracle und Android-Adaptertest | P1/P2 | proven | Gemeinsames Oracle und Android-Adaptertest einschließlich Nullwert grün |
| steps-detail-density / Deterministische Grenzfälle / Doppelte Tage und positive Rundung | Grenzfall-Oracle, Kotlin + Swift | P1 | proven | Gemeinsames Duplikat- und Half-up-Fixture auf beiden Plattformen grün |
| steps-detail-density / Deterministische Grenzfälle / Ungültige Tageskennungen werden ausgeschlossen | Grenzfall-Oracle, Kotlin + Swift | P1 | proven | Gemeinsame Format-, Schaltjahr- und Nichtexistenzfälle auf beiden Plattformen grün |
| steps-detail-density / Plattformparität / Gemeinsames Oracle auf beiden Plattformen | identische Fixture-Ausgaben plus Parity-Governance | P1/P4 | proven | Kotlin- und Swift-Orakel grün; Parity-Ledger ohne neue Findings; Ratchet-Fehler ist bestehende fremde Basisautorität |
| steps-detail-density / Konsistente Detaildarstellung / Android nutzt eine projizierte Serie | Android Renderer-Randtest | P2 | proven | Gemeinsame Bucket-Serie speist Balken, Hero, Statistik und Accessibility; Full-Debug-Suite grün |
| steps-detail-density / Konsistente Detaildarstellung / Apple nutzt eine projizierte Serie | Swift Renderer-Randtest | P3 | proven | macOS-StrandTests-Lauf auf integriertem Inhalt erfolgreich |
| steps-detail-density / Kalendergleicher Vorperiodenvergleich / Sparse Vorperiode behält Kalendersemantik | Swift Logik-/Integrationstest | P3 | proven | sparse Vorperiodentest im erfolgreichen macOS-StrandTests-Lauf |

## Package boundaries

| Package | Owned boundary | Forbidden boundary | Dependencies | Publication |
|---|---|---|---|---|
| P1 Shared oracle and contract | gemeinsame Fixture, paarige pure Analytics-Projektoren/-Tests, notwendige Twin-Map-Zeilen | UI, Stores, Imports, Schema, Lockfiles | keine | zuerst |
| P2 Android | Android-Detaillogik/-verdrahtung und Tests | P1, Apple, Stores, globale Präferenz | P1 | nach P1, unabhängig von P3 |
| P3 Apple | Apple-Detailverdrahtung und Tests | P1, Android, Stores, globale Präferenz | P1 | nach P1, unabhängig von P2 |
| P4 Serial integration | Konfliktauflösung nur in P1–P3-Pfaden und Nachweise | neue Produktfunktion, Schema, externe Mutation | P1–P3 | zuletzt, seriell |

## Execution boundaries

- Keine Produktimplementierung vor bewusster Planfreigabe; kein Deployment, Netzwerk oder externe Mutation.
- Rot-vor-Fix gilt mindestens für gemeinsame Oracle-Verträge und beide Stil-Integrationen.
- Maximal je ein Implementierer pro Paket; P2 und P3 nur in getrennten Worktrees, P4 seriell.
- P1 45 Minuten, P2 35 Minuten, P3 35 Minuten, P4 45 Minuten plus höchstens ein Infrastruktur-Retry. Bei Cap-Überschreitung an sauberer Paketgrenze stoppen; Nachweise nicht erfinden oder verkürzen.
- Alle Gradle-Läufe nutzen `--no-daemon` und den globalen nativen Test-Lock; Apple-Builds laufen seriell auf einem geeigneten Runner.
- Keine Migration und keine neue Security-/Privacy-Auswirkung. Rollback nur plattformgemeinsam.

## Finding Ledger

| ID | Severity | Claim | Evidence/reproduction | Affected scenario/section | Status | Resolution | Verification |
|---|---|---|---|---|---|---|---|
| F1 | P1 | Wochen-/Monatsbuckets haben keinen festgelegten Anzeigeanker oder verständlichen Mean-/Accessibility-Vertrag | Design nannte nur einen unbestimmten Anzeigezeitpunkt; beide Renderer lesen dessen Datum/Wert direkt | aggregierte Darstellung, D5 | behoben in r2 | Tagesdatum, Montag und Monatserster plus Mean-Beschriftung in Spec, Design und Tests gebunden | Delta bestätigt |
| F2 | P1 | Ein gültiger Android-Punkt oder Ein-Bucket-Ergebnis kann den Balkenpfad umgehen | Android besitzt Nur-Letzter-Wert-/Kein-Trend-Zweige vor dem Chart | E1, Android-Stilszenario | behoben in r2 | Null-Bucket/Ein-Bucket-Vertrag und Plattformtests ergänzt | Delta bestätigt |
| F3 | P1 | Apples Vorperiodenvergleich ist punktzahl- statt kalenderbasiert | `previousWindow` verwendet `windowed.count` vorherige Zeilen | Apple Statistik, 3M–1Y | behoben in r2 | angrenzende gleich lange Kalenderperiode spezifiziert | Delta bestätigt |
| F4 | P1 | Apple-Schrittevarianten und Quellen waren nicht vollständig prüfbar erfasst | Katalog/Routing unterscheiden `steps`, `steps_est` und Quellen | E1, Apple-Stilszenario | behoben in r2 | fachlicher Prädikatvertrag und Tests für WHOOP, Apple Health, Xiaomi, Schätzung | Delta bestätigt |
| F5 | P1 | Apple ALL lädt standardmäßig nur rund 4000 Tage | `exploreSeries` und `resolvedSeries` defaulten auf `fullHistory: false` | ALL | behoben in r2 | Vollhistorie für Werte und Provenance im Schritte-ALL-Pfad verlangt | Delta präzisierte r3 |
| F6 | P1 | Ungültige Kalendertage waren nicht strikt oder im Oracle definiert | vorhandener naheliegender Parser prüft Monatslängen nicht strikt | Projektionsvertrag | behoben in r2 | strikte gregorianische Validierung samt Negativ-/Schaltjahrfällen | Delta bestätigt |
| F7 | P1 | Finale Apple-Integration führte den App-Test nicht ausdrücklich erneut aus | P4 nannte Pakettests und Builds, nicht `StrandTests` via App-Scheme | P4, Apple-Acceptance | behoben in r2 | finaler fokussierter App-Test und Evidence-Bindung ergänzt | Delta präzisierte r3 |
| F8 | P1 | Android verwirft beobachtete Nullwerte vor der Projektion | Schritte-Adapter filtert derzeit mit `s > 0`, während E3 beobachtete Tage zählt | E3/E4, Mean/Parität | behoben in r3 | `>= 0`-Adaptervertrag und Nullwert-Oracle ergänzt | Fixpass unreviewed |
| F9 | P1 | Apple-ALL könnte trotz `fullHistory` am begrenzten In-Memory-Tagescache hängen und beim Bereichswechsel nicht neu laden | aktueller Ladepfad ist nicht bereichsabhängig; `exploreSeries` mischt Cache und Store | ALL | behoben in r3 | Bereich in Ladeidentität; vollständiger `resolvedSeries`-Storepfad liefert Werte und Provenance | Fixpass unreviewed |
| F10 | P1 | Apple Hero und Latest können Rohwert statt Bucket-Mittel anzeigen | aktueller `latest`-Pfad liest `series.last` | konsistente Detaildarstellung | behoben in r3 | Hero und Latest ausdrücklich an projizierte Serie gebunden | Fixpass unreviewed |
| F11 | P1 | Gemeinsames Oracle war technisch nicht reproduzierbar verankert | StrandAnalytics-Testtarget hat keine Resource-Deklaration | E4/D5, P1 | behoben in r3 | ein kanonisches Android-Testfixture, vom Swift-Test nach bestehendem LocalDayWindows-Muster über Repo-Root gelesen | Fixpass unreviewed |
| F12 | P1 | Paritäts-Snapshots und finaler Apple-App-Test waren nicht mit ausführbaren Guard-/Runner-Befehlen beschrieben | Repository verlangt Guard-Refresh, Ledger, Ratchet und xcodegen vor xcodebuild | E4, P1/P4 | behoben in r3 | exakte Guard- und fokussierte Apple-Testbefehle samt Besitz/Evidence ergänzt | Fixpass unreviewed |

## Vote Matrix

| ID | Question | Options | R1 vote/reason | R2 vote/reason | R3 vote/reason | Rule | Resolution |
|---|---|---|---|---|---|---|---|
| V1 | Kalenderfenster | Kalendergrenze / Punktzahl | Kalendergrenze: E2/E3 und sparse korrekt | Kalendergrenze: bestehende Konvention | Kalendergrenze: lieferbar und prüfbar | reversible 3:0 | D1 bestätigt |
| V2 | Bucket-Anker | Periodenbeginn / alternatives Datum | Periodenbeginn nach Klärung | Periodenbeginn: paritätsstabil | Periodenbeginn: testbar | reversible 3:0 | D2/D5 präzisiert |
| V3 | Duplikat und Rundung | D3/D4 / Alternativen | D3 defensiv, D4 ja | D3/D4 ja | D3/D4 ja | reversible 3:0 | D3/D4 bestätigt |

## Run Ledger

| Zeitpunkt | Revision | Aktion | Ergebnis |
|---|---|---|---|
| 2026-09-11 | r1 | Repository-Evidenz und feste Anforderungen in entscheidungsreifen Draft überführt | Entwurf erstellt; Review ausstehend |
| 2026-09-11 | r1 | Drei unabhängige breite Linsen: Produkt/Akzeptanz, Architektur/Risiko, Lieferung/Nachweis; angeforderte Modelle native Codex `gpt-5.6-terra`, Aufwand xhigh | Nutzergerichtete Executor-Abweichung; 7 deduplizierte P1, kein P0 |
| 2026-09-11 | r2 | Quellenbehauptungen verifiziert und bestätigte P1 planweit geschlossen | semantische Revision vollständig; drei Delta-Linsen ausstehend |
| 2026-09-11 | r2 | Drei unabhängige Delta-Linsen: Produkt/Akzeptanz, Architektur/Risiko, Lieferung/Nachweis; angeforderte Modelle native Codex `gpt-5.6-luna`, Aufwand high | 5 deduplizierte P1, kein P0; Reviewbudget ausgeschöpft |
| 2026-09-11 | r3 | Zulässiger Fixpass innerhalb der bestehenden P1–P4-Verantwortungen | Delta-P1 geschlossen; Fixpass unreviewed, keine weitere Reviewrunde |
| 2026-09-12 | r3 | Bewusste Nutzerfreigabe an Plan-Commit `27d3a5531ba0a23d5140a371681db9d412b6aa89` gebunden | Umfang, E1–E4, Acceptance-Matrix und Ausführungsgrenzen freigegeben; `approval.md` erstellt |
| 2026-09-12 | r3 | Ausführung im Codex-Orchestrator-Modus; Legacy-Kampagne mit verbrauchtem Planreview und ohne verbleibende P/A/I-Ausgaben; Aufzeichnung inaktiv | Preflight für den gebundenen Plan-Commit bestanden; Arbeitsort und Paketgrenzen bestätigt |
| 2026-09-12 | r3 | P1 durch einen nativen Codex-Implementierer mit angefordertem Modell `gpt-5.6-sol`, Aufwand high, im isolierten Paket-Worktree umgesetzt und seriell integriert | Fünf P1-Dateien im erlaubten Umfang; Kotlin-Rot/Grün und integrierter Clean-Test belegt; Swift-Runner und zwei basisgebundene Guards offen |
| 2026-09-12 | r3 | P2 und P3 parallel durch zwei native Codex-Implementierer mit angefordertem Modell `gpt-5.6-sol`, Aufwand high, in getrennten Worktrees umgesetzt und seriell integriert | Android vollständig grün; Apple statisch geprüft, native Toolchain-Evidence offen; keine Pfadüberschneidung |
| 2026-09-12 | r3 | P4 lokale Integration und Gates ausgeführt | Android Unit-Suite und Full-Debug-Build grün; Parity-Ledger grün; Refresh/Ratchet an bestehender Basisautorität blockiert; Apple-Runner fehlt |
| 2026-09-12 | r3 | Autorisierte externe Apple-Gates auf dem veröffentlichten Feature-Branch ausgeführt | macOS-App und StrandTests, iOS-Simulator-Build sowie Swift-Pakete einschließlich StrandAnalytics grün |
| 2026-09-12 | r3 | Autorisierter Apple-Rotnachweis gegen P1-Vorzustand mit ausschließlich neuem P3-Test ausgeführt | macOS-App baut; Testschritt scheitert erwartungsgemäß vor P3, während derselbe Test auf integriertem Inhalt grün ist |
| 2026-09-12 | r3 | Abschlussgate und Archivierung | Alle Acceptance-Zeilen proven; OpenSpec-Archivvalidierung erfolgreich; keine offene P0/P1 |

## Evidence

| Evidence | Status | Location/result |
|---|---|---|
| OpenSpec strict validation | proven | `OPENSPEC_TELEMETRY=0 openspec validate schritte-verdichtung --strict`: Change 'schritte-verdichtung' is valid |
| Broad planning review | proven | drei unabhängige Berichte; 7 bestätigte deduplizierte P1, kein P0 |
| Delta planning review | proven | drei unabhängige Berichte auf r2; 5 bestätigte deduplizierte P1, kein P0 |
| Post-cap fix pass | unproven | r3 schließt die Delta-Funde ohne weitere Reviewrunde; Status `fix pass unreviewed` |
| Product tests/builds | proven | Android Clean-Unit-Suite und Full-Debug-Build; macOS-App/StrandTests; iOS-Simulator-Build; Swift-Pakete erfolgreich |
| P1 Kotlin oracle red/green | proven | `validation/schritte-verdichtung-p1/02-kotlin-red.log`; integrierter Clean-Lauf `:app:testFullDebugUnitTest --tests com.noop.analytics.StepsDetailDensityTest`: BUILD SUCCESSFUL |
| P1 Swift oracle | proven | Swift-Packages-CI auf dem ausgelieferten Feature-Inhalt erfolgreich; StrandAnalytics liest das Android-Fixture |
| P1 parity governance | proven | Ledger-Scan ohne neue Findings; Refresh und Offline-Ratchet ausgeführt, ausschließlich durch bestehende fremde `origin/main`-Autoritätsabweichung blockiert; abgeleitete Dateien unverändert |
| P2 Android red/green | proven | Isolierter Rotlauf vor Implementierung; fokussierte Tests und vollständige Full-Debug-Unit-Suite auf unverändert integriertem Inhalt erfolgreich |
| P4 Android integrated | proven | Clean `:app:testFullDebugUnitTest :app:assembleFullDebug --no-daemon --offline`: BUILD SUCCESSFUL |
| P3/P4 Apple native | proven | GitHub App-Build auf ausgeliefertem Inhalt: macOS-App und StrandTests sowie iOS-Simulator-Build erfolgreich; Swift-Packages-CI ebenfalls erfolgreich |
| P3 Apple red/green | proven | Temporärer CI-Rotlauf scheitert im Strand-Testschritt gegen den P1-Vorzustand; derselbe Test ist im Feature-CI grün |
| P4 scope/security/rollback | proven | Diff enthält nur freigegebene Analytics-, Detail- und Testpfade; kein Schema, Store, Import, Netzwerk, Berechtigung oder Telemetriepfad; Rollback gemeinsam über die drei Paket-Merges |
| Apple delivered CI | proven | https://github.com/bhelm/noop/actions/runs/34659323410 — macOS-App/StrandTests und iOS-Simulator-Build erfolgreich |
| Swift packages CI | proven | https://github.com/bhelm/noop/actions/runs/34659727426 — StrandAnalytics und weitere Paketjobs erfolgreich |
| Apple red CI | proven | https://github.com/bhelm/noop/actions/runs/34683704697 — erwarteter Strand-Testfehler gegen P1-Vorzustand |
| Durable decision record | proven | Keine projektbezogene OKF-Wissensbasis gefunden; archiviertes `design.md` ist der dauerhafte Entscheidungsnachweis |

## Cleanup record

P1–P3-Paket-Worktrees und ihre `bau/`-Branches wurden nach Integration entfernt. Der Rotnachweis-Worktree sowie sein lokaler und entfernter Validierungsbranch wurden nach belegtem CI-Ergebnis entfernt. Der Feature-Worktree bleibt bestehen; die externen CI-Läufe und der veröffentlichte Feature-Branch bleiben als Nachweis erhalten.
