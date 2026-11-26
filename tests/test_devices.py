import itertools
import unittest

from support import FakeLogger, SupplyMode, requests

from HttpErrors import describe_request_error
from SonoffPowElite import SonoffCommunicationError, SonoffPowElite
from WallboxController import WallboxController

_addresses = itertools.count(0x7F8800000000)


def connect_timeout():
    # Real urllib3 texts contain a new object address on every attempt.
    return requests.ConnectTimeout(
        "HTTPConnectionPool(host='x'): (Caused by ConnectTimeoutError("
        "<urllib3.connection.HTTPConnection object at 0x%x>, 'timed out'))" % next(_addresses)
    )


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, fail=False, power="OFF"):
        self.fail = fail
        self.power = power

    def get(self, url, params=None, timeout=None):
        if self.fail:
            raise connect_timeout()
        command = params["cmnd"]
        if command.startswith("Power "):
            self.power = command.split()[1]
        if command == "Status 10":
            return FakeResponse({"StatusSNS": {"ENERGY": {k: 1 for k in (
                "Voltage", "Current", "Power", "ApparentPower", "ReactivePower",
                "Factor", "Today", "Yesterday", "Total")}}})
        return FakeResponse({"POWER": self.power})

    def close(self):
        pass


def make_switch(name, logger, **session_args):
    switch = SonoffPowElite(name, logger=logger)
    switch.session = FakeSession(**session_args)
    return switch


class HttpErrorsTest(unittest.TestCase):
    def test_text_is_stable_between_attempts(self):
        self.assertEqual(describe_request_error(connect_timeout()), "connect timeout")
        self.assertEqual(describe_request_error(connect_timeout()), "connect timeout")

    def test_object_addresses_are_removed(self):
        self.assertNotIn("0x", describe_request_error(ValueError("bad <obj at 0x1234>")))


class SonoffTest(unittest.TestCase):
    def test_communication_error_is_short(self):
        switch = make_switch("WallboxSwSolar", FakeLogger(), fail=True)
        with self.assertRaises(SonoffCommunicationError) as caught:
            switch.get_power_state()
        self.assertEqual(str(caught.exception), "wallboxswsolar 'Power': connect timeout")


class WallboxControllerTest(unittest.TestCase):
    def setUp(self):
        self.logger = FakeLogger()

    def make(self, solar_fail=False):
        solar = make_switch("WallboxSwSolar", self.logger, fail=solar_fail)
        mains = make_switch("WallboxSwMains", self.logger)
        controller = WallboxController(solar, mains, logger=self.logger)
        self.addCleanup(controller.close)
        return controller

    def test_unreachable_solar_is_normal_and_logged_once_as_info(self):
        controller = self.make(solar_fail=True)
        self.assertFalse(controller.has_status())
        controller.start()
        for _ in range(4):
            controller._worker.refresh()
        self.assertTrue(controller.has_status())
        self.assertEqual(self.logger.errors(), [])
        offline = [m for level, m in self.logger.lines if level == "INFO" and "solar Sonoff offline" in m]
        self.assertEqual(len(offline), 1)

    def test_set_mode_mains_with_unreachable_solar(self):
        controller = self.make(solar_fail=True)
        controller.start()
        self.assertEqual(controller.set_mode("mains"), SupplyMode.MAINS)
        self.assertEqual(controller.get_mode(), SupplyMode.MAINS)

    def test_set_mode_cycle(self):
        controller = self.make()
        controller.start()
        self.assertEqual(controller.set_mode("solar"), SupplyMode.SOLAR)
        self.assertEqual(controller.set_mode("mains"), SupplyMode.MAINS)
        self.assertEqual(controller.set_mode("off"), SupplyMode.OFF)

    def test_repeated_command_failure_logged_once(self):
        controller = self.make()
        controller.start()
        controller.mains_switch.session.fail = True
        for _ in range(3):
            with self.assertRaises(Exception):
                controller.set_mode("mains")
        self.assertEqual(len(self.logger.errors("worker operation failed")), 1)


if __name__ == "__main__":
    unittest.main()
