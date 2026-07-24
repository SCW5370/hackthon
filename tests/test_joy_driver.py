import unittest

from joy.driver import JoyDriver


class FakeDataExchange:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def get_keys(self) -> list[str]:
        return []

    def rpc(self, func_name: str, *args: object) -> object:
        self.calls.append((func_name, args))
        if func_name == "health":
            return {"ok": True, "arm_state": "IDLE"}
        if func_name == "inventory":
            return {"sample_locations": {"sample-A": "cold-storage"}}
        return {"ok": True, "accepted": True}


class StaleRpcDataExchange:
    def __init__(self) -> None:
        self.payload = {
            "ts": 1.0,
            "func_name": "health",
            "value": {"ok": True, "arm_state": "MOVING"},
        }
        self.pending = None

    def get_keys(self) -> list[str]:
        return ["rpc_result"]

    def get_data(self, key: str) -> object:
        self.assert_key(key)
        if self.pending is not None:
            self.payload = self.pending
            self.pending = None
        return self.payload

    def rpc(self, func_name: str, *args: object) -> object:
        del args
        old_value = self.payload["value"]
        self.pending = {
            "ts": 2.0,
            "func_name": func_name,
            "value": {"ok": True, "arm_state": "IDLE"},
        }
        return old_value

    @staticmethod
    def assert_key(key: str) -> None:
        if key != "rpc_result":
            raise AssertionError(key)


class JoyDriverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.exchange = FakeDataExchange()
        self.driver = JoyDriver(exchange=self.exchange).connect()

    def test_fake_exchange_covers_high_level_calls(self) -> None:
        self.assertEqual(self.driver.get_arm_state(), "IDLE")
        self.assertEqual(
            self.driver.get_inventory()["sample_locations"]["sample-A"],
            "cold-storage",
        )
        self.driver.pause()
        self.driver.resume()
        self.driver.reset()
        self.assertEqual(
            [name for name, _ in self.exchange.calls],
            ["health", "inventory", "pause", "resume", "reset"],
        )

    def test_transfer_sends_names_and_command_object_only(self) -> None:
        self.driver.transfer(
            "sample-A",
            "cold-storage",
            "waste-bin",
            command_id="cmd-legacy-001",
        )
        name, args = self.exchange.calls[-1]
        self.assertEqual(name, "transfer")
        self.assertEqual(
            args[0],
            {
                "command_id": "cmd-legacy-001",
                "action": "TRANSFER",
                "sample_id": "sample-A",
                "source": "cold-storage",
                "destination": "waste-bin",
            },
        )
        self.assertNotIn("coordinates", args[0])

    def test_invalid_names_never_reach_exchange(self) -> None:
        with self.assertRaises(ValueError):
            self.driver.transfer("sample-Z", "cold-storage", "waste-bin")
        self.assertEqual(self.exchange.calls, [])

    def test_pyjop_103_stale_rpc_result_is_polled(self) -> None:
        driver = JoyDriver(exchange=StaleRpcDataExchange()).connect()
        self.assertEqual(driver.get_arm_state(), "IDLE")


if __name__ == "__main__":
    unittest.main()
