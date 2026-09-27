"""Build only from public workspace documents; never consume task/gold files."""
import csv
import json
from pathlib import Path
from .common import digest, evidence_id, read_json, safe_child


def scalar(value):
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value.strip()


def parse_document(path):
    """Yield (locator, text, record, kind). Locations refer to original sources."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise ValueError("CSV requires unique headers: " + str(path))
            for i, row in enumerate(reader, 1):
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("CSV row has a different width from header: " + str(path))
                fields = {k: scalar(v) for k, v in row.items()}
                yield "row:%d" % i, "; ".join("%s=%s" % p for p in fields.items()), fields, "table"
    elif suffix == ".json":
        data = read_json(path)
        records = data if isinstance(data, list) else [data]
        for i, row in enumerate(records):
            if not isinstance(row, dict):
                raise ValueError("JSON must be an object or list of objects")
            yield "/%d" % i if isinstance(data, list) else "/", json.dumps(row, ensure_ascii=False), row, "record"
    elif suffix in (".md", ".txt"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            fields = {}
            if ":" in line and not line.startswith("#"):
                key, value = line.split(":", 1)
                fields[key.strip()] = scalar(value.strip())
            yield "line:%d" % i, line.strip(), fields, "text"
    elif suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError("PDF support requires: python -m pip install '.[pdf]'") from exc
        for i, page in enumerate(PdfReader(path).pages, 1):
            text = page.extract_text() or ""
            if not text.strip():
                raise ValueError("PDF page %d has no extractable text; OCR is not implemented" % i)
            yield "page:%d" % i, text, {}, "pdf"
    else:
        raise ValueError("Unsupported document format: " + suffix)


def build_surface(workspace, policy):
    workspace = Path(workspace)
    catalog = read_json(workspace / "workspace.json")
    allowed = {"name", "description", "table_records", "temporal_metadata", "relational_links", "latest_only"}
    if set(policy) - allowed:
        raise ValueError("Unknown policy keys: " + str(set(policy) - allowed))
    if policy.get("relational_links") and not policy.get("table_records"):
        raise ValueError("Relational links require preserved table records")
    chunks, registry, hashes = [], {}, {}
    for document in catalog["documents"]:
        source = document["path"]
        path = safe_child(workspace / "documents", source)
        sha = digest(path.read_bytes())
        hashes[source] = sha
        parsed = list(parse_document(path))
        for locator, _, _, _ in parsed:
            rid = evidence_id(source, sha, locator)
            registry[rid] = dict(source=source, locator=locator, sha256=sha,
                                 version=document.get("version"))
        if path.suffix.lower() == ".csv" and not policy.get("table_records"):
            # Explicitly lossy diagnostic fault: flatten values and lose row/header binding.
            parsed = [("file", " ".join(str(v) for _, _, fields, _ in parsed for v in fields.values()), {}, "flat_table")]
        for locator, text, fields, kind in parsed:
            rid = evidence_id(source, sha, locator)
            registry.setdefault(rid, dict(source=source, locator=locator, sha256=sha, version=document.get("version")))
            metadata = {k: v for k, v in document.items() if k != "path"}
            if not policy.get("temporal_metadata"):
                metadata.pop("valid_from", None)
                metadata.pop("valid_to", None)
            chunks.append(dict(id=rid, text=text, fields=fields, kind=kind, source=source,
                               locator=locator, metadata=metadata, links=[]))
    if policy.get("latest_only"):
        newest = {}
        for c in chunks:
            m = c["metadata"]
            key = (m["collection"], m.get("authority", 0))
            newest[key] = max(newest.get(key, ""), m.get("valid_from", ""))
        chunks = [c for c in chunks if c["metadata"].get("valid_from", "") == newest[(c["metadata"]["collection"], c["metadata"].get("authority", 0))]]
    if policy.get("relational_links"):
        by_key = {}
        for c in chunks:
            for key, value in c["fields"].items():
                if key.endswith("_id") and value not in (None, ""):
                    by_key.setdefault((key, str(value)), []).append(c)
        for (key, _), nodes in by_key.items():
            for c in nodes:
                for other in nodes:
                    if c["source"] != other["source"]:
                        c["links"].append(dict(target=other["id"], key=key))
    from .retrieval import make_index
    return dict(schema_version=1, workspace_id=catalog["id"], policy=policy,
                policy_hash=digest(policy), document_hashes=hashes, catalog_hash=digest(catalog),
                chunks=chunks, registry=registry, index=make_index(chunks))


def verify_surface(surface, workspace):
    workspace = Path(workspace)
    if digest(read_json(workspace / "workspace.json")) != surface["catalog_hash"]:
        raise ValueError("Stale surface: workspace catalog changed")
    for source, expected in surface["document_hashes"].items():
        if digest(safe_child(workspace / "documents", source).read_bytes()) != expected:
            raise ValueError("Stale surface: source changed: " + source)
