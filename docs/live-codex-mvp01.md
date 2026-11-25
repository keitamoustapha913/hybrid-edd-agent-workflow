# MVP 01 — opt-in live Codex CLI specialist runtime

**Implemented:** prototype/codex_runtime.py, invoked with 'uv run workflow.py codex-stage'.
**Later MVP 02–05 are now available:** automatic adoption and human-gated dispatch are implemented in `prototype/live_coordinator.py` and documented in [MVP 02 live Coordinator](live-coordinator-mvp02.md). This **MVP 01 isolated pilot command** remains candidate-only. MVP 03 adds actual G2 preview hosting; MVP 04 adds host tests, screenshots and human UX/proof review; MVP 05 schedules subsequent MVPs sequentially after G7. None of these claims automated visual quality grading or production release certification.

This is a deliberate vertical slice of actual Codex execution. The regular 'run', 'approve', and terminal dashboard still use **simulation-only** stage progression. This independent live command does **not** increment the SQLite stage index, approve a gate, update a project ticket, commit, push, or release.

## Prepare a project

The installed Codex CLI must support codex exec with JSON events, an output schema, output-last-message, sandbox, working-directory, and ephemeral-session flags, and must already be authenticated in the environment where it runs. No credentials are requested by this kit. Never use full-access sandbox bypass flags.

From the kit root:

~~~bash
uv run workflow.py init my-project
uv run workflow.py repo-attach my-project --path /absolute/path/to/existing-git-checkout
~~~

Edit work/my-project/brief.md before asking Discovery to run. The selected checkout must be a **clean Git worktree**; dirty/untracked changes block preflight. The kit's local work/ records remain outside the target repository.

Preview the exact one-stage plan without starting Codex:

~~~bash
uv run workflow.py codex-stage my-project --stage discovery --dry-run
~~~

Execute **only that stage**, after confirming the selected Git worktree and scope:

~~~bash
uv run workflow.py codex-stage my-project --stage discovery --confirm
~~~

Use a model override only if desired, e.g. --model YOUR_CONFIGURED_CODEX_MODEL. Default is the installed CLI's configured model. Every live invocation costs real model usage. A maximum execution time is bounded by --timeout 900 (seconds **per** specialist/Handoff session).

The stage selector supports discovery, prototype, specification, mvp_design, ux_design, requirements, red, green, refactor, verify, ux_inspect, and prove. Each stage must have its input contracts already present under work/my-project/; missing files block preflight. This pilot does not generate missing approvals or automatically import a prior candidate output into the source-of-truth files. An operator must review, approve and carry forward results separately until MVP 02 adds Coordinator adoption.

## Exactly what happens

1. Resolve the attached Git **worktree, branch, common directory and HEAD**; reject missing, changed, or dirty worktrees.
2. Read only the named stage's required local Markdown inputs and embed their contents/digests as untrusted context in its assigned specialist prompt.
3. Generate a fresh codex-* run ID and persist run.json in work/my-project/codex-runs/<run-id>/ before dispatch.
4. Start **one new Codex CLI process**, explicitly scoped to the Git worktree, with JSON events, structured output, an ephemeral session, a fixed sandbox, and a timeout. Save the exact prompt, invocation, JSONL event log, stderr and final result.
5. Require exact authoritative role/run/MVP/status/report identity and canonical artifact paths. Reject path traversal, missing required artifacts, invalid JSON, unsupported statuses, and empty successful reports.
6. Check actual Git HEAD/branch and dirty status after the specialist, blocking changed HEAD or modifications by a read-only role.
7. Materialize the Markdown report and declared Markdown artifacts **only in a quarantined candidate-root under that run**. Do not overwrite production specs, previous reports or mock files.
8. Start the **single reusable Handoff Agent** in a different fresh, read-only Codex process with the producer's original result and actual staged file inventory.
9. Deterministically validate the Handoff candidate against the producer schema, identities, accepted statuses, real staged paths and content digests. Record validation.json and a final run.json state: CANDIDATE_VALIDATED or BLOCKED.

This run's **candidate validation is not technical verification or human approval**. Even UX_VERIFIED, VERIFIED or PASS is a specialist *claim* at this point. No proof and no automatic integration follow from it. run.json always has verified: false and adopted_by_coordinator: false in this MVP.

## Read-only and source-writing roles

| Stage | Codex sandbox | Source repository writes |
| --- | --- | --- |
| Discovery, Prototype, Specification, MVP Design, UIUX DESIGN, REQUIREMENTS | read-only | Not allowed |
| RED, GREEN, REFACTOR | workspace-write | Allowed **only** on the explicitly selected worktree; baseline must be clean |
| VERIFY, UIUX INSPECT, PROVE | read-only | Not allowed |
| Reusable HANDOFF (after every specialist) | read-only | Never allowed |

All Markdown reports and extra Markdown artifacts are returned through the specialist's structured JSON and materialized by the host in the isolated candidate folder. Even a read-only agent can therefore draft a report without editing the repository. Application source changes, if any, are the real Git changes in the selected worktree; the runner records whether changes occurred, and refuses unexpected Git HEAD movement. It never automatically reverts user or agent changes.

Running actual tests that need to write caches may not work with read-only; the affected specialist must return an honest INCONCLUSIVE/BLOCKED report. Configurable test-sandbox isolation belongs to the next runtime hardening milestone.

## Run artifact layout

~~~text
work/my-project/codex-runs/codex-<uuid>/
  run.json                      # real Git identity, sandbox, exit results and candidate state
  producer.json                 # original specialist metadata, canonical schema
  specialist/
    prompt.txt
    invocation.json
    events.jsonl
    stderr.txt
    last-message.json           # structured extended Codex result + Markdown body
  candidate-root/
    work/my-project/...         # staged report and declared Markdown artifacts only
  handoff/
    prompt.txt
    invocation.json
    events.jsonl
    stderr.txt
    last-message.json
  normalized-handoff.json
  validation.json
~~~

Failures preserve available logs and a BLOCKED run.json with the reason. Missing files after an interrupted or failed process must not be interpreted as success.

## Boundaries and follow-up

- Stage execution requires --confirm; --dry-run is non-mutating.
- This pilot does not read or store authentication tokens, run deployment, create GitHub Issues or push to GitHub.
- The Codex sandbox is **not** a per-file authorization system. Only RED, GREEN and REFACTOR have write permissions in the selected source worktree. A later Coordinator must independently inspect source diffs and role ownership.
- Live multi-stage orchestration, human approval gates, retries, conservative crash recovery and canonical ticket adoption are now **implemented separately** by MVP 02's `live-*` commands. This `codex-stage` pilot still does none of them; integration/push remains explicit.
- Real prototype hosting/feedback is **MVP 03** and true browser/device UI/UX inspection is **MVP 04**.

Run offline contract tests without invoking the real Codex model:

~~~bash
uv run --offline python -m unittest discover -s tests -q
~~~
