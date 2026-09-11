# RR-Legacy-Bestandsschutz

Revision: r1

## Warum

NOOP 11.6.0 kennzeichnet neue WHOOP-5-RR-Daten nach Transport und wertet alte, unmarkierte RR-Zeilen
bewusst nicht mehr aus, weil der Altbestand Millisekunden und 1/1024-Sekunden-Ticks ununterscheidbar
mischen kann. Der einmalige neue Analyse-Fingerprint stößt nach Upgrade oder Restore einen Re-Score an.
Dabei werden vorhandene, unter 11.5 berechnete HRV- und Recovery-Werte durch leere Werte ersetzt.

Nutzer, die direkt von 11.5 auf eine korrigierte Folgeversion aktualisieren, sollen ihre vorhandenen
historischen Ergebnisse behalten, bis eindeutig markierte RR-Daten eine sichere Neuberechnung erlauben.

## Was sich ändert

- Apple und Android erkennen konservativ eine Nacht, deren RR-Daten ausschließlich durch die neue
  WHOOP-5-Legacy-Regel zurückgehalten werden.
- Für genau diese Nacht werden vorhandene berechnete HRV- und Recovery-Zellen als unveränderter
  Legacy-Snapshot erhalten; alle anderen Tagesfelder werden weiterhin frisch berechnet.
- Sobald ein sicher auswertbarer markierter RR-Transport vorliegt, endet der Bestandsschutz und die
  normale Neuberechnung gewinnt.
- Upgrade- und restore-nahe Regressionstests sichern die Semantik plattformgleich ab.
- Nach erfolgreicher lokaler Prüfung wird ein Pull Request erstellt und das bestehende öffentliche
  Issue um einen Testaufruf ergänzt; der fehlende Test mit echter Nutzer-Sicherung und Hardware wird
  dort ausdrücklich benannt.

## Entscheidungen

- E1: Ein direkter Upgrade-Pfad von 11.5 soll vorhandene HRV-/Recovery-Ergebnisse verlustfrei erhalten.
  Quelle: Nutzer, 2026-09-12.
- E2: Alte RR-Rohzeilen werden weder heuristisch konvertiert noch pauschal wieder zum Scoring zugelassen.
  Quelle: Nutzerklärung und bestätigte Datenambiguität, 2026-09-12.
- E3: Nach Abschluss werden Pull Request und öffentlicher Testaufruf erstellt; der Realbackup-/Hardwarepfad
  wird als ungetestet ausgewiesen. Quelle: Nutzer, 2026-09-12.

Der Bestandsschutz bewahrt HRV und Recovery exakt als zusammengehörigen historischen Snapshot. Recovery
wird nicht aus altem HRV und heutigen Baselines neu erfunden. Die allgemeine Upsert-Semantik bleibt
unverändert; die Entscheidung liegt im eng begrenzten WHOOP-5-Scoringpfad.

## Nicht-Ziele

- Keine Rekonstruktion bereits durch 11.6.0 verlorener Werte ohne ältere Sicherung oder erneuten Offload.
- Keine automatische Einheitenklassifikation oder Konvertierung unmarkierter RR-Rohdaten.
- Keine Änderung an WHOOP 4, anderen Marken oder gewöhnlichen Nächten ohne RR-Daten.
- Kein App-Release und keine Veröffentlichung in einem Store.

## Auswirkungen

Betroffen sind die RR-Statusabfrage, die Score-Persistenz und deren Tests auf Apple und Android. Die
Änderung berührt persistierte Nutzerdaten und Plattformparität, verändert aber weder das Datenbankschema
noch das Backupformat. Rollback ist durch Rücknahme des Changes möglich; erhaltene Legacy-Snapshots
bleiben dabei normale vorhandene Tageswerte.
