"""Generate facts first, then documents, public tasks and private evaluation labels."""
import csv
import io
import random
from pathlib import Path
from .common import digest, evidence_id, write_json, write_jsonl


def generate_dataset(destination, seed=6100):
    root = Path(destination)
    if root.exists() and any(root.iterdir()):
        raise ValueError("Dataset destination must be empty; use a new directory to avoid overwrites")
    rng = random.Random(seed)
    root.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "seed": seed, "synthetic": True, "workspaces": []}
    names = ["Orchid", "Harbor", "Cedar", "Maple", "Delta", "Summit", "Willow", "Quartz", "Birch", "Aurora"]
    for index, name in enumerate(names):
        wid = "ws_%02d" % (index + 1)
        split = "development" if index < 6 else "validation" if index < 8 else "locked_holdout"
        workspace = root / "workspaces" / wid
        docs = workspace / "documents"
        docs.mkdir(parents=True)
        catalog, tasks, gold = [], [], {}
        hashes = {}

        def document(filename, text, collection, **metadata):
            (docs / filename).write_text(text, encoding="utf-8")
            hashes[filename] = digest(text.encode())
            catalog.append(dict(path=filename, collection=collection, **metadata))

        def table(filename, rows, collection):
            stream = io.StringIO(newline="")
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            document(filename, stream.getvalue(), collection)

        def eid(filename, locator):
            return evidence_id(filename, hashes[filename], locator)

        def task(kind, question, query, answer, evidence):
            tid = wid + "_q%02d" % (len(tasks) + 1)
            tasks.append(dict(id=tid, workspace_id=wid, kind=kind, question=question, query=query))
            gold[tid] = dict(answer=answer, unanswerable=answer is None,
                            evidence_sets=[[eid(f, loc) for f, loc in evidence]] if evidence else [],
                            sources=sorted({f for f, _ in evidence}))

        # Column names differ across families; task query is a public, typed request.
        team_key = ["department", "team", "division"][index % 3]
        budget_key = ["budget", "allocation", "annual_limit"][index % 3]
        order_key = ["order_id", "purchase_id"][index % 2]
        team_a, team_b = name + " Research", name + " Outreach"
        amount_a, amount_b = rng.randrange(20, 50) * 100, rng.randrange(50, 80) * 100
        shipping = "2026-11-%02d" % (index + 10)
        document("manual.md", "# %s handbook\nshipping_date: %s\ncontact_channel: helpdesk\n" % (name, shipping), "manual")
        document("department.json", '[{"region": "Zone-%d", "contact": "%s Office"}]\n' % (index + 1, name), "directory")
        table("budgets.csv", [{team_key: team_a, budget_key: amount_a},
                              {team_key: team_b, budget_key: amount_b},
                              {team_key: name + " Admin", budget_key: 0}], "budgets")
        low, high = 400 + index * 25, 750 + index * 25
        document("policy_alpha.md", "# Travel policy\nhotel_limit: %d\nmeal_limit: %d\n" % (low, 80 + index),
                 "policy", version="1", valid_from="2025-01-01", valid_to="2026-04-01", authority=10)
        document("policy_beta.md", "# Travel policy\nhotel_limit: %d\nmeal_limit: %d\n" % (high, 120 + index),
                 "policy", version="2", valid_from="2026-04-01", valid_to=None, authority=10)
        document("draft_notes.md", "# Unapproved travel proposal\nhotel_limit: 9999\nmeal_limit: 999\n",
                 "policy", version="3", valid_from="2025-01-01", valid_to=None, authority=1)
        order_rows, invoice_rows = [], []
        for j in range(3):
            order_id = "%s-%02d" % (name[:3].upper(), j + 1)
            ordered = rng.randrange(10, 40) * 100
            actual = ordered + [150, -100, 0][j]
            order_rows.append({order_key: order_id, "ordered_amount": ordered})
            invoice_rows.append({"invoice_id": "INV-" + order_id, order_key: order_id, "invoiced_amount": actual})
        table("orders.csv", order_rows, "orders")
        table("invoices.csv", invoice_rows, "invoices")
        task("single_file", "What is the shipping_date in the handbook?", dict(op="lookup", collection="manual", field="shipping_date"), shipping, [("manual.md", "line:2")])
        task("single_file", "Who is the contact in the directory?", dict(op="lookup", collection="directory", field="contact"), name + " Office", [("department.json", "/0")])
        for j, (team, amount) in enumerate([(team_a, amount_a), (team_b, amount_b)]):
            task("table_query", "What is the %s for %s?" % (budget_key, team),
                 dict(op="lookup", collection="budgets", field=budget_key, where={team_key: team}),
                 amount, [("budgets.csv", "row:%d" % (j + 1))])
        for date, filename, amount in [("2026-02-01", "policy_alpha.md", low), ("2026-05-01", "policy_beta.md", high)]:
            task("version_query", "What hotel_limit is effective on %s?" % date,
                 dict(op="lookup", collection="policy", field="hotel_limit", as_of=date), amount, [(filename, "line:2")])
        for date, filename, amount in [("2026-03-31", "policy_alpha.md", 80 + index), ("2026-04-01", "policy_beta.md", 120 + index)]:
            task("conflicting_evidence", "Use the authoritative policy, not the proposal: meal_limit as of %s?" % date,
                 dict(op="lookup", collection="policy", field="meal_limit", as_of=date), amount, [(filename, "line:3")])
        for j in range(2):
            order = order_rows[j]
            task("cross_file_join", "For %s, calculate invoiced_amount minus ordered_amount." % order[order_key],
                 dict(op="join_difference", collection="invoices", where={order_key: order[order_key]},
                      left_key=order_key, right_key=order_key, right_collection="orders",
                      left_field="invoiced_amount", right_field="ordered_amount"),
                 invoice_rows[j]["invoiced_amount"] - order["ordered_amount"],
                 [("invoices.csv", "row:%d" % (j + 1)), ("orders.csv", "row:%d" % (j + 1))])
        task("insufficient_info", "What is the handbook emergency_phone?",
             dict(op="lookup", collection="manual", field="emergency_phone"), None, [])
        task("insufficient_info", "What is the budget for Unknown Team?",
             dict(op="lookup", collection="budgets", field=budget_key, where={team_key: "Unknown Team"}), None, [])
        write_json(workspace / "workspace.json", dict(id=wid, family=name, documents=catalog))
        write_jsonl(root / "tasks" / (wid + ".jsonl"), tasks)
        write_json(root / "gold" / (wid + ".json"), gold)
        manifest["workspaces"].append(dict(id=wid, family=name, split=split, task_count=len(tasks)))
    write_json(root / "manifest.json", manifest)
    return manifest
