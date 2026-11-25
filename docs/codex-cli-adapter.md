# Codex CLI runtime adapter — selected integration

**Runtime selected:** OpenAI Codex CLI. **MVP 01 is now implemented** as a bounded, opt-in, **single-stage** live runner in prototype/codex_runtime.py, invoked with `uv run workflow.py codex-stage`. It launches a distinct specialist and fresh read-only Handoff Codex session, captures logs and JSON results, materializes isolated candidate artifacts and validates identity/paths. It does not yet implement the automatic Coordinator scheduler, live gate progression, or true EDD/UX proof. The local Git adapter binds a selected worktree; the default mock stages still do not invoke Codex. See [MVP 01](live-codex-mvp01.md).

**Local Coordinator launcher:** `uv run workflow.py` starts the guided simulated terminal prototype using uv-managed Python 3.12, with no Python package installation. uv runs Python orchestration; **Codex CLI** runs actual specialists only when `codex-stage --confirm` is explicitly chosen. These are complementary tools.

Local project tracking is independent of Codex and GitHub: work/<initiative>/project-ticket.md and work/<initiative>/mvps/mvp-NN/ticket.md are updated by the deterministic Coordinator after each transition. The GitHub Issues mirror is optional. Once real Codex runs are available, run the specialist and handoff within the selected MVP worktree, but keep the single project integration worktree as a separate destination.

**Prototype preview feedback is a separate gate contract.** The local TUI already implements G2 iteration IDs, a synthetic browser-openable preview, structured human feedback, rerun, renewed hash-bound approval and acceptance handoff to Specification. A real Codex Prototype session must produce its own runnable demo and revision-bound preview manifest; the Coordinator must safely launch/stop that preview, capture checks, and always request a *new* G2 after feedback. It must not describe the bundled sample HTML as code built by Codex. See `docs/prototype-review-contract.md`.

## Codex CLI invocation contract

Use **codex exec**, one process/session per specialist. Pass the specialist prompt through stdin (avoid shell string interpolation), use a known repository working directory, and capture all process outputs:

~~~text
codex exec --json --sandbox workspace-write --cd <repo-root>
  --output-schema <workflow-root>/schemas/specialist-result.schema.json
  --output-last-message <run-dir>/specialist-result.json -
~~~

The implemented MVP 01 local runner supplies stdin, collects stdout as event JSONL, captures stderr and exit code, and records source identity. It uses a **more specific extended schema** (schemas/codex-live-result.schema.json) that adds report_markdown and authorized Markdown artifacts; the Python host safely stages these under a candidate root before producing the canonical specialist-result metadata. It resolves the selected worktree, binds run ID to its Git common directory/branch/HEAD, and records post-run changes. RED/GREEN/REFACTOR get workspace-write; other roles get read-only. Do not use dangerous sandbox-bypass flags.

For the **Handoff Agent**, start a **separate read-only** Codex process:

~~~text
codex exec --json --sandbox read-only --cd <repo-root>
  --output-schema <workflow-root>/schemas/handoff.schema.json
  --output-last-message <run-dir>/normalized-handoff.json -
~~~

MVP 01 now writes these two structured final outputs and raw logs to controlled paths. The Handoff Agent has read-only source access. A bounded timeout and real process exit status are recorded; no candidate result is automatically approved or promoted. The older shell snippets above describe the general adapter contract; use the implemented command rather than manually copying those invocations.

These options are supported in the Codex CLI help verified for this kit. The syntax above is schematic: line wrapping is illustrative, and <repo-root>, <workflow-root> and <run-dir> must be supplied by the runner as validated paths.

## Canonical final statuses

The specialist's JSON result must use one canonical status from its role:

| Role | Allowed final statuses |
| --- | --- |
| DISCOVERY / PROTOTYPE / SPECIFICATION / MVP_DESIGN | READY_FOR_REVIEW, BLOCKED |
| UIUX DESIGN | DESIGN_READY, BLOCKED (dedicated fresh session) |
| UIUX INSPECT | UX_VERIFIED, UX_FAILED, INCONCLUSIVE, BLOCKED (dedicated fresh session; INSPECTION_RECORDED is **mock-only**) |
| REQUIREMENTS | READY, BLOCKED |
| RED | RED_CONFIRMED, ALREADY_GREEN, BLOCKED |
| GREEN | GREEN_CONFIRMED, BLOCKED |
| REFACTOR | REFACTOR_COMPLETE, NO_REFACTOR_NEEDED, BLOCKED |
| VERIFY | VERIFIED, FAILED, INCONCLUSIVE |
| PROVE | PASS, FAIL, INCONCLUSIVE |

