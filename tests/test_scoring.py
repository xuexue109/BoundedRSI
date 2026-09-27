import unittest
from boundedrsi.scoring import score_prediction


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.gold = {"answer": 500, "evidence_sets": [["a"]], "sources": ["p1.md"], "unanswerable": False}
        self.registry = {"a": {"source": "p1.md"}, "b": {"source": "p2.md"}}
        self.pred = {"answer": 500, "abstained": False, "citations": ["a"], "retrieved": ["a"]}

    def test_correct_answer_wrong_version_fails(self):
        self.pred.update(citations=["b"], retrieved=["b"])
        score = score_prediction(self.gold, self.pred, self.registry)
        self.assertEqual(score["answer_correct"], 1)
        self.assertEqual(score["version_correct"], 0)
        self.assertEqual(score["success"], 0)

    def test_forged_or_unretrieved_evidence_fails(self):
        for citations in [["fake"], ["a", "b"]]:
            self.pred["citations"] = citations
            self.assertEqual(score_prediction(self.gold, self.pred, self.registry)["success"], 0)
        self.pred.update(citations=["a"], retrieved=[])
        self.assertEqual(score_prediction(self.gold, self.pred, self.registry)["success"], 0)

    def test_join_requires_all_evidence(self):
        self.gold.update(evidence_sets=[["a", "b"]], sources=["p1.md", "p2.md"])
        score = score_prediction(self.gold, self.pred, self.registry)
        self.assertEqual(score["recall_at_k"], 0.5)
        self.assertEqual(score["complete_evidence_at_k"], 0)
        self.assertEqual(score["success"], 0)

    def test_abstention_has_not_applicable_metrics(self):
        self.gold.update(answer=None, unanswerable=True, evidence_sets=[], sources=[])
        self.pred.update(answer=None, abstained=True, citations=[])
        score = score_prediction(self.gold, self.pred, self.registry)
        self.assertEqual(score["success"], 1)
        self.assertIsNone(score["recall_at_k"])
        self.pred["answer"] = "invented"
        self.assertEqual(score_prediction(self.gold, self.pred, self.registry)["success"], 0)

    def test_numeric_zero_is_a_real_answer(self):
        self.gold["answer"] = 0
        self.pred["answer"] = 0
        self.assertEqual(score_prediction(self.gold, self.pred, self.registry)["success"], 1)
        self.pred["answer"] = False
        self.assertEqual(score_prediction(self.gold, self.pred, self.registry)["success"], 0)
