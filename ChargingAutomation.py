"""Persistent charging policy and background relay automation."""
import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from WallboxController import SupplyMode


class ChargingAutomation:
    """@brief Steuert die Wallbox nach dem ausgewaehlten Lademodus.

    Die Klasse verwaltet Einstellungen, Sitzungszustand und den periodischen Regler.
    """
    MINIMUM_BATTERY_SOC = 20
    SOLAR_START_OFFSET = 4
    SOLAR_STOP_HYSTERESIS = 5
    ENERGY_STATUS_MAX_AGE_SECONDS = 30
    TICK_SECONDS = 10
    SETTING_KEYS = (
        "charge_mode",
        "car_current_a",
        "full_charge_minutes",
        "ready_at",
        "solar_start_soc",
        "instant_soc_threshold",
    )

    def __init__(self, wallbox, energy_client, settings_path=None, logger=None):
        """@brief Initialisiert Automatik, Konfiguration und Zustands-Caches.

        @param wallbox Relay-Controller fuer Solar- und Netzversorgung.
        @param energy_client Client fuer SOC und Wechselrichter-Messwerte.
        @param settings_path Optionaler Pfad fuer dauerhaft gespeicherte Einstellungen.
        @param logger Optionaler Logger mit Info- und Error-Methoden.
        """
        self.logger = logger
        self.wallbox = wallbox
        self.energy_client = energy_client
        configured_path = settings_path or os.getenv("WALLBOX_SETTINGS_FILE")
        if configured_path:
            self.settings_path = Path(configured_path)
            if not self.settings_path.is_absolute():
                self.settings_path = Path(__file__).resolve().parent / self.settings_path
        else:
            self.settings_path = Path(__file__).resolve().parent / "wallbox_settings.json"
        self._lock = threading.RLock()
        # Serializes policy changes with ticks; status reads use only cached snapshots.
        self._control_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread = None
        self.last_error = None
        self._cached_energy = None
        self._cached_energy_at = None
        self.energy_error = None
        self._status_lock = threading.Lock()
        self.settings, self.session_started_at = self._load_settings()

    def _defaults(self):
        """@brief Liefert die Standardwerte aller Ladeeinstellungen.

        @return Neue Dictionary-Instanz mit den Werkseinstellungen.
        """
        return {
            "charge_mode": "solar",
            "car_current_a": 6,
            "full_charge_minutes": 0,
            "ready_at": "",
            "solar_start_soc": 95,
            "instant_soc_threshold": 50,
        }

    def _load_settings(self):
        """@brief Laedt gespeicherte Einstellungen und ergaenzt fehlende Standardwerte.

        Unbekannte oder veraltete Felder (z. B. paused, manual_override) werden verworfen.
        @return Tupel aus Einstellungen und gespeichertem Sitzungsstart.
        """
        values = self._defaults()
        stored = {}
        try:
            with self.settings_path.open("r", encoding="utf-8") as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            stored = {}
        if not isinstance(stored, dict):
            stored = {}
        values.update({key: stored[key] for key in self.SETTING_KEYS if key in stored})
        # Migrate settings left behind by the removed SOC-threshold mode.
        if values.get("charge_mode") == "soc":
            values["charge_mode"] = "solar"
        return values, stored.get("session_started_at")

    def _persist(self):
        """@brief Schreibt Einstellungen und Sitzungsstart atomar in die Konfigurationsdatei.

        @return None
        """
        data = dict(self.settings)
        data["session_started_at"] = self.session_started_at
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.settings_path.with_suffix(self.settings_path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        temporary.replace(self.settings_path)

    @staticmethod
    def _parse_local(text):
        """@brief Wandelt einen ISO-Zeitstempel in eine zeitzonenbehaftete Zeit um.

        @param text ISO-Datum/Uhrzeit, optional mit Offset.
        @return datetime mit Zeitzone (lokale Zone, falls keine angegeben).
        """
        value = datetime.fromisoformat(text)
        if value.tzinfo is None:
            value = value.astimezone()
        return value

    @staticmethod
    def _now():
        """@brief Aktuelle lokale Zeit mit Zeitzone.

        @return datetime.
        """
        return datetime.now().astimezone()

    def get_settings(self):
        """@brief Gibt eine kopierte Momentaufnahme der Ladeeinstellungen zurueck.

        @return Kopie des Einstellungs-Dictionarys.
        """
        with self._lock:
            return dict(self.settings)

    def update_settings(self, supplied):
        """@brief Serialisiert eine Einstellungsanpassung mit der Ladeautomatik.

        @param supplied Neue Ladeeinstellungen.
        @return Kopie der gespeicherten Einstellungen.
        """
        with self._control_lock:
            return self._update_settings_locked(supplied)

    def _update_settings_locked(self, supplied):
        """@brief Validiert und speichert neue Ladeeinstellungen und wendet sie sofort an.

        Eine laufende Ladung wird nicht pauschal gestoppt: der unmittelbar folgende
        Automatik-Schritt entscheidet nach dem neuen Modus, ob weitergeladen,
        die Quelle gewechselt oder gestoppt wird.
        @param supplied Vom Client uebermittelte Einstellungsfelder.
        @return Kopie der gespeicherten Einstellungen.
        @throws ValueError Bei ungueltigen Werten oder unvollstaendigem Zielzeitprogramm.
        """
        if not isinstance(supplied, dict):
            raise ValueError("settings must be a JSON object")
        with self._lock:
            updated = dict(self.settings)
        # Only known fields are accepted; anything else is ignored.
        updated.update({key: supplied[key] for key in self.SETTING_KEYS if key in supplied})
        if updated["charge_mode"] not in ("solar", "deadline", "instant"):
            raise ValueError("charge_mode must be solar, deadline or instant")
        if int(updated["car_current_a"]) not in (6, 8, 10):
            raise ValueError("car_current_a must be 6, 8 or 10")
        updated["car_current_a"] = int(updated["car_current_a"])
        updated["full_charge_minutes"] = int(updated["full_charge_minutes"])
        if updated["full_charge_minutes"] < 0 or updated["full_charge_minutes"] > 1440:
            raise ValueError("full_charge_minutes must be between 0 and 1440")
        updated["solar_start_soc"] = int(updated["solar_start_soc"])
        if updated["solar_start_soc"] < 93 or updated["solar_start_soc"] > 95:
            raise ValueError("solar_start_soc must be between 93 and 95")
        updated["instant_soc_threshold"] = int(updated["instant_soc_threshold"])
        if updated["instant_soc_threshold"] < self.MINIMUM_BATTERY_SOC or updated["instant_soc_threshold"] > 95:
            raise ValueError("instant_soc_threshold must be between 20 and 95")
        ready_at = str(updated.get("ready_at") or "")
        ready_at_time = None
        if ready_at:
            try:
                ready_at_time = self._parse_local(ready_at)
            except ValueError:
                raise ValueError("ready_at must be an ISO local date/time")
        updated["ready_at"] = ready_at
        if updated["charge_mode"] == "deadline":
            if not ready_at or updated["full_charge_minutes"] <= 0:
                raise ValueError("Deadline mode requires a ready-by date/time and full-charge duration")
            if ready_at_time <= self._now():
                raise ValueError("ready_at must be in the future")

        with self._lock:
            self.settings = updated
            self._persist()
            result = dict(self.settings)
        if self.logger:
            self.logger.Info("Charging settings saved: mode {}".format(updated["charge_mode"]))

        # Apply the new policy right away instead of waiting up to one tick interval.
        try:
            self._tick_locked()
            self._record_tick_result()
        except Exception as exc:
            self._record_tick_result(exc)
        return result

    def _elapsed_seconds(self):
        """@brief Berechnet die Laufzeit der aktuellen Ladesitzung.

        @return Vergangene Sekunden oder 0, wenn keine gueltige Startzeit vorliegt.
        """
        if not self.session_started_at:
            return 0
        try:
            started = self._parse_local(self.session_started_at)
            return max(0, int((self._now() - started).total_seconds()))
        except ValueError:
            return 0

    def _deadline_window(self):
        """@brief Liefert das Zwangslade-Fenster des Zielzeitmodus.

        @return Tupel (spaetester Start, Zielzeit) oder None ohne gueltigen Zeitplan.
        """
        if self.settings.get("charge_mode") != "deadline":
            return None
        ready_at_text = self.settings.get("ready_at", "")
        duration = int(self.settings.get("full_charge_minutes", 0))
        if not ready_at_text or duration <= 0:
            return None
        try:
            ready_at = self._parse_local(ready_at_text)
        except ValueError:
            return None
        return ready_at - timedelta(minutes=duration), ready_at

    def _in_forced_deadline_window(self, now=None):
        """@brief Prueft, ob der Zielzeitmodus gerade zwangsweise laedt.

        @param now Optional vorgegebene aktuelle Zeit.
        @return True zwischen spaetestem Start und Zielzeit.
        """
        window = self._deadline_window()
        if window is None:
            return False
        now = now or self._now()
        return window[0] <= now < window[1]

    def get_status(self):
        """@brief Baut den API-Status aus Cache- und Controller-Snapshots auf.

        Diese Methode fuehrt keine Netzwerkabfragen aus und wartet daher nicht auf Geraete.
        @return Status-Dictionary fuer UI und API.
        """
        # Status requests read only the latest tick and Sonoff worker snapshots.
        with self._status_lock:
            energy = dict(self._cached_energy) if self._cached_energy is not None else None
            energy_updated_at = self._cached_energy_at
            energy_error = self.energy_error
            last_error = self.last_error
        energy_age = time.time() - energy_updated_at if energy_updated_at is not None else None
        energy_available = (
            energy is not None
            and energy_age is not None
            and energy_age <= self.ENERGY_STATUS_MAX_AGE_SECONDS
            and energy_error is None
        )
        if energy is None and energy_error is None:
            energy_error = "No OffgridControl energy data received yet"
        elif energy_age is not None and energy_age > self.ENERGY_STATUS_MAX_AGE_SECONDS:
            energy_error = energy_error or "OffgridControl energy data is stale"
        wallbox_measurements = self.wallbox.get_status()
        mode = SupplyMode(wallbox_measurements.get("mode", "unknown"))
        # The solar Sonoff is powered by the inverter output and is normally
        # unreachable while the inverter is in standby, so it is not an error.
        wallbox_error = wallbox_measurements.get("mains_error") or None
        settings = dict(self.settings)
        window = self._deadline_window()
        elapsed = self._elapsed_seconds()
        remaining = None
        if window is not None and self.session_started_at:
            # Only the deadline mode has a meaningful planned charging duration.
            remaining = max(0, int(settings["full_charge_minutes"]) * 60 - elapsed)
        return {
            "settings": settings,
            "supply_mode": mode.value,
            "charging": mode in (SupplyMode.SOLAR, SupplyMode.MAINS),
            "session_started_at": self.session_started_at,
            "elapsed_seconds": elapsed if self.session_started_at else None,
            "remaining_seconds": remaining,
            "scheduled_start_at": window[0].isoformat() if window else None,
            "deadline_expired": bool(window and self._now() >= window[1]),
            "energy": energy,
            "energy_available": energy_available,
            "energy_age_seconds": energy_age,
            "energy_error": energy_error,
            "wallbox_measurements": wallbox_measurements,
            "wallbox_error": wallbox_error,
            # Only real automation failures (failed tick: energy request or switch command).
            "last_error": last_error,
        }

    def start_worker(self):
        """@brief Startet den Hintergrundthread der Ladeautomatik genau einmal.

        @return None
        """
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="wallbox-automation", daemon=True)
        self._thread.start()

    def stop_worker(self):
        """@brief Fordert das Ende des Automatikthreads an und wartet kurz auf ihn.

        @return None
        """
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _record_tick_result(self, exc=None):
        """@brief Merkt sich das Tick-Ergebnis und loggt nur Zustandswechsel.

        Ein dauerhaft wiederholter Fehler erscheint damit einmal im Log, seine
        Behebung einmal als Info.
        @param exc Aufgetretene Ausnahme oder None bei Erfolg.
        @return None
        """
        message = str(exc) if exc is not None else None
        with self._status_lock:
            previous = self.last_error
            self.last_error = message
        if not self.logger or message == previous:
            return
        if message:
            self.logger.Error("Charging automation tick failed: {}".format(message))
        else:
            self.logger.Info("Charging automation recovered")

    def _run(self):
        """@brief Fuehrt den Steuerungstick regelmaessig aus und protokolliert Fehler.

        @return None; laeuft bis das Stop-Ereignis gesetzt wird.
        """
        while not self._stop_event.is_set():
            try:
                self.tick()
                self._record_tick_result()
            except Exception as exc:
                self._record_tick_result(exc)
            self._stop_event.wait(self.TICK_SECONDS)

    def _mark_session_started(self):
        """@brief Setzt den Sitzungsstart, falls noch keiner vorhanden ist.

        @return None
        """
        if not self.session_started_at:
            self.session_started_at = self._now().isoformat()
            self._persist()

    def _begin_session(self, source):
        """@brief Startet oder routet eine Sitzung auf die angegebene Versorgung.

        @param source Zielmodus SOLAR oder MAINS.
        @return None
        """
        self.wallbox.set_mode(source)
        if self.logger:
            self.logger.Info("Automatic charging started on {}".format(source.value))
        self._mark_session_started()

    def _switch_source(self, source):
        """@brief Wechselt die Versorgung einer laufenden Ladung.

        @param source Neuer Versorgungsmodus SOLAR oder MAINS.
        @return None
        """
        self.wallbox.set_mode(source)
        self._mark_session_started()

    def _end_session(self):
        """@brief Schaltet die Wallbox aus und loescht den Sitzungsstart.

        @return None
        """
        self.wallbox.set_mode(SupplyMode.OFF)
        if self.logger:
            self.logger.Info("Automatic charging stopped")
        self.session_started_at = None
        self._persist()

    def tick(self):
        """@brief Fuehrt einen Ladeautomatik-Tick exklusiv zu Einstellungsupdates aus.

        Der Status-Endpunkt nimmt diesen Lock nicht und bleibt aus dem Cache bedienbar.
        @return None
        """
        with self._control_lock:
            return self._tick_locked()

    def _tick_locked(self):
        """@brief Liest Messwerte und wendet genau einen Schritt der Ladepolitik an.

        @return None
        """
        try:
            energy = self.energy_client.get_values()
        except Exception as exc:
            with self._status_lock:
                self.energy_error = str(exc)
            raise
        with self._status_lock:
            self._cached_energy = dict(energy)
            self._cached_energy_at = time.time()
            self.energy_error = None
        # Until the first Sonoff poll completes, mode and availability are placeholders.
        if not self.wallbox.has_status():
            return
        soc = float(energy["soc"])
        source = self.wallbox.get_mode()
        charge_mode = self.settings["charge_mode"]
        now = self._now()

        # Hard battery floor applies to every automatic mode.
        if soc <= self.MINIMUM_BATTERY_SOC and source == SupplyMode.SOLAR:
            if charge_mode == "instant" or (
                    charge_mode == "deadline" and self._in_forced_deadline_window(now)):
                self._switch_source(SupplyMode.MAINS)
            else:
                self._end_session()
            return

        if charge_mode == "solar":
            self._apply_solar_only(soc, source)
        elif charge_mode == "instant":
            self._apply_instant(soc, source)
        else:
            self._apply_deadline(soc, source, now)

    def _apply_solar_only(self, soc, source):
        """@brief Lademodus "Nur Solar" (auch fuer den Zielzeitmodus ausserhalb des Zwangsfensters).

        Start ab konfiguriertem SOC + 4 Prozentpunkten, Stopp 5 Prozentpunkte darunter.
        Netzstrom wird in diesem Modus nicht zum Laden verwendet.
        @param soc Aktueller Hausakku-SOC.
        @param source Aktueller Versorgungsmodus.
        @return None
        """
        start_soc = int(self.settings["solar_start_soc"]) + self.SOLAR_START_OFFSET
        stop_soc = start_soc - self.SOLAR_STOP_HYSTERESIS
        if source == SupplyMode.SOLAR:
            if soc <= stop_soc:
                self._end_session()
        elif source == SupplyMode.MAINS:
            if soc >= start_soc and self.wallbox.solar_available is True:
                self._begin_session(SupplyMode.SOLAR)
            else:
                self._end_session()
        elif soc >= start_soc and self.wallbox.solar_available is True:
            # OFF or UNKNOWN (e.g. mains Sonoff briefly unreachable).
            self._begin_session(SupplyMode.SOLAR)

    def _apply_instant(self, soc, source):
        """@brief Lademodus "Sofort laden".

        Startet sofort: ab SOC-Ziel ueber Solar (falls erreichbar), sonst ueber Netz.
        Unter dem Ziel wird auf Netz gewechselt; Solar kommt ab Ziel + 5 % zurueck.
        @param soc Aktueller Hausakku-SOC.
        @param source Aktueller Versorgungsmodus.
        @return None
        """
        threshold = int(self.settings["instant_soc_threshold"])
        solar_ok = self.wallbox.solar_available is True
        if source in (SupplyMode.OFF, SupplyMode.UNKNOWN):
            use_solar = soc > self.MINIMUM_BATTERY_SOC and soc >= threshold and solar_ok
            self._begin_session(SupplyMode.SOLAR if use_solar else SupplyMode.MAINS)
        elif source == SupplyMode.SOLAR and soc < threshold:
            self._switch_source(SupplyMode.MAINS)
        elif source == SupplyMode.MAINS and soc >= threshold + self.SOLAR_STOP_HYSTERESIS and solar_ok:
            self._switch_source(SupplyMode.SOLAR)
        else:
            self._mark_session_started()

    def _apply_deadline(self, soc, source, now):
        """@brief Lademodus "Fahrzeug bereit bis".

        Vor dem spaetesten Start und nach Ablauf der Zielzeit wird nur wie im
        Solar-Modus geladen. Im Zwangsfenster (Zielzeit minus Ladedauer bis Zielzeit)
        wird geladen: Solar bei ausreichendem SOC und erreichbarem Solar-Sonoff,
        sonst Netz. Die harte Akku-Untergrenze betraegt 20 %.
        @param soc Aktueller Hausakku-SOC.
        @param source Aktueller Versorgungsmodus.
        @param now Aktuelle Zeit.
        @return None
        """
        window = self._deadline_window()
        if window is None:
            return
        if not self._in_forced_deadline_window(now):
            # Outside the forced window only surplus solar charging is allowed;
            # a forced mains session therefore ends at the ready-by time.
            self._apply_solar_only(soc, source)
            return
        if source in (SupplyMode.OFF, SupplyMode.UNKNOWN):
            use_solar = soc > self.MINIMUM_BATTERY_SOC and self.wallbox.solar_available is True
            self._begin_session(SupplyMode.SOLAR if use_solar else SupplyMode.MAINS)
        else:
            self._mark_session_started()
