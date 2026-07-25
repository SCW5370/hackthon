import threading
import unittest
import uuid

from runtime.lease_authority import LeaseStore


class LeaseStoreThreadingTests(unittest.TestCase):
    def test_concurrent_guard_threads_consume_lease_exactly_once(self):
        store = LeaseStore(":memory:")
        lease_id = str(uuid.uuid4())
        barrier = threading.Barrier(8)
        results = []
        errors = []

        def consume():
            try:
                barrier.wait()
                results.append(store.try_consume(lease_id))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=consume) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        self.assertEqual(errors, [])
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 7)
        store.close()


if __name__ == "__main__":
    unittest.main()
