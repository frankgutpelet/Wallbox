"""Read live energy values from the parallel OffgridControl FastAPI service."""
import requests

from HttpErrors import describe_request_error


class OffgridCommunicationError(RuntimeError):
    """@brief OffgridControl lieferte keine verwertbaren Wechselrichterwerte.

    Der Text ist zwischen Wiederholungen stabil (ohne Objektadressen).
    """


class OffgridEnergyClient:
    def __init__(self, base_url="http://127.0.0.1:8000", timeout=2.0, logger=None):
        """@brief Erstellt einen HTTP-Client fuer die OffgridControl-API.

        @param base_url Basisadresse der API.
        @param timeout HTTP-Timeout in Sekunden.
        @param logger Optionaler Logger (nur DEBUG; Fehler loggt der Aufrufer).
        """
        self.logger = logger
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def get_values(self):
        """@brief Liest SOC, Solarleistung, AC-Ausgang und Batterieladestrom.

        Victron-Messwerte werden ergaenzend beruecksichtigt, wenn sie verfuegbar sind.
        @return Dictionary mit SOC, Batterie-/Solarleistung, AC-Ausgang und ueberschuss.
        @throws OffgridCommunicationError Wenn die erforderlichen Wechselrichterwerte fehlen.
        """
        try:
            inverter_response = self.session.get(
                self.base_url + "/api/inverterValues", timeout=self.timeout
            )
            inverter_response.raise_for_status()
            inverter = inverter_response.json()["values"]
            soc = float(inverter["SOC"])
            ac_output = float(inverter["InverterOutputPower"])
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            if self.logger:
                self.logger.Debug("OffgridControl energy request failed: {}".format(exc))
            if isinstance(exc, (KeyError, TypeError)):
                reason = "missing inverter value {}".format(exc)
            else:
                reason = describe_request_error(exc)
            raise OffgridCommunicationError("OffgridControl: {}".format(reason)) from exc

        # Victron is optional; Growatt's two PV inputs remain usable without it.
        victron_power = 0.0
        victron_charging_current = 0.0
        try:
            victron_response = self.session.get(
                self.base_url + "/api/victronValues", timeout=self.timeout
            )
            victron_response.raise_for_status()
            victron_data = victron_response.json()
            victron_power = float(victron_data.get("total_solar_power", 0) or 0)
            victron_charging_current = sum(
                float(charger.get("charging_current", 0) or 0)
                for charger in victron_data.get("chargers", [])
                if charger.get("online")
            )
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            pass

        solar_total = (
            float(inverter.get("PowerSolar1", 0) or 0)
            + float(inverter.get("PowerSolar2", 0) or 0)
            + victron_power
        )
        battery_current = float(inverter.get("BatteryCurrent", 0) or 0) + victron_charging_current
        return {
            "soc": soc,
            "battery_current": battery_current,
            "solar_power": solar_total,
            "ac_output_power": ac_output,
            "surplus_power": solar_total - ac_output,
        }

    def close(self):
        """@brief Schliesst die wiederverwendete HTTP-Session.

        @return None
        """
        self.session.close()
