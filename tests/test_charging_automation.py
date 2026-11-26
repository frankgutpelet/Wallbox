import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta

from support import FakeEnergy, FakeLogger, FakeWallbox, SupplyMode

import ChargingAutomation as CA

NOW = datetime.now().astimezone().replace(microsecond=0)


def deadline(ready_at, minutes=60):
    return {"charge_mode": "deadline", "ready_at": ready_at.isoformat(), "full_charge_minutes": minutes}


class AutomationTestCase(unittest.TestCase):
    def make(self, settings, wallbox, soc, now=NOW):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        automation = CA.ChargingAutomation(
            wallbox, FakeEnergy(soc),
            settings_path=os.path.join(self.tmp.name, "settings.json"),
            logger=FakeLogger(),
        )
        automation.settings.update(settings)
        automation._now = staticmethod(lambda: now)
        return automation


class DeadlineModeTest(AutomationTestCase):
    def test_forced_window_starts_from_unknown_state(self):
        wallbox = FakeWallbox(SupplyMode.UNKNOWN, solar_available=False)
        self.make(deadline(NOW + timedelta(minutes=30)), wallbox, 80).tick()
        self.assertEqual(wallbox.calls, [SupplyMode.MAINS])

    def test_forced_window_prefers_reachable_solar(self):
        wallbox = FakeWallbox(SupplyMode.OFF)
        self.make(deadline(NOW + timedelta(minutes=30)), wallbox, 50).tick()
        self.assertEqual(wallbox.calls, [SupplyMode.SOLAR])

    def test_mains_session_ends_at_ready_time(self):
        wallbox = FakeWallbox(SupplyMode.MAINS)
        automation = self.make(deadline(NOW - timedelta(minutes=1)), wallbox, 50)
        automation.session_started_at = NOW.isoformat()
        automation.tick()
        self.assertEqual(wallbox.calls, [SupplyMode.OFF])
        self.assertIsNone(automation.session_started_at)

    def test_expired_deadline_does_not_restart_on_mains(self):
        wallbox = FakeWallbox(SupplyMode.OFF)
        self.make(deadline(NOW - timedelta(hours=5)), wallbox, 50).tick()
        self.assertEqual(wallbox.calls, [])

    def test_expired_deadline_still_allows_surplus_solar(self):
        wallbox = FakeWallbox(SupplyMode.OFF)
        self.make(deadline(NOW - timedelta(hours=5)), wallbox, 99).tick()
        self.assertEqual(wallbox.calls, [SupplyMode.SOLAR])

    def test_before_window_mains_is_stopped(self):
        wallbox = FakeWallbox(SupplyMode.MAINS)
        self.make(deadline(NOW + timedelta(hours=5)), wallbox, 50).tick()
        self.assertEqual(wallbox.calls, [SupplyMode.OFF])

    def test_battery_floor_before_window_stops(self):
        wallbox = FakeWallbox(SupplyMode.SOLAR)
        self.make(deadline(NOW + timedelta(hours=5)), wallbox, 20).tick()
        self.assertEqual(wallbox.calls, [SupplyMode.OFF])

    def test_battery_floor_in_window_switches_to_mains(self):
        wallbox = FakeWallbox(SupplyMode.SOLAR)
        self.make(deadline(NOW + timedelta(minutes=30)), wallbox, 20).tick()
        self.assertEqual(wallbox.calls, [SupplyMode.MAINS])

    def test_past_ready_time_is_rejected(self):
        automation = self.make({}, FakeWallbox(), 50)
        with self.assertRaises(ValueError):
            automation.update_settings(deadline(NOW - timedelta(minutes=1)))

    def test_remaining_seconds_only_in_deadline_mode(self):
        automation = self.make(deadline(NOW + timedelta(hours=1), 120), FakeWallbox(SupplyMode.MAINS), 50)
        automation.session_started_at = (NOW - timedelta(minutes=30)).isoformat()
        self.assertAlmostEqual(automation.get_status()["remaining_seconds"], 90 * 60, delta=5)
        automation.settings["charge_mode"] = "instant"
        self.assertIsNone(automation.get_status()["remaining_seconds"])


class SolarModeTest(AutomationTestCase):
    def test_starts_at_threshold_plus_offset(self):
        wallbox = FakeWallbox(SupplyMode.OFF)
        self.make({"charge_mode": "solar", "solar_start_soc": 95}, wallbox, 99).tick()
        self.assertEqual(wallbox.calls, [SupplyMode.SOLAR])

    def test_does_not_start_below_threshold(self):
        wallbox = FakeWallbox(SupplyMode.OFF)
        self.make({"charge_mode": "solar", "solar_start_soc": 95}, wallbox, 98).tick()
        self.assertEqual(wallbox.calls, [])

    def test_does_not_start_when_solar_switch_unreachable(self):
        wallbox = FakeWallbox(SupplyMode.UNKNOWN, solar_available=False)
        self.make({"charge_mode": "solar"}, wallbox, 100).tick()
        self.assertEqual(wallbox.calls, [])

    def test_stops_with_hysteresis(self):
        wallbox = FakeWallbox(SupplyMode.SOLAR)
        automation = self.make({"charge_mode": "solar", "solar_start_soc": 95}, wallbox, 95)
        automation.tick()
        self.assertEqual(wallbox.calls, [])
        automation.energy_client.soc = 94
        automation.tick()
        self.assertEqual(wallbox.calls, [SupplyMode.OFF])


