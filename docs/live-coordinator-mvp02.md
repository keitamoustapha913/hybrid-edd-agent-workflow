# MVP 02 — live human-gated Codex Coordinator

> **Update after MVP 03:** This document explains the original MVP 02 milestone.
> A newer `live-preview` command now opens the real, Codex-generated offline
> HTML prototype. New G2 approvals use `--bundle-sha` and `--review-notes`,
> not the preview waiver shown in this historical MVP 02 walkthrough.
> See [MVP 03 real prototype review](live-prototype-mvp03.md).
>
> **Update after MVP 04:** Host-executed independent tests and screenshots,
> a real read-only UIUX INSPECT session, and human G6/G7 reviews are now
> available with `live-evidence`. The `AWAITING_EVIDENCE` barrier below is
> intentional, but no longer a permanent end of the live workflow.
> See [MVP 04 evidence and UX continuation](live-evidence-mvp04.md).

**Implemented:** durable live Coordinator in `prototype/live_coordinator.py`; Codex specialist + separate read-only Handoff from MVP 01; exact-path/hash validation; conservative canonical adoption; human G1–G5 gates; change feedback and reruns; Git fingerprint checks across dirty-but-owned RED/GREEN/REFACTOR source changes; local parent/MVP ticket updates; interruption reconciliation.

**Not implemented in the original MVP 02 release:** real prototype preview hosting and review (now added by MVP 03), independent test/browser/UX evidence collection and real release proof (later MVPs), dynamic execution for MVP-02/MVP-03 product scopes, automatic commit/integration/push or PR creation, authenticated human approvals.

The existing `run`, `approve`, `status` and TUI commands remain **simulation only**. To start real Codex sessions, use the separate `live-*` commands. Both modes use the same initiative directory but separate SQLite state/approval/event tables; start live mode only before the simulation begins.

## Start with a real Git worktree

The installed Codex CLI must be authenticated, support the MVP 01 `codex exec` flags, and run in the chosen working environment. Each specialist and Handoff launch consumes real model usage. No credentials are stored in this kit.

~~~bash
uv run workflow.py init storefront
uv run workflow.py repo-attach storefront --path /absolute/path/to/checkout
uv run workflow.py integration-create storefront
# Optional: create/select the working branch before live-init:
uv run workflow.py worktree-create storefront --mvp mvp-01
~~~

Fill out `work/storefront/brief.md` with the actual project description. The selected Git worktree must be on a named branch and be **clean when starting**. The initiative `work/` directory must remain outside the source repository.

~~~bash
uv run workflow.py live-init storefront
uv run workflow.py live-status storefront
uv run workflow.py live-run storefront --confirm
~~~

Live-run reserves a unique run ID **in SQLite before launching Codex**, then invokes a fresh specialist and separate fresh read-only Handoff and deterministically rechecks their materialized candidate files. Only validated outputs are adopted to canonical `work/storefront/...` Markdown paths. Source-writing roles edit the selected worktree; the Coordinator fingerprints all changes, then allows subsequent stages to run on the **exact** post-stage fingerprint without silently insisting the Git worktree is clean. It does **not** auto-commit or push those edits.

The first successful run pauses at G1. Review the actual report and its displayed SHA-256:

~~~bash
uv run workflow.py live-status storefront
uv run workflow.py live-approve storefront --gate G1_DISCOVERY --sha COPY_EXACT_SHA --continue
~~~

`--continue` explicitly authorizes the next automatic stretch of paid Codex runs up to the next gate. Without it, approval only records the human decision; use `live-run --confirm` separately to resume.

## Human gates and change requests

| Gate | Exact material to review | Action on approval |
| --- | --- | --- |
| G1 DISCOVERY | `discovery.md`, repository binding | Start Prototype |
| G2 PROTOTYPE | `prototype-report.md` | **No real preview yet**. An explicit, reasoned preview waiver is required. |
| G3 PRODUCT SPEC | `product-spec.md` and separately SHA-bound `product-ux-spec.md` | Start MVP Design |
| G4 ROADMAP | `mvp-roadmap.md` | Proceed to MVP-01 scope review |
| G5 MVP-01 | `mvps/mvp-01/spec.md` and separately SHA-bound `mvps/mvp-01/ux/spec.md` | Start UX DESIGN then EDD |

**Historical MVP 02 G2 limitation:** MVP 02 did not launch the specialist's prototype in a browser. It never fabricated an HTML preview, acceptance testing, or preview hash. Existing MVP 02 workflows without a preview can still use an explicit risk waiver:

~~~bash
uv run workflow.py live-approve storefront --gate G2_PROTOTYPE --sha COPY_EXACT_SHA --waive-preview --reason "I reviewed these findings; no interactive prototype was available." --continue
~~~

