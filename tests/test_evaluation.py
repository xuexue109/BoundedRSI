import tempfile
import unittest
from pathlib import Path
from boundedrsi.common import read_json, read_jsonl, write_jsonl
from boundedrsi.generate import generate_dataset
from boundedrsi.evaluation import evaluate, compare_runs, select_workspaces, context_rows

ROOT = Path(__file__).resolve().parents[1]


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        generate_dataset(self.data)

    def run_policy(self, policy, name=None, **kwargs):
        out = self.root / (name or policy)
        evaluate(self.data, ROOT/"policies"/(policy+".json"), out, **kwargs)
        return out

    def test_default_run_leaves_holdout_unevaluated(self):
        out = self.run_policy("combined")
        scores = read_jsonl(out/"scores.jsonl")
        self.assertEqual(len(scores), 96)
        self.assertEqual({s["split"] for s in scores}, {"development", "validation"})
        self.assertFalse(read_json(out/"run.json")["holdout_access"])
        self.assertFalse((out/"surfaces/ws_09.json").exists())

    def test_holdout_requires_explicit_opt_in_and_cannot_gate(self):
        with self.assertRaisesRegex(ValueError, "holdout"):
            select_workspaces(self.data, ["locked_holdout"])
        a = self.run_policy("baseline", splits=["locked_holdout"], allow_holdout=True)
        b = self.run_policy("combined", splits=["locked_holdout"], allow_holdout=True)
        report = compare_runs(a, b)
        self.assertFalse(report["gate"]["accepted"])
        self.assertTrue(any("Holdout" in reason for reason in report["gate"]["reasons"]))

    def test_gate_rejects_historical_regression(self):
        baseline = self.run_policy("combined")
        candidate = self.run_policy("latest_only_control")
        comparison = compare_runs(baseline, candidate)
        self.assertEqual(len(comparison["regressions"]), 16)
        self.assertFalse(comparison["gate"]["accepted"])
        self.assertLess(comparison["overall_delta"], 0)

    def test_mismatched_evaluation_budgets_cannot_compare(self):
        a = self.run_policy("combined", k=5)
        b = self.run_policy("baseline", k=1)
        with self.assertRaisesRegex(ValueError, "different k"):
            compare_runs(a, b)

    def test_duplicate_or_incomplete_external_predictions_fail(self):
        path = self.root/"external.jsonl"
        write_jsonl(path, [dict(task_id="ws_01_q01", answer=None, abstained=True, citations=[])])
        with self.assertRaisesRegex(ValueError, "exactly once"):
            self.run_policy("combined", predictions_path=path)

    def test_external_predictions_round_trip_and_context_retrieval_is_authoritative(self):
        a = self.run_policy("combined")
        predictions = read_jsonl(a/"predictions.jsonl")
        # The evaluator recomputes retrieval; supplied retrieved fields have no authority.
        for p in predictions:
            p["retrieved"] = ["invented"]
        path = self.root/"external.jsonl"
        write_jsonl(path, predictions)
        b = self.run_policy("combined", "external", predictions_path=path)
        result = read_json(b/"summary.json")
        self.assertEqual(result["overall"]["success"], 1)
        self.assertIsNone(result["overall"]["query_latency_ms_p50"])
        self.assertEqual(read_json(b/"run.json")["engine"], "external-predictions")

    def test_context_export_does_not_open_gold(self):
        for path in (self.data/"gold").glob("*.json"):
            path.write_text("invalid")
        entries = select_workspaces(self.data, ["validation"])
        contexts = list(context_rows(self.data, read_json(ROOT/"policies/combined.json"), entries, 5))
        self.assertEqual(len(contexts), 24)
        self.assertNotIn("answer", contexts[0])
        self.assertNotIn("query", contexts[0])

    def test_artifacts_are_not_silently_overwritten(self):
        self.run_policy("combined")
        with self.assertRaisesRegex(ValueError, "nonempty"):
            self.run_policy("combined")

    def test_comparison_checks_gold_fingerprint(self):
        a = self.run_policy("combined")
        path = self.data/"gold/ws_01.json"
        data = read_json(path)
        data["ws_01_q01"]["answer"] = "changed"
        import json
        path.write_text(json.dumps(data))
        b = self.run_policy("combined", "changed_data")
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            compare_runs(a, b)
