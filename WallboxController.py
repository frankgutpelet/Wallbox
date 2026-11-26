"""Coordinates Sonoff communication through one explicitly started background worker."""
import queue
import threading
import time
from enum import Enum

from SonoffPowElite import SwitchState


class SupplyMode(Enum):
    """@brief Definiert die moeglichen Versorgungswege der Wallbox.

    SOLAR und MAINS kennzeichnen aktive Versorgung; OFF und UNKNOWN
    beschreiben ausgeschaltete beziehungsweise nicht bestaetigte Zustaende.
    """
    SOLAR = "solar"
    MAINS = "mains"
    OFF = "off"
    UNKNOWN = "unknown"


class _SonoffCommunicationWorker:
    """@brief Serialisiert Sonoff-I/O und verwaltet den letzten Messwert-Snapshot.

    Im Standby wird alle fuenf Minuten, waehrend des Ladens alle zehn Sekunden
    gepollt.
    """
    STANDBY_POLL_SECONDS = 300
    CHARGING_POLL_SECONDS = 10
    # A command task can make up to six Tasmota requests (2 switch + 4 poll),
    # each with 1.5 s connect + 1.5 s read timeout, plus a queued task ahead.
    REQUEST_TIMEOUT_SECONDS = 45

    def __init__(self, solar_switch, mains_switch, logger):
        """@brief Bereitet Worker, Warteschlange und Anfangsstatus vor.

        Der Thread wird erst durch start() gestartet.
        @param solar_switch Sonoff fuer die Solarversorgung.
        @param mains_switch Sonoff fuer die Netzversorgung.
        @param logger Optionaler Logger.
        """
        self.solar_switch = solar_switch
        self.mains_switch = mains_switch
        self.logger = logger
        self._tasks = queue.Queue()
        self._snapshot_lock = threading.RLock()
        self._stop = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._started = False
        self._thread = None
        self._last_operation_error = None
        self._snapshot = {
            "mode": SupplyMode.UNKNOWN.value,
            "wallbox_sw_solar": SwitchState.UNKNOWN.value,
            "wallbox_sw_mains": SwitchState.UNKNOWN.value,
            "solar_energy": None,
            "mains_energy": None,
            "solar_available": None,
            "mains_available": None,
            "solar_error": None,
            "mains_error": None,
            "solar_meter_error": None,
            "mains_meter_error": None,
            "updated_at": None,
        }

    def start(self):
        """@brief Startet die Sonoff-Kommunikation beim Eintritt in den App-Lifespan.

        @return None
        """
        with self._lifecycle_lock:
            if self._started:
                return
            if self._stop.is_set():
                raise RuntimeError("Wallbox Sonoff communication worker is stopped")
            self._thread = threading.Thread(
                target=self._run,
                name="wallbox-sonoff-communication",
                daemon=True,
            )
            self._started = True
            # Queue one initial poll before starting; standby polling is then every five minutes.
            self._tasks.put({"kind": "refresh"})
            self._thread.start()

    def _log(self, level, message):
        """@brief Schreibt eine Meldung, falls ein Logger verfuegbar ist.

        @param level Logger-Methode, typischerweise Info oder Error.
        @param message Zu protokollierender Text.
        @return None
        """
        if not self.logger:
            return
        method = getattr(self.logger, level, None)
        if method:
            method(message)

    def snapshot(self):
        """@brief Kopiert den letzten thread-sicheren Geraetesnapshot.

        @return Dictionary mit Modi, Verfuegbarkeit, Messwerten und Fehlern.
        """
        with self._snapshot_lock:
            return dict(self._snapshot)

    @staticmethod
    def _mode_from_states(solar_state, mains_state):
        """@brief Leitet den Versorgungsmodus aus beiden Relaiszustnden ab.

        @param solar_state Zustand des Solarrelais.
        @param mains_state Zustand des Netzrelais.
        @return Abgeleiteter SupplyMode inklusive UNKNOWN bei unklarem Zustand.
        """
        if solar_state == SwitchState.ON:
            return SupplyMode.SOLAR
        if solar_state == SwitchState.OFF and mains_state == SwitchState.ON:
            return SupplyMode.MAINS
        if solar_state == SwitchState.OFF and mains_state == SwitchState.OFF:
            return SupplyMode.OFF
        # If solar is unreachable, a confirmed mains relay is the safe usable route.
        if solar_state == SwitchState.UNKNOWN and mains_state == SwitchState.ON:
            return SupplyMode.MAINS
        return SupplyMode.UNKNOWN

    def _read_switch(self, name, switch):
        """@brief Liest Relaiszustand und Zaehlerwerte eines Sonoffs.

        @param name Bezeichner fuer Log- und Snapshot-Felder.
        @param switch Zu lesender SonoffPowElite-Client.
        @return Tupel aus Zustand, Erreichbarkeit, Verbindungsfehler, Messwerten und Zaehlerfehler.
        """
        try:
            state = switch.get_power_state()
            if state not in (SwitchState.ON, SwitchState.OFF):
                raise RuntimeError("Sonoff returned an unknown relay state")
            old_error = self.snapshot().get(name + "_error")
            if old_error:
                self._log("Info", "{} Sonoff connection restored".format(name))
            connection_error = None
            available = True
        except Exception as exc:
            state = SwitchState.UNKNOWN
            connection_error = str(exc)
            old_error = self.snapshot().get(name + "_error")
            if old_error != connection_error:
                self._log("Error", "{} Sonoff communication failed: {}".format(name, exc))
            available = False

        energy = None
        meter_error = None
        if available:
            try:
                energy = switch.get_energy_values().__dict__
                old_meter_error = self.snapshot().get(name + "_meter_error")
                if old_meter_error:
                    self._log("Info", "{} Sonoff meter read recovered".format(name))
            except Exception as exc:
                meter_error = str(exc)
                old_meter_error = self.snapshot().get(name + "_meter_error")
                if old_meter_error != meter_error:
                    self._log("Error", "{} Sonoff meter read failed: {}".format(name, exc))
        return state, available, connection_error, energy, meter_error

    def _poll(self):
        """@brief Liest beide Sonoffs und veroeffentlicht einen neuen Snapshot.

        @return Aus den Relaiszustnden abgeleiteter Versorgungsmodus.
        """
        solar = self._read_switch("solar", self.solar_switch)
        mains = self._read_switch("mains", self.mains_switch)
        mode = self._mode_from_states(solar[0], mains[0])
        with self._snapshot_lock:
            self._snapshot.update({
                "mode": mode.value,
                "wallbox_sw_solar": solar[0].value,
                "wallbox_sw_mains": mains[0].value,
                "solar_available": solar[1],
                "mains_available": mains[1],
                "solar_error": solar[2],
                "mains_error": mains[2],
                "solar_energy": solar[3],
                "mains_energy": mains[3],
                "solar_meter_error": solar[4],
                "mains_meter_error": mains[4],
                "updated_at": time.time(),
            })
        return mode

    def _next_interval(self):
        """@brief Waehlt den naechsten Abfrageabstand abhaengig vom Ladebetrieb.

        @return Sekunden bis zum naechsten periodischen Poll.
        """
        mode = self.snapshot().get("mode")
        if mode in (SupplyMode.SOLAR.value, SupplyMode.MAINS.value):
            return self.CHARGING_POLL_SECONDS
        return self.STANDBY_POLL_SECONDS

    def _run(self):
        """@brief Verarbeitet Geraetebefehle und periodische Abfragen seriell.

        @return None; endet beim Stop-Ereignis oder Stop-Auftrag.
        """
        next_poll = time.monotonic()
        while not self._stop.is_set():
            task = None
            try:
                wait_seconds = max(0, next_poll - time.monotonic())
                task = self._tasks.get(timeout=wait_seconds)
            except queue.Empty:
                task = {"kind": "refresh"}

            if task is None:
                break

            if task["kind"] == "stop":
                break

            try:
                result = None
                if task["kind"] == "command":
                    result = task["action"]()
                self._poll()
                if task.get("after_poll"):
                    result = task["after_poll"](result)
                task["result"] = result
                if task["kind"] == "command" and self._last_operation_error:
                    self._log("Info", "Wallbox Sonoff worker operations recovered")
                    self._last_operation_error = None
            except Exception as exc:
                task["error"] = exc
                # The automation retries every tick; log a repeated failure only once.
                if str(exc) != self._last_operation_error:
                    self._log("Error", "Wallbox Sonoff worker operation failed: {}".format(exc))
                self._last_operation_error = str(exc)
                # Refresh the cached device state after failed commands as well.
                try:
                    self._poll()
                except Exception as poll_exc:
                    self._log("Error", "Wallbox Sonoff status refresh failed: {}".format(poll_exc))
            finally:
                if task.get("done"):
                    task["done"].set()
                next_poll = time.monotonic() + self._next_interval()

    def _submit(self, task, wait=True):
        """@brief Stellt einen Worker-Auftrag ein und wartet optional auf sein Ergebnis.

        @param task Auszufuehrender Refresh- oder Befehlsauftrag.
        @param wait Bei True auf Abschluss bis zum Worker-Timeout warten.
        @return Auftragsergebnis oder None bei asynchronem Einreihen.
        @throws TimeoutError Wenn der Worker den Auftrag nicht rechtzeitig beendet.
        """
        if self._stop.is_set():
            raise RuntimeError("Wallbox Sonoff communication worker is stopped")
        if not self._started:
            raise RuntimeError("Wallbox Sonoff communication worker has not been started")
        task["done"] = threading.Event()
        self._tasks.put(task)
        if not wait:
            return None
        if not task["done"].wait(self.REQUEST_TIMEOUT_SECONDS):
            raise TimeoutError("Timed out waiting for the wallbox Sonoff communication worker")
        if "error" in task:
            raise task["error"]
        return task.get("result")

    def refresh(self, wait=True):
        """@brief Fordert eine sofortige Aktualisierung beider Geraete an.

        @param wait Auf Abschluss warten, falls True.
        @return Worker-Ergebnis oder None.
        """
        return self._submit({"kind": "refresh"}, wait=wait)

    def execute(self, action, after_poll=None):
        """@brief Fuehrt eine Geraeteaktion seriell aus und aktualisiert danach den Status.

        @param action Aufrufbare Geraeteaktion.
        @param after_poll Optionale Funktion zur Pruefung des Ergebnisses nach dem Poll.
        @return Ergebnis der Aktion oder Nachpruefung.
        """
        return self._submit({
            "kind": "command",
            "action": action,
            "after_poll": after_poll,
        })

    def close(self):
        """@brief Stoppt den Worker und schliesst beide Sonoff-HTTP-Sessions.

        @return None
        """
        if self._stop.is_set():
            return
        self._stop.set()
        if self._started:
            self._tasks.put({"kind": "stop"})
            if self._thread is not threading.current_thread():
                self._thread.join(timeout=self.REQUEST_TIMEOUT_SECONDS)
        self.solar_switch.close()
        self.mains_switch.close()


