# Wallbox

FastAPI-Dienst zur Steuerung der Wallbox-Versorgung über zwei Sonoff POW Elite (Tasmota): ein Schalter für Solar (Wechselrichter-Ausgang), einer für Netz. Schwesterprojekt zu OffgridControl2, aus dem SOC und Leistungswerte gelesen werden.

## Start

Auf dem Server (Linux):

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
./startService.sh     # startet uvicorn im Hintergrund auf Port 8001
./stopService.sh
```

Zum Entwickeln geht auch `python main.py`. Die Oberfläche ist danach unter http://<host>:8001 erreichbar.

### Umgebungsvariablen

| Variable | Standard | Bedeutung |
|---|---|---|
| `WALLBOX_SW_SOLAR_HOST` | `WallboxSwSolar` | IP/DNS-Name des Solar-Sonoffs |
| `WALLBOX_SW_MAINS_HOST` | `WallboxSwMains` | IP/DNS-Name des Netz-Sonoffs |
| `OFFGRIDCONTROL_URL` | `http://127.0.0.1:8000` | Basis-URL von OffgridControl2 |
| `WALLBOX_SETTINGS_FILE` | `wallbox_settings.json` | Einstellungsdatei (relativ zum Projektordner) |
| `WALLBOX_LOG_LEVEL` | `INFO` | `NONE`, `ERROR`, `INFO`, `DEBUG` |
| `WALLBOX_DAILY_LOG_PREFIX` | `log/Wallbox_` | Präfix der Tageslogdateien |

## HTTP-API

- `GET /` – Weboberfläche
- `GET /api/charging/status` – Einstellungen, aktuelle Quelle, Ladezeit/Restzeit (nur Zielzeit-Modus), Energiewerte aus dem Cache, Sonoff-Messwerte und Fehler. Wartet nie auf Geräte.
- `PUT /api/charging/settings` – speichert die Einstellungen (JSON) und wendet sie sofort an. Unbekannte Felder werden ignoriert; ungültige Werte ergeben HTTP 400.
- `GET /api/wallbox/status` – letzter Sonoff-Snapshot (Relaiszustände, Erreichbarkeit, POW-Messwerte)
- `GET /api/offgrid/energy` – aktuelle Werte direkt von OffgridControl2

Die API hat keine Authentifizierung und ist nur für das lokale Netz gedacht.

Einstellungsfelder: `charge_mode` (`solar` | `instant` | `deadline`), `car_current_a` (6/8/10, nur Info – der Strom wird im Fahrzeug eingestellt), `full_charge_minutes` (0–1440), `ready_at` (ISO-Zeit mit Offset), `solar_start_soc` (93–95), `instant_soc_threshold` (20–95).

## Relais-Logik

Der Versorgungsmodus wird aus beiden Relais abgeleitet:

| Solar | Netz | Modus |
|---|---|---|
| ON | beliebig | `solar` |
| OFF | ON | `mains` |
| OFF | OFF | `off` |
| nicht erreichbar | ON | `mains` |
| sonst | | `unknown` |

- **Solar:** schaltet Solar ein. Ein bereits eingeschaltetes Netzrelais bleibt bewusst an (unterbrechungsfreier Wechsel; fällt der Solar-Sonoff im Power-Save aus, übernimmt das Netz).
- **Netz:** Netz ein, danach Solar aus (ein nicht erreichbarer Solar-Sonoff wird toleriert).
- **Aus:** beide Relais aus.

Alle Sonoff-Zugriffe laufen seriell über einen Hintergrund-Worker. Er pollt beim Laden alle 10 s, sonst alle 5 min, und nach jedem Schaltbefehl sofort.

## Lademodi

Die Automatik läuft alle 10 s und direkt nach jedem Speichern. Sie handelt erst, wenn der erste Sonoff-Poll abgeschlossen ist. Für alle Modi gilt die harte Untergrenze von **20 % Hausakku-SOC**: Wird sie beim Solarladen erreicht, wird auf Netz umgeschaltet (Sofort laden, Zielzeit im Zwangsfenster) bzw. abgeschaltet (sonst).

