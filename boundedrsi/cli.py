"""Command-line entry points for a complete offline pilot."""
import argparse
import json
import sys
from pathlib import Path
from .common import read_json, read_jsonl, write_json, write_jsonl
from .generate import generate_dataset
from .surface import build_surface, verify_surface
from .retrieval import retrieve
from .evaluation import evaluate, compare_runs, context_rows, select_workspaces, require_new_output

POLICIES = ("baseline", "table_rows", "temporal", "relations", "combined", "structured_reference", "latest_only_control")


def add_selection(parser):
    parser.add_argument("--data", default="data/pilot")
    parser.add_argument("--splits", nargs="+", default=["development", "validation"])
    parser.add_argument("--include-holdout", action="store_true")
    parser.add_argument("--k", type=int, default=5)


def main(argv=None):
    parser = argparse.ArgumentParser(description="BoundedRSI surface/task/scoring pilot (offline diagnostic)")
    commands = parser.add_subparsers(dest="command", required=True)
    gen = commands.add_parser("generate", help="Generate ten workspaces / 120 tasks into an empty directory")
    gen.add_argument("--out", default="data/pilot")
    gen.add_argument("--seed", type=int, default=6100)
    build = commands.add_parser("build", help="Build a public workspace into a saved surface")
    build.add_argument("--workspace", required=True)
    build.add_argument("--policy", default="policies/combined.json")
    build.add_argument("--out", required=True)
    search = commands.add_parser("search", help="Inspect evidence retrieval with a saved surface")
    search.add_argument("--surface", required=True)
    search.add_argument("--workspace", required=True, help="Original workspace for stale-source checks")
    search.add_argument("--question", required=True)
    search.add_argument("--collection")
    search.add_argument("--as-of")
    search.add_argument("--k", type=int, default=5)
    ev = commands.add_parser("evaluate", help="Run diagnostic executor, or score externally supplied predictions")
    add_selection(ev)
    ev.add_argument("--policy", default="policies/combined.json")
    ev.add_argument("--out", required=True)
    ev.add_argument("--predictions", help="External JSONL: task_id, answer, abstained, citations")
    ctx = commands.add_parser("contexts", help="Export retrieval-only contexts, without gold, for external agents")
    add_selection(ctx)
    ctx.add_argument("--policy", default="policies/combined.json")
    ctx.add_argument("--out", required=True)
    cmp = commands.add_parser("compare", help="Compare matched runs; report fixes, regressions, empirical gate")
    cmp.add_argument("--baseline", required=True)
    cmp.add_argument("--candidate", required=True)
    cmp.add_argument("--out", required=True)
    cmp.add_argument("--max-regression", type=float, default=0.05)
    cmp.add_argument("--max-slice-drop", type=float, default=0.02)
    pilot = commands.add_parser("pilot", help="Generate if missing and run all manual policies; holdout stays locked")
    pilot.add_argument("--data", default="data/pilot")
    pilot.add_argument("--out", default="artifacts/pilot")
    pilot.add_argument("--policies", default="policies")
    pilot.add_argument("--seed", type=int, default=6100)
    pilot.add_argument("--k", type=int, default=5)
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            manifest = generate_dataset(args.out, args.seed)
            print("Generated %d workspaces / %d tasks at %s" % (len(manifest["workspaces"]), sum(w["task_count"] for w in manifest["workspaces"]), args.out))
        elif args.command == "build":
            if Path(args.out).exists():
                raise ValueError("Output exists; choose a new surface filename")
            surface = build_surface(args.workspace, read_json(args.policy))
            write_json(args.out, surface)
            print("Built %d chunks: %s" % (len(surface["chunks"]), args.out))
        elif args.command == "search":
            surface = read_json(args.surface)
            verify_surface(surface, args.workspace)
            task = dict(question=args.question, query=dict(collection=args.collection, as_of=args.as_of))
            print(json.dumps(retrieve(surface, task, args.k), ensure_ascii=False, indent=2))
        elif args.command == "contexts":
            if Path(args.out).exists():
                raise ValueError("Output exists; choose a new context filename")
            entries = select_workspaces(args.data, args.splits, args.include_holdout)
            write_jsonl(args.out, context_rows(args.data, read_json(args.policy), entries, args.k))
            print("Exported public contexts to " + args.out)
        elif args.command == "evaluate":
            result = evaluate(args.data, args.policy, args.out, args.splits, args.k, args.include_holdout, args.predictions)
            print("Success %.2f%%; report: %s/report.md" % (100*result["overall"]["success"], args.out))
        elif args.command == "compare":
            if Path(args.out).exists():
                raise ValueError("Output exists; choose a new comparison filename")
            result = compare_runs(args.baseline, args.candidate, args.max_regression, args.max_slice_drop)
            write_json(args.out, result)
            print(json.dumps(result["gate"], indent=2))
        elif args.command == "pilot":
            if not (Path(args.data) / "manifest.json").exists():
                generate_dataset(args.data, args.seed)
            output = require_new_output(args.out)
            summaries = {}
            for policy in POLICIES:
                result = evaluate(args.data, Path(args.policies)/(policy+".json"), output/policy, k=args.k)
                summaries[policy] = result
                print("%-24s %.2f%% (%d tasks)" % (policy, 100*result["overall"]["success"], result["overall"]["n"]))
            comparisons = {p: compare_runs(output/"baseline", output/p) for p in POLICIES[1:]}
            comparisons["negative_control_vs_combined"] = compare_runs(output/"combined", output/"latest_only_control")
            write_json(output/"comparisons.json", comparisons)
            lines = ["# BoundedRSI Pilot", "", "10 个合成 workspace / 120 个任务；本次只评测 6 个开发 + 2 个验证 workspace（96 题）。",
                     "", "**确定性结构化查询诊断，不是 LLM Agent 实验，不是自动 RSI，不构成泛化或统计显著性的证据。**",
                     "", "| Policy | 完整成功率 | 相对基线修复 | 相对基线回归 | 构建 ms |", "|---|---:|---:|---:|---:|"]
            for p in POLICIES:
                summary = summaries[p]
                comp = comparisons.get(p, dict(fixes=[], regressions=[]))
                lines.append("| %s | %.2f%% | %d | %d | %.2f |" % (p, 100*summary["overall"]["success"], len(comp["fixes"]), len(comp["regressions"]), summary["build_ms"]))
            lines += ["", "baseline 有意注入三类信息丢失，不能代表强 RAG 基线。structured_reference 从一开始保留完整结构，与 combined 相同是预期行为。",
                      "", "relations 依赖 table_rows，不能将两者的差异误称为完全独立消融。",
                      "", "latest_only_control 是人为负对照：丢弃历史版本，检查能否发现历史查询回归。",
                      "", "## 按切片和 split 比较", "", "| Split | Task type | Baseline | Table | Temporal | Relations | Combined |", "|---|---|---:|---:|---:|---:|---:|"]
            lookup = {p: {(s["split"], s["kind"]): s["success"] for s in summaries[p]["slices"]} for p in POLICIES}
            for split, kind in sorted(lookup["baseline"]):
                lines.append("| %s | %s | %s |" % (split, kind, " | ".join("%.1f%%" % (100*lookup[p][(split,kind)]) for p in POLICIES[:5])))
            lines += ["", "锁定 holdout 的 24 道任务没有用于本次评价或选优。跨 split 仍属于同一合成生成器，不能声称跨真实领域泛化。",
                      "", "## 查看具体证据", "", "每个策略目录有 report.md、summary.json、scores.csv、scores.jsonl、predictions.jsonl、run.json 和 surfaces/。",
                      "", "compare 的 gate 只作经验性演示，无置信保证、成本约束发布器或真实部署动作。",
                      "", "下一步：人工审计失败 → 加入不同来源/模板 workspace → 固定真实 LLM → 导出 contexts 并导入预测 → 再研究自动修改。"]
            (output/"report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
            print("Report: " + str(output/"report.md"))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, "Error: %s\n" % exc)


if __name__ == "__main__":
    main()
