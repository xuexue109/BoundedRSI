"""Experiment orchestration; public prediction precedes evaluator-only scoring."""
import csv
import platform
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from .common import digest, read_json, read_jsonl, write_json, write_jsonl, safe_child
from .executor import predict
from .retrieval import retrieve
from .scoring import score_prediction
from .surface import build_surface

METRICS = ("success", "answer_correct", "version_correct", "evidence_correct",
           "recall_at_k", "any_hit_at_k", "complete_evidence_at_k", "abstention_correct")


def select_workspaces(data, splits, allow_holdout=False):
    requested = set(splits)
    known = {"development", "validation", "locked_holdout"}
    if not requested or requested - known:
        raise ValueError("Unknown or empty split selection: " + str(requested))
    if "locked_holdout" in requested and not allow_holdout:
        raise ValueError("Locked holdout requires --include-holdout after freezing the design")
    manifest = read_json(Path(data) / "manifest.json")
    entries = [w for w in manifest["workspaces"] if w["split"] in requested]
    if not entries:
        raise ValueError("No workspaces selected")
    if len({w["id"] for w in entries}) != len(entries):
        raise ValueError("Duplicate workspace IDs")
    return entries


def metric_summary(rows):
    result = {"n": len(rows)}
    for key in METRICS:
        values = [r[key] for r in rows if r[key] is not None]
        result[key] = sum(values) / len(values) if values else None
        result[key + "_n"] = len(values)
    times = sorted(r["latency_ms"] for r in rows if r.get("latency_ms") is not None)
    result["query_latency_ms_p50"] = times[(len(times)-1)//2] if times else None
    result["query_latency_ms_p95"] = times[min(len(times)-1, int(len(times)*0.95))] if times else None
    return result


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["split"], row["kind"])].append(row)
    by_workspace = defaultdict(list)
    for row in rows:
        by_workspace[row["workspace_id"]].append(row["success"])
    return dict(overall=metric_summary(rows), slices=[dict(split=s, kind=k, **metric_summary(v))
                 for (s, k), v in sorted(groups.items())],
                workspace_macro_success=sum(sum(v)/len(v) for v in by_workspace.values()) / len(by_workspace))


def require_new_output(output):
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory is nonempty; choose a new run name to preserve artifacts")
    output.mkdir(parents=True, exist_ok=True)
    return output


def validate_external_predictions(path, task_ids):
    predictions = read_jsonl(path)
    ids = [p.get("task_id") for p in predictions]
    if len(set(ids)) != len(ids) or set(ids) != set(task_ids):
        raise ValueError("External predictions must contain each selected task exactly once, with no extra IDs")
    for p in predictions:
        if type(p.get("abstained")) is not bool or "answer" not in p:
            raise ValueError("Prediction requires answer and boolean abstained")
        if not isinstance(p.get("citations"), list) or any(not isinstance(c, str) for c in p["citations"]):
            raise ValueError("Prediction citations must be a list of evidence ID strings")
    return {p["task_id"]: p for p in predictions}


def context_rows(data, policy, entries, k):
    """No gold files read here. Suitable for giving context to an external LLM."""
    for entry in entries:
        workspace = safe_child(Path(data) / "workspaces", entry["id"])
        surface = build_surface(workspace, policy)
        for task in read_jsonl(Path(data) / "tasks" / (entry["id"] + ".jsonl")):
            hits = retrieve(surface, task, k)
            yield dict(task_id=task["id"], question=task["question"],
                       policy_hash=surface["policy_hash"],
                       passages=[dict(evidence_id=h["chunk"]["id"], text=h["chunk"]["text"],
                                      source=h["chunk"]["source"], locator=h["chunk"]["locator"],
                                      metadata=h["chunk"]["metadata"]) for h in hits])


