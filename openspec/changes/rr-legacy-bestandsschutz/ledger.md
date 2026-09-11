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
| rr-legacy-score-preservation / Legacy-Ergebnisse überleben Upgrade und Restore / Vorhandener Snapshot bleibt erhalten | Engine-Regressionstest je Plattform, restore-nahe SQLite-Fixture | 1 | unproven | — |
| rr-legacy-score-preservation / Legacy-Ergebnisse überleben Upgrade und Restore / Gewöhnlich fehlende RR bleiben leer | Negativtest je Plattform | 1 | unproven | — |
| rr-legacy-score-preservation / Legacy-Ergebnisse überleben Upgrade und Restore / Andere Geräte bleiben unverändert | WHOOP-4-/Fremdmarkentest | 1 | unproven | — |
| rr-legacy-score-preservation / Sichere Neuberechnung beendet den Bestandsschutz / Markierter Transport ersetzt den Snapshot | Promotionstest je Plattform | 1 | unproven | — |
| rr-legacy-score-preservation / Sichere Neuberechnung beendet den Bestandsschutz / Unzureichende markierte Daten werden nicht kaschiert | Negativtest je Plattform | 1 | unproven | — |
| rr-legacy-score-preservation / Sichere Neuberechnung beendet den Bestandsschutz / Folge-Re-Score bleibt stabil | Wiederholungstest inklusive Provenienz | 1 | unproven | — |

## Run Ledger

| Phase | Rolle | Ergebnis | Budgetwirkung |
| --- | --- | --- | --- |
| Planung | Orchestrator mit zwei unabhängigen read-only Plattformanalysen | Ursache und kleinste sichere Semantik bestimmt | 0P; profilgemäß keine Planungsprüfung |

## Grenzen

- Paket 1 besitzt Implementierung und Tests für beide Plattformen gemäß `tasks.md`.
- Ein Implementer arbeitet im Feature-Worktree; der Orchestrator integriert und prüft seriell.
- Ein unabhängiger Abschlussreview ist nach integrierter Umsetzung verpflichtend.
- Pull Request und Issue-Kommentar erfolgen erst nach lokaler Prüfung; kein Release wird erstellt.

## Cleanup

- Noch offen: temporäre Testartefakte prüfen, Worktree nach Übergabe erhalten.
