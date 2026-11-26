# Wallbox-Seite: Implementierungsplan

## Ziel

Eine eigenständige Wallbox-Seite parallel zu OffgridControl2. Sie verwendet denselben Technologie- und Gestaltungsansatz wie die bestehende Wallbox-Anwendung: Python/FastAPI im Backend, HTML/CSS/JavaScript im Frontend, responsive Darstellung und einheitliche Optik. Die Seite steuert die Wallbox über zwei Sonoff POW Elite:

- `WallboxSwSolar`: wählt den Solarweg und misst dessen Strom.
- `WallboxSwMains`: gibt die Wallbox grundsätzlich frei und misst den Netzstrom.

Die Seite zeigt den Energiezustand von OffgridControl und erlaubt genau einen aktiven Lademodus.

## Seitenaufbau

### Kopfbereich

- Titel „Wallbox“ und Verbindungsstatus zu Wallbox und OffgridControl.
- Status der aktuellen Ladung: Aus, Solarversorgung, Netzversorgung oder Wartezustand.
- Aktionen „Ladung starten“ und „Ladung stoppen“. Im Modus „Laden bis“ muss die Startaktion klar als sofortiger manueller Start beschriftet sein, damit sie nicht mit dem Aktivieren des Zeitplans verwechselt wird.

### Energiekacheln

Livewerte aus OffgridControl:

- Hausakku-SOC in Prozent, ergänzt durch eine Fortschrittsanzeige.
- Solarstrom gesamt in kW.
- Überschussstrom in kW, berechnet als Solarstrom gesamt minus AC-Ausgangsleistung.
- Optional den Ladezustand des Hausakkus (lädt/lädt nicht) und dessen Strom anzeigen, wenn der Messwert verfügbar ist.

Ladeinformationen:

- Eingestellter Fahrzeugladestrom (6 A, 8 A oder 10 A).
- Ladezeit seit Sitzungsbeginn.
- Geschätzte verbleibende Ladezeit; wenn keine Laufzeit geschätzt werden kann, „—“ anzeigen.
- Aktuelle Quelle (Solar, Netz, aus) sowie Warte-/Stoppgrund.

### Einstellungen

Gemeinsame Einstellung:

- Ladestrom im Fahrzeug: Auswahl 6 A, 8 A oder 10 A.
- Ladezeit bis 100 % in Stunden. Sie wird im Modus „Laden bis“ zur Berechnung des spätesten Startzeitpunkts verwendet.

Drei gegenseitig ausschließende Lademodi als Radio-Auswahl/Karten. Nur die Eingabefelder des ausgewählten Modus sind aktiv; die anderen Optionen und Felder werden sichtbar ausgegraut und deaktiviert.

1. **Nur Solar**
   - Start-SOC des Hausakkus: 93–98 %.
   - Kein Zeitfeld.

2. **Fahrzeug bereit bis**
   - Datum und Uhrzeit, zu der das Fahrzeug fertig geladen sein soll.
   - Ladezeit bis 100 % in Stunden.
   - Die Oberfläche zeigt den berechneten geplanten Start: Zielzeit minus Ladezeit.
   - Hinweistext: Der Zeitplan startet zum berechneten Zeitpunkt unabhängig vom Solarüberschuss. Unter 20 % Hausakku-SOC wird auf Netz umgeschaltet.

3. **Sofort laden**
   - SOC-Schwelle zum Wechsel zwischen Solar und Netz, mindestens 20 %.
   - Startet nach Uebernahme sofort.

„Einstellungen übernehmen“ speichert Modus und Eingaben und aktiviert die Automatik. Ein geänderter Modus oder ein geänderter Zeitplan beendet zunächst einen eventuell laufenden Ladevorgang, damit alte Einstellungen nicht weiterwirken.

## Relaislogik

Die Kombinationen der beiden Schalter bedeuten:

| `WallboxSwSolar` | `WallboxSwMains` | Ergebnis |
|---|---|---|
| EIN | AUS | Laden über Solar |
| EIN | EIN | Laden über Solar |
| AUS | EIN | Laden über Netz |
| AUS | AUS | Wallbox aus |

Backend-Code soll diese Kombinationen über eine zentrale Abstraktion wie `SupplyMode` steuern. Die UI soll keine einzelnen Relais direkt schalten.

## Regeln der Lademodi

### Nur Solar