def evaluate(data, policy_path, output, splits=("development", "validation"), k=5,
             allow_holdout=False, predictions_path=None):
    if k < 1:
        raise ValueError("k must be positive")
    data, policy_path = Path(data), Path(policy_path)
    entries = select_workspaces(data, splits, allow_holdout)
    policy = read_json(policy_path)
    all_tasks = {w["id"]: read_jsonl(data / "tasks" / (w["id"] + ".jsonl")) for w in entries}
    tasks_flat = [t for ts in all_tasks.values() for t in ts]
    task_ids = [t["id"] for t in tasks_flat]
    if len(set(task_ids)) != len(task_ids):
        raise ValueError("Duplicate task IDs")
    external = validate_external_predictions(predictions_path, task_ids) if predictions_path else None
    output = require_new_output(output)
    engine = "external-predictions" if external is not None else "deterministic-diagnostic"
    run = dict(schema_version=1, created_at=datetime.now(timezone.utc).isoformat(),
               engine=engine, python=platform.python_version(), policy=policy,
               policy_hash=digest(policy), k=k, splits=sorted(set(splits)),
               holdout_access="locked_holdout" in splits, task_ids=task_ids,
               manifest_hash=digest(read_json(data / "manifest.json")),
               task_hash=digest(tasks_flat), source_hashes={}, gold_hashes={})
    # Snapshot every selected input before inference. Hashes are not passed to executor.
    for entry in entries:
        wid = entry["id"]
        catalog_path = data / "workspaces" / wid / "workspace.json"
        catalog = read_json(catalog_path)
        run["source_hashes"][wid] = dict(catalog=digest(catalog), documents={
            d["path"]: digest(safe_child(catalog_path.parent / "documents", d["path"]).read_bytes())
            for d in catalog["documents"]})
        run["gold_hashes"][wid] = digest((data / "gold" / (wid + ".json")).read_bytes())
    run["dataset_fingerprint"] = digest({key: run[key] for key in ("manifest_hash", "task_hash", "source_hashes", "gold_hashes")})
    run["status"] = "started"
    write_json(output / "run.json", run)
    rows, predictions, contexts, build_ms = [], [], [], 0.0
    started = time.perf_counter()
    for entry in entries:
        wid = entry["id"]
        before = time.perf_counter()
        surface = build_surface(data / "workspaces" / wid, policy)
        build_ms += (time.perf_counter() - before) * 1000
        write_json(output / "surfaces" / (wid + ".json"), surface)
        workspace_predictions = []
        for task in all_tasks[wid]:
            if external is None:
                p = predict(surface, task, k)
            else:
                # External output cannot claim it retrieved evidence that was not in context.
                hits = retrieve(surface, task, k)
                p = {key: external[task["id"]][key] for key in ("task_id", "answer", "abstained", "citations")}
                p.update(retrieved=[h["chunk"]["id"] for h in hits], latency_ms=None,
                         engine=engine, reason="externally_supplied")
            workspace_predictions.append((task, p))
        gold = read_json(data / "gold" / (wid + ".json"))
        if set(gold) != {t["id"] for t in all_tasks[wid]}:
            raise ValueError("Gold/task IDs do not match for " + wid)
        for task, p in workspace_predictions:
            score = score_prediction(gold[task["id"]], p, surface["registry"])
            predictions.append(p)
            rows.append(dict(task_id=task["id"], workspace_id=wid, split=entry["split"],
                             kind=task["kind"], latency_ms=p["latency_ms"],
                             failure_stage=("none" if score["success"] else
                                 "retrieval_or_surface" if score["complete_evidence_at_k"] == 0 else
                                 "answer_or_evidence_selection"), **score))
    summary = summarize(rows)
    summary.update(build_ms=build_ms, elapsed_ms=(time.perf_counter()-started)*1000)
    write_jsonl(output / "predictions.jsonl", predictions)
    write_jsonl(output / "scores.jsonl", rows)
    write_json(output / "summary.json", summary)
    with (output / "scores.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    report = ["# BoundedRSI 实验报告", "", "运行器：`%s`；策略：`%s`；k=%d。" % (engine, policy["name"], k),
              "", "这是合成数据上的诊断实验；确定性运行器的结果不能证明 LLM Agent 能力提升。", "",
              "| Split | Task type | N | 完整成功 | 答案 | 版本 | 证据 | Recall@k |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    fmt = lambda x: "N/A" if x is None else "%.1f%%" % (100*x)
    for item in summary["slices"]:
        report.append("| %s | %s | %d | %s | %s | %s | %s | %s |" % (item["split"], item["kind"], item["n"],
            *[fmt(item[key]) for key in ("success", "answer_correct", "version_correct", "evidence_correct", "recall_at_k")]))
    report += ["", "完整成功：%s；workspace 宏平均：%s。" % (fmt(summary["overall"]["success"]), fmt(summary["workspace_macro_success"])),
               "", "N/A 项从对应指标分母中排除；分母见 summary.json 的 *_n。拒答单独评分。",
               "", "Recall@k 是多条必要证据的覆盖比例；any_hit 和 complete_evidence 同时记录。",
               "", "构建耗时 %.2f ms；总评测耗时 %.2f ms。外部预测的模型成本/延迟未知，不记为零。" % (build_ms, summary["elapsed_ms"]),
               "", "输入指纹：`%s`。" % run["dataset_fingerprint"], "",
               "逐题输出见 predictions.jsonl；评分与初步失败分类见 scores.jsonl。失败阶段只是诊断线索，不是因果证明。"]
    (output / "report.md").write_text("\n".join(report)+"\n", encoding="utf-8")
    run["status"] = "complete"
    write_json(output / "run.json", run)
    return summary


def compare_runs(baseline_dir, candidate_dir, max_regression=0.05, max_slice_drop=0.02):
    if not 0 <= max_regression <= 1 or not 0 <= max_slice_drop <= 1:
        raise ValueError("Gate thresholds must be between 0 and 1")
    a, b = [read_json(Path(p) / "run.json") for p in (baseline_dir, candidate_dir)]
    for key in ("dataset_fingerprint", "k", "engine", "splits"):
        if a[key] != b[key]:
            raise ValueError("Incomparable runs: different " + key)
    if a["status"] != "complete" or b["status"] != "complete":
        raise ValueError("Only completed runs may be compared")
    old, new = [{r["task_id"]: r for r in read_jsonl(Path(p) / "scores.jsonl")} for p in (baseline_dir, candidate_dir)]
    if old.keys() != new.keys():
        raise ValueError("Compared runs must contain identical task IDs")
    fixes = [key for key in old if not old[key]["success"] and new[key]["success"]]
    regressions = [key for key in old if old[key]["success"] and not new[key]["success"]]
    old_passes = sum(row["success"] for row in old.values())
    reg_rate = len(regressions) / old_passes if old_passes else 0.0
    slices = defaultdict(list)
    for key in old:
        slices[(old[key]["split"], old[key]["kind"])].append(new[key]["success"] - old[key]["success"])
    deltas = [dict(split=s, kind=k, n=len(v), delta=sum(v)/len(v)) for (s, k), v in sorted(slices.items())]
    gain = (len(fixes)-len(regressions)) / len(old)
    reasons = []
    if gain <= 0:
        reasons.append("No positive overall gain")
    if reg_rate > max_regression:
        reasons.append("Pass-to-fail regression rate exceeds threshold")
    if any(d["delta"] < -max_slice_drop for d in deltas):
        reasons.append("At least one split/task slice exceeds allowed drop")
    if a["holdout_access"] or b["holdout_access"]:
        reasons.append("Holdout is reporting-only and cannot drive a release decision")
    return dict(baseline=a["policy"]["name"], candidate=b["policy"]["name"], n=len(old),
                overall_delta=gain, fixes=fixes, regressions=regressions,
                prior_successes=old_passes, regression_rate=reg_rate, slices=deltas,
                gate=dict(accepted=not reasons, reasons=reasons, max_regression=max_regression,
                          max_slice_drop=max_slice_drop, mode="empirical-diagnostic-only",
                          note="No statistical guarantee; no automatic deployment; same validation data reused."))
