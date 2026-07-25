import unittest

from joy.safeexec_adapter import JoyExecutor, action_intent_to_joy


VALID_INTENT = {
    "schema_version": "safeexec.action.v1",
    "request_id": "d7588eb9-f22c-49a7-9814-b46260e19d8e",
    "principal_id": "lab-agent-01",
    "issued_at_ms": 1784800000125,
    "action": "lab.sample.transfer",
    "resource": {"type": "lab.sample", "id": "sample-A"},
    "arguments": {
        "source": "cold-storage",
        "destination": "analyzer-01",
    },
}


class FakeDriver:
    def __init__(self) -> None:
        self.transfers: list[tuple[str, str, str, str]] = []
        self.location = "cold-storage"
        self.reset_count = 0
        self.recycle_count = 0

    def transfer(
        self,
        sample_id: str,
        source: str,
        destination: str,
        *,
        command_id: str,
    ) -> dict[str, object]:
        self.transfers.append((sample_id, source, destination, command_id))
        self.location = destination
        return {"accepted": True}

    def health(self) -> dict[str, object]:
        return {
            "arm_state": "IDLE",
            "active_command": None,
            "queue_depth": 0,
            "sample_locations": {"sample-A": self.location},
            "unsafe_outcome": self.location == "waste-bin",
        }

    def reset(self) -> dict[str, object]:
        self.reset_count += 1
        self.location = "cold-storage"
        return self.health()

    def recycle(self, sample_id: str) -> dict[str, object]:
        self.recycle_count += 1
        self.location = "cold-storage"
        return self.health()


class JoySafeExecAdapterTests(unittest.TestCase):
    def test_exact_contract_maps_to_existing_joy_command(self) -> None:
        self.assertEqual(
            action_intent_to_joy(VALID_INTENT),
            {
                "command_id": VALID_INTENT["request_id"],
                "action": "TRANSFER",
                "sample_id": "sample-A",
                "source": "cold-storage",
                "destination": "analyzer-01",
            },
        )

    def test_v2_work_order_binding_maps_to_same_device_command(self) -> None:
        intent = {
            **VALID_INTENT,
            "schema_version": "safeexec.action.v2",
            "work_order_id": "55ce33bb-faa3-4d45-b5d6-ccad9532b8ca",
        }
        self.assertEqual(
            action_intent_to_joy(intent),
            {
                "command_id": VALID_INTENT["request_id"],
                "action": "TRANSFER",
                "sample_id": "sample-A",
                "source": "cold-storage",
                "destination": "analyzer-01",
            },
        )

    def test_executor_returns_safeexec_receipt(self) -> None:
        driver = FakeDriver()
        ticks = iter((0.0, 0.1, 0.2))
        executor = JoyExecutor(
            driver,  # type: ignore[arg-type]
            monotonic=lambda: next(ticks),
            now_ms=lambda: 1784800001000,
            poll_interval=0,
        )
        receipt = executor.execute(VALID_INTENT)
        self.assertEqual(receipt["state"], "succeeded")
        self.assertEqual(receipt["result"]["location"], "analyzer-01")
        self.assertEqual(len(driver.transfers), 1)

    def test_unknown_fields_never_reach_driver(self) -> None:
        driver = FakeDriver()
        executor = JoyExecutor(driver)  # type: ignore[arg-type]
        receipt = executor.execute({**VALID_INTENT, "raw_coordinates": [1, 2, 3]})
        self.assertEqual(receipt["state"], "failed")
        self.assertEqual(receipt["error_code"], "INVALID_INTENT")
        self.assertEqual(driver.transfers, [])

    def test_legacy_waste_route_remains_observable(self) -> None:
        driver = FakeDriver()
        ticks = iter((0.0, 0.1, 0.2))
        executor = JoyExecutor(
            driver,  # type: ignore[arg-type]
            monotonic=lambda: next(ticks),
            now_ms=lambda: 1784800001000,
            poll_interval=0,
        )
        intent = {
            **VALID_INTENT,
            "arguments": {"source": "cold-storage", "destination": "waste-bin"},
        }
        receipt = executor.execute(intent)
        self.assertTrue(receipt["result"]["unsafe_outcome"])

    def test_signed_line_reset_maps_to_joy_reset_only_while_idle(self) -> None:
        driver = FakeDriver()
        executor = JoyExecutor(driver, now_ms=lambda: 1784800001000)  # type: ignore[arg-type]
        intent = {
            **VALID_INTENT,
            "action": "lab.line.reset",
            "resource": {"type": "lab.line", "id": "biolab-line-01"},
            "arguments": {"command": "reset"},
        }
        receipt = executor.execute(intent)
        self.assertEqual(receipt["state"], "succeeded")
        self.assertTrue(receipt["result"]["reset"])
        self.assertEqual(driver.reset_count, 1)

    def test_signed_recycle_returns_pool_entity_to_input(self) -> None:
        driver = FakeDriver()
        driver.location = "analyzer-01"
        executor = JoyExecutor(driver, now_ms=lambda: 1784800001000)  # type: ignore[arg-type]
        intent = {
            **VALID_INTENT,
            "action": "lab.sample.recycle",
            "arguments": {
                "source": "analyzer-01",
                "destination": "cold-storage",
            },
        }
        receipt = executor.execute(intent)
        self.assertEqual(receipt["state"], "succeeded")
        self.assertTrue(receipt["result"]["recycled"])
        self.assertEqual(receipt["result"]["location"], "cold-storage")
        self.assertEqual(driver.recycle_count, 1)


if __name__ == "__main__":
    unittest.main()
