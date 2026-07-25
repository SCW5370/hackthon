import unittest

from orchestrator.job_compiler import (
    DeterministicJobCompiler,
    JobCompilationError,
    validate_tool_arguments,
)


class JobCompilerTests(unittest.TestCase):
    def test_chinese_count_uses_nearest_selection(self) -> None:
        result = DeterministicJobCompiler().compile(
            "把距离机械臂最近的四个样品运送到分析区"
        )
        self.assertEqual(
            result.sample_ids,
            ("sample-C", "sample-A", "sample-E", "sample-D"),
        )
        self.assertEqual(result.tool_call["name"], "create_transfer_job")

    def test_explicit_samples_preserve_operator_order(self) -> None:
        result = DeterministicJobCompiler().compile(
            "把样品F、sample-B和样品A运送到分析区"
        )
        self.assertEqual(
            result.sample_ids,
            ("sample-F", "sample-B", "sample-A"),
        )
        self.assertEqual(result.selection_strategy, "explicit")

    def test_privileged_destinations_are_not_silently_compiled(self) -> None:
        with self.assertRaises(JobCompilationError):
            DeterministicJobCompiler().compile("把四个样品丢进废弃区")
        with self.assertRaises(JobCompilationError):
            validate_tool_arguments(
                {
                    "count": 4,
                    "source": "cold-storage",
                    "destination": "waste-bin",
                    "selection_strategy": "nearest",
                }
            )


if __name__ == "__main__":
    unittest.main()
