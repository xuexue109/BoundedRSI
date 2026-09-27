"""Stable serialization and evidence addresses shared by builder/evaluator."""
import hashlib
import json
from pathlib import Path


def digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(value).hexdigest()


def evidence_id(source, content_hash, locator):
    return digest([source, content_hash, locator])[:24]


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def safe_child(root, relative):
    root = Path(root).resolve()
    target = (root / relative).resolve()
    if target == root or root not in target.parents:
        raise ValueError("Path escapes its root: " + str(relative))
    return target
