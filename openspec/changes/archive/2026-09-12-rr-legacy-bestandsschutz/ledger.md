# Ledger: RR-Legacy-Bestandsschutz

Revision: r1

## Arbeitsort

Worktree wt-rr-legacy-bestandsschutz, Branch feature/rr-legacy-bestandsschutz, abgezweigt von main 450256a0c8b439c18407e61fe7485b04b0db58c9

## Route und Risiko

- Aufwandsklasse: Standard
- Prüfprofil: 0P1A
- Changes: 1 — RR-Legacy-Bestandsschutz
- Prüfbudget: 0P + 1A + 0I gesamt; 0P + 1A + 0I verbleibend
- Risk: high — persistierte biometrische Ergebnisse, Upgrade-/Restore-Verhalten und Plattformparität
- Profil: standard
- Recording: inaktiv; weder Nutzer noch Projektregel haben Messaufzeichnung aktiviert

## Entscheidungsvorgaben

| ID | Frage | Entscheidung | Quelle | Datum | Status |
| --- | --- | --- | --- | --- | --- |
| E1 | Soll der direkte Upgrade-Pfad Bestand erhalten? | Vorhandene HRV-/Recovery-Ergebnisse dürfen beim ersten Re-Score nicht verschwinden. | Nutzer: „ich hab noch nicht auf 11.6.0 geupdated und mit dem fix geht das dann smooth?“ | 2026-09-12 | gültig |
| E2 | Sollen alte RR-Rohdaten konvertiert werden? | Keine heuristische oder pauschale Konvertierung; uneindeutige Rohdaten bleiben vom Scoring ausgeschlossen. | Nutzerklärung nach Erläuterung der gemischten Einheiten | 2026-09-12 | gültig |
| E3 | Welche externe Übergabe ist beauftragt? | Nach Fertigstellung Pull Request erstellen, Realbackup-/Hardwarepfad als ungetestet kennzeichnen und im Issue Tester ansprechen. | Nutzer: „Wenn du fertig ist mach einen MR und sag das er ungetestet ist, pinge aber die leute im issue ob sie es testen wollen.“ | 2026-09-12 | gültig |

## Annahmen und Spikes

| Annahme | Risiko wenn falsch | Spike/Proof | Resultat | Planfolge |
| --- | --- | --- | --- | --- |
| 11.6 überschreibt vorhandene Werte statt nur RR auszublenden | Fix am falschen Ort | Read-only Pfadprüfung beider Plattformen | Vollzeilen-Write ersetzt HRV/Recovery durch NULL | Bestandsschutz vor der allgemeinen Persistenz |
| Altzeilen sind nicht sicher konvertierbar | Falsche biometrische Ergebnisse | Historie, Read-Policy und Datenmodell geprüft | Herkunft fehlt; korrekte Millisekunden und rohe Ticks können gemischt sein | Keine Rohdatenmigration |
| Ein exakter Status kann normale No-RR-Nächte unterscheiden | Alte Scores könnten unzulässig festgehalten werden | Vorhandene Zeit-, Owner-, Quarantäne- und Transportprädikate geprüft | Store besitzt alle erforderlichen Signale | Gemeinsame gekapselte Statusabfrage |

## Akzeptanzmatrix

| Scenario reference | Proof | Package | Status | Evidence |
| --- | --- | --- | --- | --- |
| rr-legacy-score-preservation / Legacy-Ergebnisse überleben Upgrade und Restore / Vorhandener Snapshot bleibt erhalten | Engine-Regressionstest je Plattform, restore-nahe SQLite-Fixture | 1 | proven | Android-Integrationstest grün; spiegelbildlicher Swift-Test liegt vor, Ausführung bleibt CI/macOS |
| rr-legacy-score-preservation / Legacy-Ergebnisse überleben Upgrade und Restore / Gewöhnlich fehlende RR bleiben leer | Negativtest je Plattform | 1 | proven | Android-Integrationstest grün; Swift-Gegenstück im Quelltest |
| rr-legacy-score-preservation / Legacy-Ergebnisse überleben Upgrade und Restore / Andere Geräte bleiben unverändert | WHOOP-4-/Fremdmarkentest | 1 | proven | Android-Integrationstest und bestehende Store-Tests grün; Swift-Gegenstück vorhanden |
| rr-legacy-score-preservation / Sichere Neuberechnung beendet den Bestandsschutz / Markierter Transport ersetzt den Snapshot | Promotionstest je Plattform | 1 | proven | Android-Promotion und Folge-Re-Score grün; Swift-Gegenstück vorhanden |
| rr-legacy-score-preservation / Sichere Neuberechnung beendet den Bestandsschutz / Unzureichende markierte Daten werden nicht kaschiert | Negativtest je Plattform | 1 | proven | Android-Negativtest grün; Swift-Gegenstück vorhanden |
| rr-legacy-score-preservation / Sichere Neuberechnung beendet den Bestandsschutz / Folge-Re-Score bleibt stabil | Wiederholungstest inklusive Provenienz | 1 | proven | Android-Provenienz-/Stabilitätstest grün; Swift-Gegenstück vorhanden |