- Automatischer Start nur, wenn der Hausakku mindestens den konfigurierten Start-SOC erreicht hat (zulässig 93–98 %), der Akku weiterhin lädt (Batteriestrom positiv) und der Überschuss positiv ist.
- Während des Ladens weiter überwachen.
- Stoppen, sobald der SOC auf Start-SOC minus 5 Prozentpunkte oder darunter fällt, der Akku nicht mehr lädt oder kein positiver Überschuss mehr vorhanden ist.
- Die 5 Prozentpunkte sind die Hysterese: Sie verhindern häufiges Ein-/Ausschalten nahe der Startgrenze.
- Kein automatischer Netzbezug in diesem Modus.

### Sofort laden

- Nach ?bernahme dieses Modus unverz?glich laden.
- SOC-Schwelle mindestens 20 %. Bei SOC oberhalb der Schwelle ?ber Solar starten.
- Sobald der Hausakku-SOC unter die eingestellte Schwelle f?llt, auf Netz umschalten und die Fahrzeugladung fortsetzen; nicht pausieren oder beenden.
- Ist der SOC beim Start schon unter der Schwelle, direkt Netz w?hlen.
- Bei 20 % Hausakku-SOC sp?testens auf Netz umschalten, damit die Batterie nicht weiter entladen wird.

### Fahrzeug bereit bis (Zeitplan)

- Den spätesten Startzeitpunkt aus Zielzeit minus Ladezeit bis 100 % berechnen.
- Vor diesem Zeitpunkt automatisch Solarbedingungen berücksichtigen. Sind Hausakku-SOC mindestens auf dem eingestellten Solar-Startwert (93–98 %), positiver Batterieladestrom und positiver Überschuss vorhanden, darf die Wallbox über Solar laden. Dafür gelten dieselben Stoppregeln und die 5-Prozentpunkte-Hysterese wie im Modus „Nur Solar“.
- Wenn die Solarbedingungen nicht erfüllt sind, bleibt die Wallbox vor dem spätesten Startzeitpunkt aus.
- Zum spätesten Startzeitpunkt beginnt oder setzt die Steuerung die Ladung unabhängig vom Überschuss fort. Das ist der geplante Zwangsstart.
- Beim Zwangsstart zunächst über Solar laden, solange der Hausakku über 20 % liegt. Bei 20 % auf Netz umschalten; Netzladen darf den Hausakku nicht weiter entladen.
- Vorherige Solarladung zählt als aktive Fahrzeugsitzung. Sitzungsdauer und Restzeit anzeigen; den geplanten Zwangsstart zusätzlich separat anzeigen.
- Ist der berechnete Startzeitpunkt bereits vorbei, den Zwangsstart unmittelbar ausführen.
- Zielzeiten zeitzonenbewusst behandeln. Das Frontend sendet die lokale Zielzeit mit UTC-Offset; Backend-Vergleiche erfolgen mit Zeitzonen.
## Daten und API

Persistierte Einstellungen:

- `charge_mode`: `solar`, `deadline` oder `instant`.
- `car_current_a`: 6, 8 oder 10.
- `full_charge_minutes`: Ladezeit in Minuten.
- `ready_at`: lokale Zielzeit mit Zeitzonenoffset.
- `solar_start_soc`: 93–98.
- `instant_soc_threshold`: SOC-Schwelle zum Solar-zu-Netz-Wechsel im Sofortmodus, mindestens 20.
- Laufzeitstatus wie `paused` und `session_started_at` getrennt von den Benutzereinstellungen behandeln.

Die Seite braucht mindestens Endpunkte für:

- Status: Einstellungen, Relais-/Lademodus, Energie-Messwerte, Sitzungsdauer, Restzeit, berechneter Startzeitpunkt, Fehler.
- Einstellungen übernehmen/validieren.

Validierungsfehler sollen als verständlicher Text auf der Seite erscheinen. Bei Ausfall von Wallbox oder OffgridControl Messwerte als nicht verfügbar kennzeichnen und keinen unsicheren automatischen Start aus unbekannten Messwerten ableiten.

## Zustandsdarstellung