class WallboxController:
    """@brief Steuert die beiden Sonoffs ueber einen Hintergrund-Worker.

    Der Controller startet den Worker explizit und liefert Statusabfragen aus
    dessen Cache.
    """

    def __init__(self, solar_switch, mains_switch, logger=None):
        """@brief Erstellt die Controller-Fassade ohne den I/O-Thread zu starten.

        @param solar_switch Sonoff-Client fuer Solarumschaltung und Messung.
        @param mains_switch Sonoff-Client fuer Netzversorgung und Messung.
        @param logger Optionaler Logger.
        """
        self.logger = logger
        self.solar_switch = solar_switch
        self.mains_switch = mains_switch
        self._closed = False
        self._worker = _SonoffCommunicationWorker(solar_switch, mains_switch, logger)

    def start(self):
        """Start Sonoff communication after the application enters its lifespan."""
        if self._closed:
            raise RuntimeError("Wallbox controller is closed")
        self._worker.start()

    @property
    def solar_available(self):
        """@brief Gibt den zuletzt bekannten Online-Status des Solar-Sonoffs zurueck.

        @return True, False oder None, solange noch kein Poll abgeschlossen ist.
        """
        return self._worker.snapshot().get("solar_available")

    @property
    def mains_available(self):
        """@brief Gibt den zuletzt bekannten Online-Status des Netz-Sonoffs zurueck.

        @return True, False oder None, solange noch kein Poll abgeschlossen ist.
        """
        return self._worker.snapshot().get("mains_available")

    @property
    def solar_error(self):
        """@brief Gibt den letzten Verbindungsfehler des Solar-Sonoffs zurueck.

        @return Fehlertext oder None.
        """
        return self._worker.snapshot().get("solar_error")

    @property
    def mains_error(self):
        """@brief Gibt den letzten Verbindungsfehler des Netz-Sonoffs zurueck.

        @return Fehlertext oder None.
        """
        return self._worker.snapshot().get("mains_error")

    def has_status(self):
        """@brief Gibt an, ob mindestens ein vollstaendiger Sonoff-Poll vorliegt.

        Vorher sind Modus und Erreichbarkeit nur Platzhalter (UNKNOWN/None).
        @return True nach dem ersten abgeschlossenen Poll.
        """
        return self._worker.snapshot().get("updated_at") is not None

    def get_mode(self):
        """@brief Liefert den zuletzt beobachteten Versorgungsmodus ohne Netzwerkzugriff.

        @return Aktueller SupplyMode-Snapshot.
        """
        return SupplyMode(self._worker.snapshot().get("mode", SupplyMode.UNKNOWN.value))

    def set_mode(self, mode):
        """@brief Schaltet zwischen Solar, Netz und Aus um und prueft den Zielzustand.

        Beim Wechsel auf Solar bleibt das Netzrelais eingeschaltet, um eine Ladepause
        zu vermeiden.
        @param mode SupplyMode oder String solar, mains beziehungsweise off.
        @return Bestaetigter SupplyMode.
        @throws ValueError Bei einem nicht untersttzten Zielmodus.
        @throws TimeoutError oder RuntimeError Bei fehlender Geraetebesttigung.
        """
        if isinstance(mode, str):
            try:
                mode = SupplyMode(mode.lower())
            except ValueError:
                raise ValueError("Mode must be 'solar', 'mains' or 'off'")
        if mode not in (SupplyMode.SOLAR, SupplyMode.MAINS, SupplyMode.OFF):
            raise ValueError("Mode must be 'solar', 'mains' or 'off'")

        def apply_mode():
            """@brief Schaltet die Relais in sicherer Reihenfolge auf den Zielmodus.

            @return Angeforderter SupplyMode.
            """
            if mode == SupplyMode.SOLAR:
                # Keep the mains relay ON during transfer to avoid an interruption.
                self._worker.solar_switch.turn_on()
            elif mode == SupplyMode.MAINS:
                self._worker.mains_switch.turn_on()
                try:
                    self._worker.solar_switch.turn_off()
                except Exception as exc:
                    # Solar may lose power in inverter Power Save. Mains is already ON.
                    self._worker._log("Error", "Solar switch unavailable while selecting mains: {}".format(exc))
            else:
                self._worker.mains_switch.turn_off()
                try:
                    self._worker.solar_switch.turn_off()
                except Exception as exc:
                    self._worker._log("Error", "Solar switch could not be confirmed off: {}".format(exc))
            return mode

        def verify_mode(_):
            """@brief Prueft den nach dem Poll beobachteten Zielmodus.

            @param _ Ergebnis der vorherigen Relaisaktion.
            @return Bestaetigter SupplyMode.
            @throws RuntimeError Wenn der Zielmodus nicht erreicht wurde.
            """
            snapshot = self._worker.snapshot()
            actual = SupplyMode(snapshot["mode"])
            if mode == SupplyMode.MAINS and actual == SupplyMode.MAINS:
                result = actual
            elif mode == SupplyMode.OFF and (
                    actual == SupplyMode.OFF
                    or (actual == SupplyMode.UNKNOWN
                        and snapshot["solar_available"] is False
                        and snapshot["mains_available"] is True)):
                result = actual
            elif actual == mode:
                result = actual
            else:
                raise RuntimeError("Requested mode was not reached: {}".format(actual.value))
            self._worker._log("Info", "Supply mode changed to {}".format(mode.value))
            return result

        return self._worker.execute(apply_mode, verify_mode)

    def get_status(self):
        """@brief Gibt den letzten Worker-Snapshot ohne Netzwerk-Wartezeit zurueck.

        @return Kopiertes Status-Dictionary beider Sonoffs.
        """
        # The worker refreshes while charging; status reads must never wait for network I/O.
        return self._worker.snapshot()

    def close(self):
        """@brief Schliesst den Controller und seine Sonoff-Verbindungen.

        @return None
        """
        if self._closed:
            return
        self._closed = True
        self._worker.close()
