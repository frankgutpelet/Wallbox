"""Shared test setup: project import path, optional requests stub and fakes."""
import os
import sys
import types

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

try:
    import requests  # noqa: F401
except ImportError:
    # Minimal stand-in so the tests also run on machines without requests.
    stub = types.ModuleType("requests")

    class RequestException(IOError):
        def __init__(self, *args, response=None, **kwargs):
            super().__init__(*args)
            self.response = response

    class ConnectionError(RequestException):
        pass

    class Timeout(RequestException):
        pass

    class ConnectTimeout(ConnectionError, Timeout):
        pass

    class ReadTimeout(Timeout):
        pass

    class HTTPError(RequestException):
        pass

    stub.RequestException = RequestException
    stub.ConnectionError = ConnectionError
    stub.Timeout = Timeout
    stub.ConnectTimeout = ConnectTimeout
    stub.ReadTimeout = ReadTimeout
    stub.HTTPError = HTTPError
    stub.Session = lambda: None
    sys.modules["requests"] = stub

import requests  # noqa: E402

from WallboxController import SupplyMode  # noqa: E402


class FakeLogger:
    def __init__(self):
        self.lines = []

    def Info(self, message):
        self.lines.append(("INFO", message))

    def Error(self, message):
        self.lines.append(("ERROR", message))

    def Debug(self, message):
        pass

    def errors(self, text=""):
        return [m for level, m in self.lines if level == "ERROR" and text in m]


class FakeWallbox:
    """Stands in for WallboxController; set_mode is applied immediately."""

    def __init__(self, mode=SupplyMode.OFF, solar_available=True, has_status=True):
        self.mode = mode
        self.solar_available = solar_available
        self._has_status = has_status
        self.calls = []

    def has_status(self):
        return self._has_status

    def get_mode(self):
        return self.mode

    def get_status(self):
        return {"mode": self.mode.value}

    def set_mode(self, mode):
        self.calls.append(mode)
        self.mode = mode
        return mode


class FakeEnergy:
    def __init__(self, soc):
        self.soc = soc
        self.error = None

    def get_values(self):
        if self.error:
            raise self.error
        return {"soc": self.soc}
