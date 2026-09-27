"""Serializable BM25 index, effective-time filtering, bounded relation expansion."""
import math
import re
from collections import Counter, deque
from datetime import date


def tokens(text):
    return re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", str(text).lower())


def make_index(chunks):
    docs, df = {}, Counter()
    for chunk in chunks:
        terms = Counter(tokens(chunk["text"]))
        docs[chunk["id"]] = dict(terms=dict(terms), length=sum(terms.values()))
        df.update(terms.keys())
    return dict(documents=docs, df=dict(df), count=len(docs),
                average_length=sum(d["length"] for d in docs.values()) / max(1, len(docs)))


def is_effective(metadata, as_of):
    if not as_of:
        return True
    at = date.fromisoformat(as_of)
    start, end = metadata.get("valid_from"), metadata.get("valid_to")
    return (not start or date.fromisoformat(start) <= at) and (not end or at < date.fromisoformat(end))


def bm25(index, chunk_id, query):
    doc = index["documents"][chunk_id]
    score = 0.0
    avg = index["average_length"] or 1
    for term in set(tokens(query)):
        tf = doc["terms"].get(term, 0)
        if not tf:
            continue
        df = index["df"].get(term, 0)
        idf = math.log(1 + (index["count"] - df + 0.5) / (df + 0.5))
        score += idf * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * doc["length"] / avg))
    return score


def retrieve(surface, task, k=5):
    if k < 1:
        raise ValueError("k must be positive")
    query = task.get("query", {})
    as_of = query.get("as_of")
    if as_of:
        date.fromisoformat(as_of)
    nodes = {c["id"]: c for c in surface["chunks"]}
    ranked = []
    for chunk in nodes.values():
        meta = chunk["metadata"]
        if query.get("collection") and meta["collection"] != query["collection"]:
            continue
        if not is_effective(meta, as_of):
            continue
        score = bm25(surface["index"], chunk["id"], task["question"])
        ranked.append((chunk, score))
    # Source authority is public catalog metadata, fixed across every policy.
    ranked.sort(key=lambda item: (float(item[0]["metadata"].get("authority", 0)),
                                 item[1], item[0]["metadata"].get("valid_from", ""),
                                 item[0]["metadata"].get("version", "")), reverse=True)
    chosen, seen = [], set()
    for root, score in ranked:
        queue = deque([(root, "lexical", score)])
        while queue and len(chosen) < k:
            chunk, via, rank_score = queue.popleft()
            if chunk["id"] in seen or not is_effective(chunk["metadata"], as_of):
                continue
            seen.add(chunk["id"])
            chosen.append(dict(chunk=chunk, via=via, score=round(rank_score, 6)))
            for link in chunk["links"]:
                if link["target"] in nodes:
                    queue.append((nodes[link["target"]], "relation:" + link["key"], 0.0))
        if len(chosen) == k:
            break
    return chosen