- **Aus**: beide Relais aus.
- **Warte auf Solarbedingungen**: Solar-Modus aktiv, SOC/ Akku-Ladezustand/Überschuss erfüllt die Startbedingungen noch nicht.
- **Warte auf geplanten Start**: Zeitplan aktiv, berechneter Start liegt in der Zukunft; Startdatum und Uhrzeit anzeigen.
- **Lädt über Solar** oder **Lädt über Netz**: aktive Quelle klar anzeigen.
- **Fehler**: Verbindungs- oder Messfehler mit kurzer Ursache.

## Prüfpunkte für die Implementierung

- Zu jeder Zeit ist höchstens ein Lademodus ausgewählt und nur dessen Felder sind bedienbar.
- Vor dem geplanten Zwangsstart lädt die Wallbox nur dann, wenn die Solarbedingungen des Solar-Modus erfüllt sind; alte Sitzungsdaten dürfen keinen Netz- oder unbeabsichtigten Ladevorgang aktiv halten.
- Bei Zielzeit morgen 18:00 Uhr und sechs Stunden Ladezeit ist der Zwangsstart morgen 12:00 Uhr. Vor 12:00 Uhr darf nur bei erfüllten Solarbedingungen geladen werden; ohne Solarbedingungen bleibt die Wallbox aus.
- Zum geplanten Start beginnt oder setzt Laden auch ohne Überschuss fort, selbst wenn zuvor bereits solar geladen wurde.
- Bei SOC 20 % im Zeitplanmodus wird von Solar auf Netz umgeschaltet; in den anderen Modi wird nicht unter 20 % weiter entladen.
- Der Sofortmodus startet nach ?bernahme direkt. Unter seiner SOC-Schwelle wird auf Netz umgeschaltet und weitergeladen.
- Der SOC-Schieber hat einen Mindestwert von 20 % und Backend-Validierung weist kleinere Werte ab.
- Angezeigte Ladezeit, Restzeit, SOC, Solar- und Überschusswerte aktualisieren sich regelmäßig.
- Fehlerhafte oder unvollständige Eingaben werden abgewiesen, ohne einen laufenden Zeitplan unbeabsichtigt zu starten.

## Erklärungstexte für die Lademodi auf der Seite

Diese kurzen Texte können direkt unter den jeweiligen Modus-Karten angezeigt werden:

### Nur Solar — „Überschuss nutzen“

„Das Auto lädt nur mit Solarüberschuss. Die Ladung startet, wenn der Hausakku den gewählten Ladezustand erreicht hat, weiter geladen wird und Strom übrig ist. Sinkt der Akku um 5 Prozentpunkte unter den Startwert oder fällt der Überschuss weg, pausiert die Ladung. So wird möglichst wenig Strom aus dem Netz bezogen.“

**Kurz erklärt:** Der Hausakku bekommt Vorrang. Das Auto nutzt nur Energie, die nach der Hausversorgung übrig bleibt.

### Fahrzeug bereit bis — „Zu einer Zielzeit fertig sein“

„Gib an, wann das Auto fertig geladen sein soll und wie lange es bis 100 % laden muss. Daraus berechnet die Steuerung den spätesten Startzeitpunkt. Beispiel: Ziel 18:00 Uhr, Ladezeit 6 Stunden — Zwangsstart spätestens um 12:00 Uhr. Vorher nutzt die Steuerung Solar automatisch, sobald die Solarbedingungen erfüllt sind. Zum spätesten Startzeitpunkt wird die Ladung auch ohne Überschuss fortgesetzt: zunächst über Solar, bei 20 % Hausakku-SOC automatisch über das Netz.“

**Kurz erklärt:** Dieser Modus lädt vorzeitig mit Solar, wenn genug Überschuss vorhanden ist, und stellt zum berechneten spätesten Start die Ladung sicher. Die Ladezeit ist eine Schätzung des Nutzers; die Steuerung kann den tatsächlichen Ladefortschritt des Autos nicht selbst bestimmen.

### Sofort laden ? ?Solar nutzen und bei niedrigem Akku mit Netz weiterladen?

?Die Ladung beginnt sofort, sobald du diesen Modus ?bernimmst. Solange der Hausakku ?ber dem eingestellten SOC liegt, wird Solar verwendet. Sinkt der SOC unter den Grenzwert, schaltet die Wallbox auf Netz um und l?dt weiter, statt abzubrechen. Der Grenzwert kann nicht unter 20 % eingestellt werden.?

**Kurz erkl?rt:** Der SOC-Wert bestimmt, wann Solar durch Netzstrom ersetzt wird.