Ein Moduswechsel stoppt eine laufende Ladung nicht pauschal; der neue Modus entscheidet sofort, ob weitergeladen, die Quelle gewechselt oder gestoppt wird.

- **Nur Solar:** Start bei Hausakku-SOC ≥ eingestellter Schwellwert + 4 % (also 97–99 %), Stopp bei ≤ Start − 5 %. Entscheidend ist nur der SOC; Überschuss und Batteriestrom werden angezeigt, aber nicht ausgewertet. Gestartet wird nur, wenn der Solar-Sonoff erreichbar ist. Netzstrom wird nicht zum Laden verwendet.
- **Sofort laden:** startet sofort. Liegt der SOC ≥ eingestelltem Ziel und ist Solar erreichbar, über Solar, sonst über Netz. Unter dem Ziel wird auf Netz gewechselt; zurück auf Solar ab Ziel + 5 %.
- **Fahrzeug bereit bis:** Der späteste Start ist Zielzeit − Ladedauer. Davor und nach Ablauf der Zielzeit gilt die Logik von „Nur Solar“. Im Zwangsfenster (spätester Start bis Zielzeit) wird geladen: über Solar, wenn SOC > 20 % und der Solar-Sonoff erreichbar ist, sonst über Netz. Eine Netzladung endet zur Zielzeit. Die Zielzeit muss beim Speichern in der Zukunft liegen.

Einstellungen und Sitzungsstart liegen in `wallbox_settings.json` (atomar geschrieben).

## Sonoff POW Elite (Tasmota32)

`SonoffPowElite.py` kapselt die lokale Tasmota-HTTP-API (`/cm?cmnd=...`, [Tasmota Commands](https://github.com/tasmota/docs/blob/master/docs/Commands.md)):

```python
from SonoffPowElite import SonoffPowElite

pow = SonoffPowElite("192.168.1.42")  # oder DNS-Name
pow.turn_on()
print(pow.get_power_state())
values = pow.get_energy_values()      # aus "Status 10" → StatusSNS.ENERGY
print(values.voltage, values.current, values.active_power)
pow.turn_off()
pow.close()
```

Kommunikationsfehler werden als `SonoffCommunicationError` mit kurzem, stabilem Text gemeldet (z. B. `wallboxswsolar 'Power': connect timeout`).

## OffgridControl-Energiewerte

Gelesen werden `SOC`, `PowerSolar1`, `PowerSolar2`, `InverterOutputPower` und `BatteryCurrent` aus `/api/inverterValues`, ergänzt um `total_solar_power` und Ladeströme aus `/api/victronValues`, sofern verfügbar. Überschuss = Solar gesamt − AC-Ausgang. Werte älter als 30 s gelten in der Oberfläche als veraltet.

## Logging

- `log/Wallbox_YYYY-MM-DD.log`: Anwendungsereignisse und Fehler. Wiederholte gleiche Fehler werden nur beim ersten Auftreten und bei der Wiederherstellung geloggt.
- Der Solar-Sonoff wird vom Wechselrichter-Ausgang versorgt und ist im Wechselrichter-Standby (z. B. bei niedrigem SOC) nicht erreichbar. Das ist ein normaler Zustand: Er wird nur in der Solarzähler-Karte angezeigt und als INFO geloggt, nicht als Fehler. Details zu jedem einzelnen Fehlversuch gibt es mit `WALLBOX_LOG_LEVEL=DEBUG`.
- `log/errorYYYY-MM-DD_HH-MM.log`: Konsolenausgabe von uvicorn (inkl. Access-Log), eine Datei pro Start über `startService.sh`.
- Logs werden nicht automatisch gelöscht.

## Tests

```bash
python -m unittest discover -s tests
```

Die Tests laufen ohne Geräte (Fakes) und ohne installiertes `requests`.