class InstantModeTest(AutomationTestCase):
    def test_unreachable_solar_uses_mains(self):
        wallbox = FakeWallbox(SupplyMode.OFF, solar_available=False)
        self.make({"charge_mode": "instant", "instant_soc_threshold": 30}, wallbox, 90).tick()
        self.assertEqual(wallbox.calls, [SupplyMode.MAINS])

    def test_switches_back_to_solar_with_hysteresis(self):
        wallbox = FakeWallbox(SupplyMode.MAINS)
        automation = self.make({"charge_mode": "instant", "instant_soc_threshold": 50}, wallbox, 54)
        automation.tick()
        self.assertEqual(wallbox.calls, [])
        automation.energy_client.soc = 55
        automation.tick()
        self.assertEqual(wallbox.calls, [SupplyMode.SOLAR])

    def test_waits_for_first_sonoff_poll(self):
        wallbox = FakeWallbox(SupplyMode.UNKNOWN, solar_available=None, has_status=False)
        self.make({"charge_mode": "instant"}, wallbox, 80).tick()
        self.assertEqual(wallbox.calls, [])


class SettingsUpdateTest(AutomationTestCase):
    def test_mode_change_keeps_charging(self):
        wallbox = FakeWallbox(SupplyMode.SOLAR)
        automation = self.make({"charge_mode": "solar"}, wallbox, 97)
        automation.update_settings({"charge_mode": "instant", "instant_soc_threshold": 50})
        self.assertNotIn(SupplyMode.OFF, wallbox.calls)
        self.assertEqual(wallbox.mode, SupplyMode.SOLAR)

    def test_mode_change_applies_immediately(self):
        wallbox = FakeWallbox(SupplyMode.MAINS)
        automation = self.make({"charge_mode": "instant"}, wallbox, 40)
        automation.update_settings({"charge_mode": "solar"})
        self.assertEqual(wallbox.calls, [SupplyMode.OFF])

    def test_threshold_change_reroutes_source(self):
        wallbox = FakeWallbox(SupplyMode.SOLAR)
        automation = self.make({"charge_mode": "instant", "instant_soc_threshold": 30}, wallbox, 50)
        automation.update_settings({"instant_soc_threshold": 60})
        self.assertEqual(wallbox.calls, [SupplyMode.MAINS])

    def test_unknown_fields_are_ignored(self):
        automation = self.make({}, FakeWallbox(), 50)
        saved = automation.update_settings({"charge_mode": "instant", "evil": 1, "paused": True})
        self.assertNotIn("evil", saved)
        self.assertNotIn("paused", saved)

    def test_invalid_values_rejected(self):
        automation = self.make({}, FakeWallbox(), 50)
        for bad in ({"charge_mode": "x"}, {"car_current_a": 7}, {"solar_start_soc": 99},
                    {"instant_soc_threshold": 10}, {"ready_at": "morgen"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                automation.update_settings(bad)

    def test_tick_failure_after_save_does_not_fail_save(self):
        automation = self.make({}, FakeWallbox(), 50)
        automation.energy_client.error = RuntimeError("OffgridControl: connect timeout")
        saved = automation.update_settings({"charge_mode": "instant"})
        self.assertEqual(saved["charge_mode"], "instant")
        self.assertEqual(automation.last_error, "OffgridControl: connect timeout")


class StatusTest(AutomationTestCase):
    def test_unreachable_solar_meter_is_not_an_error(self):
        wallbox = FakeWallbox(SupplyMode.MAINS, solar_available=False)
        wallbox.get_status = lambda: {"mode": "mains", "solar_error": "wallboxswsolar 'Power': connect timeout"}
        automation = self.make({"charge_mode": "instant"}, wallbox, 15)
        automation.tick()
        status = automation.get_status()
        self.assertIsNone(status["last_error"])
        self.assertIsNone(status["wallbox_error"])

    def test_unreachable_mains_meter_is_reported(self):
        wallbox = FakeWallbox(SupplyMode.OFF)
        wallbox.get_status = lambda: {"mode": "off", "mains_error": "wallboxswmains 'Power': connect timeout"}
        status = self.make({}, wallbox, 50).get_status()
        self.assertEqual(status["wallbox_error"], "wallboxswmains 'Power': connect timeout")


class PersistenceAndLoggingTest(AutomationTestCase):
    def test_legacy_keys_are_dropped(self):
        automation = self.make({}, FakeWallbox(), 50)
        with open(automation.settings_path, "w", encoding="utf-8") as handle:
            json.dump({"charge_mode": "soc", "paused": True, "manual_override": True,
                       "session_started_at": "2026-01-01T10:00:00+01:00"}, handle)
        settings, started = automation._load_settings()
        self.assertEqual(settings["charge_mode"], "solar")
        self.assertNotIn("paused", settings)
        self.assertEqual(started, "2026-01-01T10:00:00+01:00")
        automation._persist()
        with open(automation.settings_path, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertNotIn("paused", data)
        self.assertNotIn("manual_override", data)

    def test_repeated_tick_error_logged_once(self):
        automation = self.make({"charge_mode": "solar"}, FakeWallbox(), 50)
        automation.energy_client.error = RuntimeError("OffgridControl: connect timeout")
        for _ in range(5):
            try:
                automation.tick()
                automation._record_tick_result()
            except Exception as exc:
                automation._record_tick_result(exc)
        automation.energy_client.error = None
        automation.tick()
        automation._record_tick_result()
        self.assertEqual(len(automation.logger.errors()), 1)
        self.assertIn(("INFO", "Charging automation recovered"), automation.logger.lines)


if __name__ == "__main__":
    unittest.main()