The legacy Coordinator generated `prototype-acceptance.md` with the report digest, source HEAD, exact waiver and the absence of real preview evidence. Specification cannot run without this file. **New MVP 03 runs instead require an actual Codex-produced HTML preview and a report/preview/bundle-bound G2 review**. The **simulation** still illustrates a separate synthetic HTML example, which is not interchangeable with the live Codex evidence.

At any pending G1–G5, request changes:

~~~bash
uv run workflow.py live-changes storefront --reason "Improve empty/error states and keyboard focus behavior."
uv run workflow.py live-run storefront --confirm
~~~

The Coordinator writes an immutable feedback Markdown file under `work/storefront/live-feedback/`, returns to the corresponding specialist, includes that feedback in the next Codex prompt, and requests a **new approval** after its replacement report is adopted. For G5 feedback, it reruns MVP Design, revokes the earlier G4 authorization, and pauses for renewed G4 and G5 approvals. Prior output revisions remain in `work/storefront/live-revisions/<run-id>/` when overwritten. Older gate decisions and rejected runs remain auditable.

Approving a changed report or changed companion UX spec with a stale digest is rejected. Approved contracts are continually rechecked, as are the selected branch, HEAD and content-bound Git fingerprint.

## MVP-01 automatic sub-stages

Once G5 is approved, eligible specialists run in order:

1. **UIUX DESIGN** — drafts UX design + design review.
2. **REQUIREMENTS** — MVP functional and UX acceptance IDs.
3. **RED** — tests, claimed expected failure and source diff.
4. **GREEN** — implementation and source diff.
5. **REFACTOR** — code cleanup or documented no-op.
6. **VERIFY** — produces a claimed verification report and then **stops**.
7. **UIUX INSPECT / PROVE** — **not automatically dispatched in MVP 02** because independent test and visual evidence are not yet integrated.

The terminal `live-status` and local MVP child ticket show these stages separately. A validated Handoff proves only output identity/schema/paths and that a structured claim was made; it does **not** prove a test actually ran, that an interface was visually inspected, or that a release is safe. After VERIFY the status is `AWAITING_EVIDENCE`, not `PASS`. Source review, test execution receipts and UX inspection belong to the next milestones.

## Failure and restart safety

~~~bash
uv run workflow.py live-status storefront
uv run workflow.py live-reconcile storefront
uv run workflow.py live-retry storefront
uv run workflow.py live-run storefront --confirm
~~~

- A failed Handoff/specialist result becomes `BLOCKED`; retry requires explicit human review, has a bounded attempt budget, and never starts a new model session by itself.
- If the host crashes while a Codex subprocess might still be running, the reserved run is `RUNNING` or `UNCERTAIN`. **Never replay automatically.** Inspect the source tree, `codex-runs/<run-id>/`, and processes. `live-reconcile` can finalize only a terminal, validated run or a terminal blocked run.
- Adoption uses a run-specific content-addressed manifest, preflights **all** destinations, preserves earlier live-owned file revisions and refuses to replace user-authored or simulated reports. A partial adoption can resume from the previously recorded manifest if files match their expected bytes.
- Unexpected worktree changes, moved HEAD/branch, a mutated approved spec or a changed canonical report block further dispatch.
- Manual resolution of a still-uncertain running process is intentionally **not** automated; this kit will not kill processes or discard user changes to clear the state.
- All GitHub Issue creation and status mirroring are **optional explicit commands**. No live command pushes, merges, commits, deploys or creates PRs.

## Where reports live

~~~text
work/storefront/
  agentflow.sqlite3                       # includes separate live_workflow and live_* audit tables
  project-ticket.md                       # additional live-managed section; human notes preserved
  discovery.md
  prototype-report.md
  prototype-acceptance.md                  # explicit G2 waiver (not a fabricated preview)
  product-spec.md
  product-ux-spec.md
  mvp-roadmap.md
  live-feedback/<uuid>.md                  # exact change request fed to rerun specialist
  live-revisions/<codex-run-id>/...        # previous live-owned bytes when replaced
  codex-runs/<codex-run-id>/
    run.json
    adoption-manifest.json
    specialist/...
    handoff/...
    candidate-root/...
    producer.json
    normalized-handoff.json
    validation.json
  mvps/mvp-01/
    spec.md
    ticket.md
    ux/spec.md
    ux/design.md
    ux/design-review.md
    edd/requirements.md
    edd/red.md
    edd/green.md
    edd/refactor.md
    edd/verify.md
~~~

The live CLI gates are **local command approvals**, not authenticated human signatures. Source-write Codex sandboxes do not enforce per-file ownership, and tests may write caches the read-only VERIFY sandbox disallows. Both require additional hardening before unattended production use.

## Local non-model tests

~~~bash
uv run --offline python -m unittest discover -s tests -q
~~~

The test suite uses a fake Codex executable to exercise dispatch, handoff, adoption, gates, worktree drift, change rerun, retry and crash reconciliation. These tests exercise the **Coordinator code**; they do not claim the actual generated app has been verified.
