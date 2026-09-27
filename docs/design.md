# BoundedRSI pilot design

Scope: implement the approved Surface/Task/Scoring pilot, not an autonomous
self-modifying agent. The source conversation's numerical examples are illustrative,
not results. Ten synthetic workspaces contain twelve tasks each, across six slices.

Surface = parsed content + records/chunks + metadata + lexical index + stable
source/evidence mapping + optional record relationships. Raw documents are immutable.
Task queries are public structured requests; answers and evidence labels are evaluator-only.

The offline executor is a deterministic query interpreter, not an LLM. It enables
pipeline diagnosis without API keys; its scores cannot establish LLM improvement.
External agent predictions can be scored through the same evaluator.

Policies: a deliberately lossy diagnostic baseline; table-record preservation;
temporal metadata preservation; relational links (requires table records); combined
manual repair; independently named structured reference with all capabilities.
A latest-only negative control tests regression detection. It is not an observed
natural regression or an automatically discovered modification.

Splits are assigned by workspace family: six development, two validation, two
locked holdout. Default evaluation excludes locked holdout. Final evaluation requires
an explicit flag, creates a frozen-input manifest, and never selects policies from
holdout results. This is a local convention, not an OS security boundary.

Evidence-grounded success requires correct answer, exact supported evidence (one of
the approved alternative evidence sets), and correct source/version. Unsupported or
unretrieved citations fail. Unanswerable tasks require explicit abstention, no answer,
and no citations; evidence/version/recall are not applicable, not fictitious successes.
Report answer accuracy, evidence accuracy, version accuracy, evidence recall@k,
any-hit@k, complete-evidence@k, and pass-to-fail changes with clear denominators.

Raw hashes and policy hashes accompany surfaces and runs. Reports compare identical
task IDs and split manifests. The small synthetic pilot is not a significance study.
Text PDF extraction is optional; no OCR or automatic cross-page table recovery claim.

Acceptance: reproducible data; no evaluator labels in build/solve input; temporal
boundary and citation forgery tests; join provenance includes both records; explicit
holdout access; per-task JSONL and Markdown/CSV summaries; VS Code run/test tasks.
