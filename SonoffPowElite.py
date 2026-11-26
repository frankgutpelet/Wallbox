"""HTTP client for a Tasmota-flashed Sonoff POW Elite."""
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlsplit

import requests

from HttpErrors import describe_request_error


class SonoffCommunicationError(RuntimeError):
    """@brief Ein Tasmota-Befehl konnte nicht ausgefuehrt werden.

    Der Text ist zwischen Wiederholungen stabil (ohne Objektadressen).
    """


class SwitchState(Enum):
    ON = "ON"
    OFF = "OFF"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class EnergyValues:
    voltage: float
    current: float
    active_power: float
    apparent_power: float
    reactive_power: float
    power_factor: float
    energy_today: float
    energy_yesterday: float
    energy_total: float


class SonoffPowElite:
    """Control a POW Elite through Tasmota's local HTTP command endpoint.

    ``host`` accepts an IP address or DNS name, optionally with a scheme
    (http/https) and port. Tasmota's web server must be enabled.
    """

    def __init__(self, host: str, timeout: float = 1.5, logger=None):
        """@brief Erstellt einen Client fuer ein Tasmota-Sonoff POW Elite.

        @param host IP-Adresse oder DNS-Name, optional mit Schema und Port.
        @param timeout HTTP-Timeout in Sekunden.
        @param logger Optionaler Fehlerlogger.
        @throws ValueError Bei einer ungueltigen Geraeteadresse.
        """
        if not host or not host.strip():
            raise ValueError("host must be an IP address or DNS name")

        host = host.strip()
        if "://" not in host:
            host = f"http://{host}"
        parsed = urlsplit(host)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("host must be a valid IP address or DNS name")

        self.base_url = f"{parsed.scheme}://{parsed.netloc}"
        self.hostname = parsed.hostname
        self.timeout = timeout
        self.logger = logger
        self.session = requests.Session()

    def _command(self, command: str) -> dict[str, Any]:
        """@brief Sendet einen Tasmota-Befehl ueber den lokalen HTTP-Endpunkt.

        @param command Tasmota-Kommando.
        Fehler werden hier nur auf DEBUG-Level protokolliert; der Aufrufer entscheidet
        ueber ERROR-Meldungen, damit wiederholte Ausfaelle nicht doppelt geloggt werden.
        @param command Tasmota-Kommando.
        @return Dekodierte JSON-Antwort als Dictionary.
        @throws SonoffCommunicationError Bei HTTP-, Verbindungs- oder Antwortfehlern.
        """
        try:
            response = self.session.get(
                f"{self.base_url}/cm", params={"cmnd": command}, timeout=self.timeout
            )
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            if self.logger:
                self.logger.Debug("Tasmota {} command on {} failed: {}".format(command, self.hostname, exc))
            raise SonoffCommunicationError(
                "{} '{}': {}".format(self.hostname, command, describe_request_error(exc))
            ) from exc
        if not isinstance(result, dict):
            raise SonoffCommunicationError(
                "{} '{}': unexpected response from Tasmota".format(self.hostname, command)
            )
        return result

    def turn_on(self) -> SwitchState:
        """@brief Schaltet das Relais ein.

        @return Bestaetigter Zustand oder UNKNOWN.
        """
        return self._set_power("ON")

    def turn_off(self) -> SwitchState:
        """@brief Schaltet das Relais aus.

        @return Bestaetigter Zustand oder UNKNOWN.
        """
        return self._set_power("OFF")

    def _set_power(self, requested: str) -> SwitchState:
        """@brief Setzt die gewuenschte Relaisstellung und wertet die Antwort aus.

        @param requested Gewuenschte Stellung ON oder OFF.
        @return Bestaetigter SwitchState oder UNKNOWN.
        """
        result = self._command(f"Power {requested}")
        state = str(result.get("POWER", "")).upper()
        if state == requested:
            return SwitchState[state]
        return SwitchState.UNKNOWN

    def get_power_state(self) -> SwitchState:
        """@brief Fragt den momentanen Tasmota-Relaiszustand ab.

        @return ON, OFF oder UNKNOWN bei nicht lesbarer Antwort.
        """
        result = self._command("Power")
        state = str(result.get("POWER", "")).upper()
        if state in ("ON", "OFF"):
            return SwitchState[state]
        return SwitchState.UNKNOWN

    def get_energy_values(self) -> EnergyValues:
        """@brief Liest elektrische Messwerte und Energiezaehler des POW Elite.

        @return Struktur mit Spannung, Strom, Leistung, Leistungsfaktor und Zaehlern.
        @throws ValueError Wenn die Tasmota-Energiedaten fehlen oder ungueltig sind.
        """
        result = self._command("Status 10")
        try:
            energy = result["StatusSNS"]["ENERGY"]
        except (KeyError, TypeError) as exc:
            raise ValueError("Tasmota response does not contain StatusSNS.ENERGY") from exc

        def number(key: str) -> float:
            """@brief Liest einen numerischen Wert aus Tasmotas ENERGY-Antwort.

            @param key Tasmota-Feldname.
            @return Feldwert als Flieskommazahl.
            @throws ValueError Wenn das Feld fehlt oder nicht numerisch ist.
            """
            try:
                return float(energy[key])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Missing or invalid ENERGY.{key}") from exc

        return EnergyValues(
            voltage=number("Voltage"),
            current=number("Current"),
            active_power=number("Power"),
            apparent_power=number("ApparentPower"),
            reactive_power=number("ReactivePower"),
            power_factor=number("Factor"),
            energy_today=number("Today"),
            energy_yesterday=number("Yesterday"),
            energy_total=number("Total"),
        )

    def close(self) -> None:
        """@brief Schliesst die HTTP-Session zum Sonoff.

        @return None
        """
        self.session.close()
