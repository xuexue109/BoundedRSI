"""Evaluator-only gold access. Never imported by the surface builder or executor."""
import math


def answer_equal(expected, actual):
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(expected) is type(actual) and expected == actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isfinite(actual) and math.isclose(expected, actual, abs_tol=1e-9)
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.strip().casefold() == actual.strip().casefold()
    return type(expected) is type(actual) and expected == actual


def score_prediction(gold, prediction, registry):
    citations = set(prediction.get("citations", []))
    retrieved = set(prediction.get("retrieved", []))
    abstained = prediction.get("abstained") is True
    if gold["unanswerable"]:
        correct = int(abstained and prediction.get("answer") is None and not citations)
        return dict(answer_correct=correct, version_correct=None, evidence_correct=None,
                    success=correct, recall_at_k=None, any_hit_at_k=None,
                    complete_evidence_at_k=None, abstention_correct=correct)
    answer = int(not abstained and answer_equal(gold["answer"], prediction.get("answer")))
    alternatives = [set(items) for items in gold["evidence_sets"]]
    if not alternatives or any(not items for items in alternatives):
        raise ValueError("Answerable task requires non-empty gold evidence sets")
    known = bool(citations) and citations.issubset(registry) and citations.issubset(retrieved)
    evidence = int(known and any(citations == items for items in alternatives))
    sources = {registry[c]["source"] for c in citations if c in registry}
    version = int(known and sources == set(gold["sources"]))
    recall = max(len(items & retrieved) / len(items) for items in alternatives)
    return dict(answer_correct=answer, version_correct=version, evidence_correct=evidence,
                success=answer * version * evidence, recall_at_k=recall,
                any_hit_at_k=int(recall > 0), complete_evidence_at_k=int(recall == 1),
                abstention_correct=None)