The Handoff Agent can understand prose such as “ready for review,” but it cannot quietly convert an invalid specialist JSON status to a passing status. The originating specialist must repair its structured result. The strict authoritative status is the original specialist result, not the Handoff Agent's recommendation.

## Execution ordering

1. Coordinator selects the approved next specialist and gives it one assigned role file.
2. The Codex CLI process produces repository changes (within its authorized scope), a structured final result, a JSONL event trace, and its exit status.
3. The runtime captures **trusted** facts: actual file inventory, pre/post source identity, exit status, and output paths.
4. Coordinator starts the reusable Handoff Agent with read-only context and the producer's real artifacts.
5. Handoff Agent emits a candidate normalized-handoff.json.
6. A **deterministic validator** checks schema, path safety/existence, role and run ID, matching status, allowed output scope, and no reported blocking issues.
7. Coordinator checks the specialist's phase-specific evidence requirements, human gates, and source revision. Only then may it dispatch the next specialist.
8. On invalid handoff, return it to the producing specialist for correction or pause for human scope resolution. Never let the Handoff Agent invent a pass.

**UIUX twice per MVP:** after G5 (feature spec and UX spec approved), launch one UIUX DESIGN `codex exec` session using `agents/edd/00-uiux.md` with `mode=DESIGN` and outputs `ux/design.md`, `ux/design-review.md`. Later, after VERIFY, start a **new, separate** UIUX INSPECT `codex exec` in read-only implementation mode with `mode=INSPECT`; collect actual UI/device/browser observations and `ux/inspection.md` plus evidence. The deterministic validator checks report-path/mode/status consistency. The real Coordinator must inspect evidence freshness and require `UX_VERIFIED` (or human-approved NOT_APPLICABLE) before PROVE PASS. A mock `INSPECTION_RECORDED` cannot authorize real proof; see docs/uiux-quality-gates.md.

For MVP-02 and later, create or select an appropriately based worktree only after the relevant scope approval, deriving its base from the latest approved integration revision. After each *real* PROVE PASS, verify the committed MVP tip, integrate it (fast-forward where possible; conflicts/divergence stop for review), rerun impacted prior-MVP regressions, and push the one integration branch non-force when authorized. Refresh local parent/sub-tickets after every phase and integration event; mirror summaries to GitHub only if opted in. The prototype implements these as explicit primitives, not an automatic proof-driven deployment engine.

## Deterministic verification requirements

- Treat role names, expected status sets, paths and source identity from the runner/manifest as authoritative. Text emitted by either agent is **not** an authorization.
- Use repository-relative paths; reject absolute paths, parent traversal, symlink escapes, missing files, and paths outside role-allowed roots.
- Enforce exact expected run ID and role, no silent remapping between MVPs, and stable acceptance IDs.
- Match claimed exit statuses and test results to actual captured command/trace evidence. A valid JSON shape alone does not establish test success.
- Require read-only execution for Handoff, VERIFY, and PROVE with respect to implementation; role-specific file ownership is additionally enforced with worktrees or diff checks, because workspace-write alone is not per-file permission isolation.
- Validate approvals separately through a trusted human channel with an artifact version/hash. Never accept a model-generated approval.
- Restrict automatic handoff repair attempts and record every retry; ambiguous mappings go back to the producer.

## Two-step validation is intentional

The Handoff Agent can semantically interpret a human-readable report and discover that a producer wrote a report under a different filename. It **cannot** make that report exist or authorize renaming it. The runtime must verify the candidate path and reject conflicts. If the required canonical artifact remains missing, route correction to the producer.

## Smoke test before real automation

Run the bundled deterministic validator tests from the workflow directory:

~~~text
uv run python -m unittest discover -s tests -v
~~~

1. Use a mock producer with a valid handoff and an existing artifact: normalized handoff should validate.
2. Rename or omit a required file: validation must fail, not proceed.
3. Give multiple plausible output paths: Handoff must report ambiguity.
4. Supply an incorrect role/run ID or invalid status: validation must fail.
5. Point to a file outside the project or a symlink escape: validation must fail.
6. Simulate fake PASS with no logs: Coordinator must reject completion.
7. Simulate a blocked human approval: Coordinator must not dispatch the next specialist.

An actual deployment-grade adapter additionally needs durable state, idempotent scheduling, process monitoring, and safe recovery. See docs/runtime-adapter.md.
