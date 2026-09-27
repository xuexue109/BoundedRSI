"""Fixed, typed diagnostic executor. Receives retrieved surface nodes, never gold.

This intentionally does not model an LLM's ability to infer structure from raw text.
Use exported contexts + external predictions for actual model evaluation.
"""
import time
from .retrieval import retrieve


def matches(fields, where):
    return all(str(fields.get(key, "")).casefold() == str(value).casefold() for key, value in where.items())


def execute(task, hits):
    query = task["query"]
    nodes = [h["chunk"] for h in hits]
    left = [c for c in nodes if c["metadata"]["collection"] == query["collection"]
            and matches(c["fields"], query.get("where", {}))]
    if query["op"] == "lookup":
        for chunk in left:
            if query["field"] in chunk["fields"]:
                return chunk["fields"][query["field"]], [chunk["id"]], "lookup"
        return None, [], "missing_matching_record_or_field"
    if query["op"] == "join_difference":
        for lnode in left:
            lf = lnode["fields"]
            if query["left_key"] not in lf or query["left_field"] not in lf:
                continue
            for rnode in nodes:
                rf = rnode["fields"]
                if (rnode["metadata"]["collection"] == query["right_collection"]
                        and query["right_key"] in rf and rf[query["right_key"]] == lf[query["left_key"]]
                        and query["right_field"] in rf):
                    a, b = lf[query["left_field"]], rf[query["right_field"]]
                    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                        return a - b, [lnode["id"], rnode["id"]], "join_difference"
        return None, [], "missing_join_partner_or_numeric_fields"
    raise ValueError("Unsupported public query operator: " + str(query["op"]))


def predict(surface, task, k=5):
    start = time.perf_counter()
    hits = retrieve(surface, task, k)
    answer, citations, reason = execute(task, hits)
    return dict(task_id=task["id"], answer=answer, abstained=answer is None,
                citations=citations, retrieved=[h["chunk"]["id"] for h in hits],
                reason=reason, engine="deterministic-diagnostic", latency_ms=(time.perf_counter()-start)*1000,
                retrieval_trace=[dict(id=h["chunk"]["id"], source=h["chunk"]["source"],
                                      locator=h["chunk"]["locator"], via=h["via"], score=h["score"]) for h in hits])