## Run Ledger

| Phase | Rolle | Ergebnis | Budgetwirkung |
| --- | --- | --- | --- |
| Planung | Orchestrator mit zwei unabhängigen read-only Plattformanalysen | Ursache und kleinste sichere Semantik bestimmt | 0P; profilgemäß keine Planungsprüfung |
| 2026-09-12 01:05 CEST | Codex-Orchestrator, Session-Modell | hoch | Ausführungs-Preflight für r1 bestanden; Hostmodus Codex, Paket 1, Basis laut Arbeitsort, Skills-Stand d343278; 1A verbleibt |
| 2026-09-12 01:32 CEST | Implementer, gpt-5.6-sol | high | Paket integriert; Android rot vor Fix, danach gezielte Engine-, Store-, Schema-, Transaktions- und Bytecode-Prüfungen grün |
| 2026-09-12 01:33 CEST | Codex-Orchestrator, Session-Modell | hoch | Integrator-Smoke auf integriertem Stand grün; 43 Android-Tests über fünf betroffene Klassen, Swift lokal nicht verfügbar |
| 2026-09-12 01:41 CEST | Abschlussreview, gpt-5.6-sol | high | 1A verbraucht: 0 P0, 1 P1, 2 P2; Swift-Nebenwirkung auf Wochenableitungen bestätigt |
| 2026-09-12 01:48 CEST | Implementer, gpt-5.6-sol | high | P1 korrigiert; separater Swift-Persistenzpfad, Quellvertrag rot vor und grün nach Fix, Android-Regressionslauf grün |
| 2026-09-12 01:49 CEST | Codex-Orchestrator, Session-Modell | hoch | Fix integriert; Quellvertrag 2/2 grün, gebundener Android-Rerun 23/23 grün; 0A verbleibt |
| 2026-09-12 01:53 CEST | Codex-Orchestrator, Session-Modell | hoch | Draft-Pull-Request veröffentlicht; Realbackup, Hardware und Apple-Ausführung ausdrücklich als ungetestet markiert |
| 2026-09-12 01:54 CEST | Codex-Orchestrator, Session-Modell | hoch | Öffentlicher Testaufruf im bestehenden Issue veröffentlicht; Reporter angesprochen, Datenschutzgrenze genannt |

## Finding Ledger

| ID | Severity | Claim | Evidence/reproduction | Affected scenario/section | Status | Resolution | Verification |
| --- | --- | --- | --- | --- | --- | --- | --- |
| F1 | P1 | Swift ließ geschütztes HRV in Vitality/Body-Age einfließen, Android nicht. | Abschlussreview zeigte mutierte Arbeitsliste vor Wochenableitungen; Quellvertrag reproduzierte den Unterschied rot. | Vorhandener Snapshot bleibt erhalten; Plattformparität | resolved | Geschützte Swift-Tageswerte auf separate Persistenz-/Ausgabekopie begrenzt. | Quellvertrag 2/2 grün; Android 23/23 grün |
| F2 | P2 | Swift-App-Code konnte lokal nicht kompiliert oder ausgeführt werden. | Linux-Host ohne Swift/Xcode. | Gesamte Apple-Lieferung | accepted risk | Pull Request verlangt CI/macOS-Nachweis und nennt Hardware-/Realbackup-Pfad ungetestet. | Offen bis PR-CI/Nutzertest |
| F3 | P2 | Kein kombinierter E2E-Test für Alias nach Re-Pairing an DST-Grenze. | Store-/Scorer-Teiltests decken Signale getrennt ab. | Fenster- und Owner-Auflösung | accepted risk | Kein Scope-Ausbau; bestehende exakte Prädikate und fester Offset bleiben maßgeblich. | Revisit bei CI- oder Nutzertestabweichung |

## Grenzen

- Paket 1 besitzt Implementierung und Tests für beide Plattformen gemäß `tasks.md`.
- Ein Implementer arbeitet im Feature-Worktree; der Orchestrator integriert und prüft seriell.
- Ein unabhängiger Abschlussreview ist nach integrierter Umsetzung verpflichtend.
- Pull Request und Issue-Kommentar erfolgen erst nach lokaler Prüfung; kein Release wird erstellt.

## Cleanup

- Registriert: Paket-Worktree `/root/whoop/wt-rr-legacy-bestandsschutz-crossplatform` und Branch `bau/rr-legacy-bestandsschutz-crossplatform` nach erfolgreicher Integration entfernbar.
- Paket-Worktree nach Integration bereinigt; Feature-Worktree bleibt für Pull Request und Übergabe erhalten.

## Externe Übergabe

- Draft-Pull-Request: https://github.com/ryanbr/noop/pull/2115
- Testaufruf: https://github.com/ryanbr/noop/issues/2101#issuecomment-5641963221
- Kein Release und keine Store-Veröffentlichung durchgeführt.
