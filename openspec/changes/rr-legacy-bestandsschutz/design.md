# Design: RR-Legacy-Bestandsschutz

Revision: r1

## Kontext

Der neue WHOOP-5-Read-Filter ist fachlich korrekt: Unmarkierte Altzeilen können zwei Einheiten enthalten
und dürfen nicht als ein homogener Beat-Train interpretiert werden. Der Defekt entsteht danach, wenn ein
vollständiger Tagesdatensatz mit leerem HRV und leerer Recovery einen vorhandenen berechneten Tageswert
überschreibt. Ein allgemeines `NULL bedeutet alten Wert behalten` wäre falsch, weil legitime Löschungen,
Schlafkorrekturen und Rekalibrierungen dann nicht mehr wirksam würden.

## Architektur und Datenfluss

Vor der Neuberechnung lädt jede Plattform die vorhandenen berechneten Tageswerte ausschließlich für das
aktuelle Re-Score-Fenster. Der Store stellt für Owner und exaktes RR-Lesefenster einen kleinen Status bereit,
der dieselben Geräte-, Zeit-, Quarantäne- und Transportprädikate wie der Scoring-Read verwendet.

Ein frisch berechneter Tag darf vorhandenes HRV und vorhandene Recovery nur übernehmen, wenn:

1. die tatsächliche Quelle unter der strikten WHOOP-5-Regel steht,
2. im exakten Fenster unmarkierte, nicht verdächtige RR-Zeilen existieren,
3. im Fenster kein sicher auswertbarer Transport existiert,
4. der frische Tag Schlaf, aber kein HRV-Ergebnis besitzt, und
5. ein vorhandener berechneter Tag mindestens HRV enthält.

Die beiden geschützten Zellen werden gemeinsam übernommen. Alle übrigen Felder stammen aus der aktuellen
Analyse. Die bestehende Score-Provenienz des Snapshots darf in diesem Fall nicht gelöscht oder als neu
berechnet ausgegeben werden. Markierte, aber zu dünne oder ungültige RR-Daten lösen keinen Bestandsschutz
aus; deren leeres Ergebnis ist eine aktuelle fachliche Aussage.

## Kompatibilität und Migration

Es gibt keine Schemaänderung. Der Schutz greift beim ersten Re-Score direkt auf die bereits vorhandenen
berechneten Zeilen aus 11.5 oder einer intakten Sicherung zu. Nutzer, deren Werte durch 11.6.0 bereits auf
leer gesetzt wurden, benötigen eine ältere Sicherung oder einen erneuten Strap-Offload.

## Sicherheit und Datenschutz

Keine neuen Daten verlassen das Gerät. Die Lösung vermeidet eine spekulative Interpretation biometrischer
Rohdaten und verändert keine Exportinhalte. Der öffentliche Testaufruf darf keine persönlichen Daten oder
Rohdaten anfordern; bestehende redigierte Diagnosewege bleiben maßgeblich.

## Rollout und Rollback

Der Change wird mit deterministischen Store- und Engine-Tests beider Plattformen geprüft. Ein Pull Request
kennzeichnet ausdrücklich, dass der Ablauf mit einer echten 11.5-Nutzersicherung und realer Hardware noch
nicht ausgeführt wurde. Rücknahme des Changes stellt das 11.6.0-Verhalten wieder her, ohne Datenmigration
rückgängig machen zu müssen.

## Entscheidungen und Abwägungen

- Gemäß E1 werden vorhandene Ergebnisse geschützt, bevor der erzwungene Re-Score sie löschen kann.
- Gemäß E2 bleibt der RR-Filter unverändert; weder Datum noch Wertebereich gelten als Einheitenbeweis.
- HRV und Recovery bleiben exakt zusammen, statt Recovery mit veränderten Baselines neu zu berechnen.
- Ein exakter Fensterstatus wird einer groben ersten/letzten Tagesgrenze vorgezogen, damit gewöhnlich
  fehlende oder später unzureichende RR-Daten keine alten Scores festhalten.
- Die allgemeine Persistenzschicht wird nicht verändert; dadurch bleiben legitime Null-Updates möglich.

## Risiken

- Unterschiedliche Fenster- oder Owner-Auflösung zwischen Status und Read könnte falsch schützen. Deshalb
  müssen beide dieselben Prädikate verwenden und Paritätstests erhalten.
- Das Laden bestehender Tageswerte erhöht die Arbeit pro Re-Score geringfügig, bleibt aber auf das bereits
  begrenzte Fenster beschränkt.
- Ein synthetischer Restore-Test ersetzt keinen Test mit einer echten Nutzersicherung; dieses Restrisiko
  wird im Pull Request und Issue sichtbar gemacht.

## Fertigstellung

Fertig ist der Change, wenn alle Spezifikationsszenarien auf beiden Plattformen nachgewiesen sind, keine
allgemeine RR- oder Upsert-Regel aufgeweicht wurde, der Abschlussreview keine offene kritische Abweichung
enthält und Pull Request sowie Testaufruf mit ehrlicher Testgrenze veröffentlicht sind.
