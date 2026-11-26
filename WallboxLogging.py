"""Small daily-file logger compatible with the OffgridControl Logging class."""
from datetime import datetime
from pathlib import Path
from threading import Lock


class Logging:
    LOGGLEVEL_NONE = 0
    LOGGLEVEL_ERROR = 1
    LOGGLEVEL_INFO = 2
    LOGGLEVEL_DEBUG = 3

    def __init__(self, logfile=None):
        """@brief Initialisiert einen thread-sicheren Logger mit taeglichen Logdateien.

        @param logfile Dateipraefix; das Tagesdatum und die Endung werden ergaenzt.
        """
        self.logfile = Path(logfile or "log/Wallbox_")
        self.loglevel = self.LOGGLEVEL_ERROR
        self.lock = Lock()

    def setLogLevel(self, loglevel):
        """@brief Setzt den Schwellwert fuer Logmeldungen.

        @param loglevel NONE, ERROR, INFO oder DEBUG. Unbekannte Werte fallen auf ERROR zurueck.
        @return None
        """
        levels = {
            "NONE": self.LOGGLEVEL_NONE,
            "ERROR": self.LOGGLEVEL_ERROR,
            "INFO": self.LOGGLEVEL_INFO,
            "DEBUG": self.LOGGLEVEL_DEBUG,
        }
        self.loglevel = levels.get(str(loglevel).upper(), self.LOGGLEVEL_ERROR)
        if str(loglevel).upper() not in levels:
            self.Error("Loglevel wrong: {}".format(loglevel))

    def _write(self, severity, message, level):
        """@brief Schreibt eine Meldung bei ausreichendem Loglevel in die Tagesdatei.

        @param severity Textuelle Stufe fuer die Logzeile.
        @param message Inhalt der Meldung.
        @param level Numerischer Schwellwert der Meldung.
        @return None
        """
        if level > self.loglevel:
            return
        now = datetime.now()
        path = self.logfile.parent / (self.logfile.name + now.strftime("%Y-%m-%d") + ".log")
        line = "{}: {} - {}\n".format(now.strftime("%H:%M:%S"), severity, str(message))
        with self.lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as output:
                output.write(line)

    def Debug(self, message):
        """@brief Protokolliert eine Debug-Meldung.

        @param message Zu protokollierender Text.
        @return None
        """
        self._write("DEBUG", message, self.LOGGLEVEL_DEBUG)

    def Info(self, message):
        """@brief Protokolliert eine Informationsmeldung.

        @param message Zu protokollierender Text.
        @return None
        """
        self._write("INFO ", message, self.LOGGLEVEL_INFO)

    def Error(self, message):
        """@brief Protokolliert einen Fehler.

        @param message Zu protokollierender Text.
        @return None
        """
        self._write("ERROR", message, self.LOGGLEVEL_ERROR)

    def Write(self, message):
        """@brief Schreibt eine normale Meldung mit STD-Kennzeichnung.

        @param message Zu protokollierender Text.
        @return None
        """
        self._write("STD  ", message, self.LOGGLEVEL_INFO)
