import tempfile
import unittest
from pathlib import Path
from boundedrsi.common import read_json, read_jsonl, safe_child, digest
from boundedrsi.generate import generate_dataset
from boundedrsi.surface import build_surface, verify_surface
from boundedrsi.retrieval import is_effective
from boundedrsi.executor import predict
from boundedrsi.scoring import score_prediction

ROOT = Path(__file__).resolve().parents[1]


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "data"
        self.manifest = generate_dataset(self.data)
        self.workspace = self.data / "workspaces/ws_01"
        self.tasks = read_jsonl(self.data / "tasks/ws_01.jsonl")
        self.gold = read_json(self.data / "gold/ws_01.json")

    def surface(self, name):
        return build_surface(self.workspace, read_json(ROOT / "policies" / (name + ".json")))

    def results(self, name):
        surface = self.surface(name)
        return [score_prediction(self.gold[t["id"]], predict(surface, t), surface["registry"]) for t in self.tasks]

    def test_dataset_is_reproducible_and_family_disjoint(self):
        other = Path(self.temp.name) / "other"
        generate_dataset(other)
        self.assertEqual(len(self.manifest["workspaces"]), 10)
        self.assertEqual(sum(w["task_count"] for w in self.manifest["workspaces"]), 120)
        self.assertEqual(len({w["family"] for w in self.manifest["workspaces"]}), 10)
        for path in self.data.rglob("*"):
            if path.is_file():
                self.assertEqual(path.read_bytes(), (other / path.relative_to(self.data)).read_bytes())

    def test_combined_solves_public_typed_tasks(self):
        results = self.results("combined")
        self.assertEqual([r["success"] for r in results], [1] * 12)

    def test_manual_patches_fix_distinct_diagnostic_faults(self):
        baseline, table, temporal, relation = [self.results(n) for n in ("baseline", "table_rows", "temporal", "relations")]
        self.assertEqual(baseline[2]["success"], 0)
        self.assertEqual(table[2]["success"], 1)
        self.assertEqual(baseline[4]["version_correct"], 0)
        self.assertEqual(temporal[4]["success"], 1)
        self.assertEqual(table[8]["success"], 0)
        self.assertEqual(relation[8]["success"], 1)

    def test_baseline_and_reference_are_not_misrepresented(self):
        self.assertEqual(self.results("structured_reference"), self.results("combined"))
        self.assertEqual(self.results("latest_only_control")[4]["success"], 0)

    def test_effective_end_is_exclusive(self):
        meta = {"valid_from": "2025-01-01", "valid_to": "2026-04-01"}
        self.assertTrue(is_effective(meta, "2026-03-31"))
        self.assertFalse(is_effective(meta, "2026-04-01"))

    def test_builder_does_not_consume_gold_or_tasks(self):
        before = self.surface("combined")
        (self.data / "gold/ws_01.json").write_text("INVALID")
        (self.data / "tasks/ws_01.jsonl").write_text("INVALID")
        self.assertEqual(digest(before), digest(self.surface("combined")))

    def test_source_change_invalidates_surface_and_evidence_ids(self):
        before = self.surface("combined")
        path = self.workspace / "documents/manual.md"
        path.write_text(path.read_text() + "new_field: 1\n")
        with self.assertRaisesRegex(ValueError, "Stale"):
            verify_surface(before, self.workspace)
        after = self.surface("combined")
        old_ids = {c["id"] for c in before["chunks"] if c["source"] == "manual.md"}
        new_ids = {c["id"] for c in after["chunks"] if c["source"] == "manual.md"}
        self.assertFalse(old_ids & new_ids)

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            safe_child(self.workspace, "../gold/ws_01.json")

    def test_relation_dependency_is_explicit(self):
        with self.assertRaises(ValueError):
            build_surface(self.workspace, {"name": "bad", "table_records": False, "relational_links": True})

    def test_numeric_zero_lookup(self):
        surface = self.surface("combined")
        task = dict(self.tasks[2])
        task["query"] = dict(task["query"], where={"department": "Orchid Admin"})
        task["question"] = "What is the budget for Orchid Admin?"
        prediction = predict(surface, task)
        self.assertEqual(prediction["answer"], 0)
        self.assertFalse(prediction["abstained"])
