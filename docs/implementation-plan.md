# Surface pilot implementation plan

Goal: deliver a locally runnable, auditable pilot in the existing BoundedRSI folder.
Architecture: Python package with separate generator, builder, retrieval/executor,
evaluator and CLI. JSON artifacts make each boundary inspectable.
Spec: design.md. Runtime: Python 3.9+, standard library for the core pilot.

1. Define model and evidence ID interfaces; test deterministic IDs and answer scoring.
2. Generate ten workspace families and 120 tasks with evaluator-only gold labels.
3. Build MD/CSV/JSON surfaces with content hashes and three configurable repairs.
4. Retrieve using BM25 and optional temporal filtering/relationship expansion;
   execute public structured queries without access to gold.
5. Score predictions, aggregate by split/type, compare fixes/regressions, export reports.
6. Provide build/search/evaluate/score/compare commands and VS Code launch/tasks.
7. Run unit and end-to-end tests, execute the development/validation pilot, and inspect
   the generated report in VS Code. Keep locked holdout unopened by evaluation.

Review focus: future version leakage; valid-to boundary; forged source references;
multi-evidence joins; missing fields versus numeric zero; gold accidentally ingested;
stale source data; unsafe paths; silent partial external predictions; split mismatch.

User requested implementation directly from the referenced design; execute locally.
