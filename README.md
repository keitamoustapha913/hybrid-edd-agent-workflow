# Hybrid → EDD multi-agent development workflow

A reusable **14-role, human-gated automatic orchestration kit**: one Coordinator Agent, one reusable Handoff Agent, four hybrid discovery/design specialists, six Evidence-Driven Development (EDD) specialists, **one dedicated UI/UX specialist** invoked twice per MVP, and **one dedicated Git Agent** for repository inspection and delivery planning. Every approved MVP follows its own UX + EDD cycle.

**Target operating mode: HUMAN_GATED_AUTO. Runtime: OpenAI Codex CLI.** The Coordinator launches eligible specialists; the Handoff Agent reconciles variable outputs before deterministic validation; a human approves product decisions and each MVP scope. **Implemented today:** the simulation dashboard, MVP 01's isolated Codex pilot, **MVP 02's durable live Coordinator**, **MVP 03's human-reviewed Codex HTML prototype**, **MVP 04's actual host test + visual evidence / G6 / G7 reviews**, **MVP 05's sequential multi-MVP scheduler**, **MVP 06's human-approved, G7-bound Git delivery**, and **MVP 07's automatic continuation between human gates**. Real post-VERIFY steps still need independent checks and human review.

**Project tracking is local-first:** one parent Markdown ticket for the initiative and a child Markdown ticket for each generated MVP. A dedicated Git integration branch collects accepted MVP code and published documentation. GitHub Issues are **optional mirrors**, not a prerequisite or source of truth.

## One-command live workflow (MVP 07)

```bash
uv run workflow.py live my-project
```

Before the first Codex run, press **I → 1** to prepare the dedicated
integration and MVP worktrees when needed. Press **N** once to authorize the
first specialist run. With **Auto-continue ON** (the default), each **A** gate
approval or **C** change request immediately resumes the next eligible Codex
stretch and stops at the next human gate. **E** still requires explicit approval
of the exact test/capture commands. **T** toggles persisted manual mode; simply
reopening the console never starts Codex.

Finish the regular human-gated UX + EDD cycle.
After a genuine human **G7** approval, press **I** for the four reviewed
delivery steps: **2 Commit → 3 Promote → 4 Snapshot → 5 Push (optional)**.
The terminal shows the active MVP, Git revision, delivery status and local
parent/sub-ticket progress. No remote write happens without a distinct human
confirmation. After the local snapshot, **M** schedules the next approved MVP
on a new branch based on the integration tip.

See [MVP 07: automatic continuation](docs/live-auto-orchestration-mvp07.md)
for exact trigger/stop semantics, and [MVP 06: Git delivery](docs/live-git-delivery-mvp06.md) for the content
integrity bridge, recovery behavior and limitations. You do not need the
individual `integration-*` CLI commands for ordinary use.

## Complete flow

~~~mermaid
flowchart TD
    D["1. Discovery Agent"] -->|"Approved discovery"| P["2. Prototype Agent"]
    P -->|"Reviewed prototype evidence"| S["3. Specification Agent"]
    S -->|"G3: product spec + UX contract"| M["4. MVP Design Agent"]
    M -->|"G5: feature + UX scope"| A["MVP-01 / spec.md + ux/spec.md"]
    M -->|"G5: feature + UX scope"| B["MVP-02 / spec.md + ux/spec.md"]
    M -->|"G5: feature + UX scope"| C["MVP-03 / spec.md + ux/spec.md"]
    A --> E1["MVP-01: UX Design → EDD → UX Inspect → PROVE"]
    B --> E2["MVP-02: UX Design → EDD → UX Inspect → PROVE"]
    C --> E3["MVP-03: UX Design → EDD → UX Inspect → PROVE"]
~~~

The MVP branches in this diagram represent **separate executions**, not automatic parallel development. Usually implement MVP-01 first, then MVP-02, then MVP-03, respecting dependencies and checking prior-MVP regressions. A human can explicitly approve skipping the prototype, with the waiver documented.

**Every development-stage arrow passes through one reusable Handoff Agent and a deterministic validator.** The Handoff Agent reconciles filenames, report layout and missing fields using observed artifacts; the validator checks safe paths, schema, identity and status. The Git Agent is a bounded read-only review whose already-structured JSON goes directly to a strict identity validator. Neither pathway can manufacture evidence or bypass human approvals.

## Coordinator + Handoff + twelve distinct specialists

| Agent | File | Output / stop condition |
| --- | --- | --- |
| 0. Coordinator | agents/00-coordinator.md | Orchestration state, approvals, dispatch and failure routing |
| 0b. Handoff | agents/00-handoff.md | Read-only normalization of each specialist's artifacts; no approval authority |
| 1. Discovery | agents/hybrid/01-discovery.md | Discovery report; human review |
| 2. Prototype | agents/hybrid/02-prototype.md | Safe experiment + report; human review |
| 3. Specification | agents/hybrid/03-specification.md | Product feature spec + separately hash-bound UI/UX contract |
| 4. MVP Design | agents/hybrid/04-mvp-design.md | Roadmap + feature and UX specs per MVP; human G5 review |
| 5. UIUX (DESIGN and INSPECT) | agents/edd/00-uiux.md | One specialist invoked twice in new sessions: pre-build design and post-VERIFY inspection |
| 6. Requirements | agents/edd/01-requirements.md | MVP-scoped functional and UX acceptance contract and IDs |
| 7. RED | agents/edd/02-red.md | Protected tests + genuine baseline failure |
| 8. GREEN | agents/edd/03-green.md | Minimal implementation passing RED tests and scoped UX behavior |
| 9. REFACTOR | agents/edd/04-refactor.md | Behavior-preserving improvement or no-op |
| 10. VERIFY | agents/edd/05-verify.md | Independent, technology-appropriate functional checks |
| 11. PROVE | agents/edd/06-prove.md | Evidence receipt, source identity, final UX + technical claim |
| 12. Git Agent | agents/git/01-git-delivery.md | Separate read-only Codex review of setup, commit, promotion, snapshot and push; exact structured handoff |

Role definitions are kept in separate files for clean handoffs and independent execution. The root **AGENTS.md** provides shared policy; agents/00-coordinator.md defines the orchestration behavior and agents/00-handoff.md defines the normalization boundary.

**Git delivery is agent-assisted:** pressing **I → 1/2/3/4/5** first launches a
fresh read-only Git Agent through Codex, displays its risk assessment and
validates the exact source/integration identity. Only a `READY` review can
reach the separate human confirmation; the deterministic Git adapter still
performs the fixed operation. No model-generated Git command, branch or file
path is executed. Every review appears in the local parent ticket.

### Dedicated UI/UX quality checkpoints

UI/UX can no longer disappear into a vague "make it look good" instruction. **G3** approves `product-spec.md` **and** `product-ux-spec.md`; **G5** approves the selected MVP's `spec.md` **and** `ux/spec.md`. Each UX contract must cover user tasks, hierarchy, design tokens, responsive variants, keyboard/focus/accessibility, all relevant empty/loading/error/success states, content and recovery behavior, with traceable `UX-NNN` / `MVP-NN-UX-NNN` acceptance IDs.

For each approved MVP, the dedicated UIUX role runs **twice in fresh sessions**: `UX DESIGN` produces an inspectable design before REQUIREMENTS/RED; `UX INSPECT` independently examines the actual interface **after VERIFY and before PROVE**, recording screenshot/device/interaction evidence and defects. It cannot change application code or approve its own findings. For a genuine release claim, UX_FAILED or INCONCLUSIVE blocks PROVE PASS; fixes must rerun VERIFY and UX INSPECT on the changed build. See [UI/UX quality gates](docs/uiux-quality-gates.md) and the [UIUX agent](agents/edd/00-uiux.md).

The current terminal runner simulates these handoffs and marks real UI checks **NOT EXECUTED**. It is a useful review of orchestration and coverage, not evidence that a real design has passed inspection.

**workflow.yaml** is a machine-readable *descriptive* phase/gate manifest. It does not launch agents by itself. For real Codex sessions use the **guided live interface** below. The granular `live-*` commands and isolated `codex-stage` pilot remain available for advanced scripting. See [MVP 01 pilot](docs/live-codex-mvp01.md), [MVP 02 Coordinator](docs/live-coordinator-mvp02.md), [MVP 03 browser review](docs/live-prototype-mvp03.md), [MVP 04 independent evidence](docs/live-evidence-mvp04.md), [MVP 05 multi-MVP execution](docs/live-multi-mvp-mvp05.md) and the [runtime contract](docs/codex-cli-adapter.md).

## Quick start — one uv command

A local Coordinator is included in **workflow.py**. The uninitialized default dashboard is a **simulation**. A separate, persistent live flow launches actual Codex sessions only after explicit authorization, uses the Handoff Agent, and never treats a model's report as independent evidence.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) once. From the extracted workflow directory, run:

~~~bash
uv run workflow.py
~~~

For a **real, human-gated Codex project**, use this **single command** instead:

~~~bash
uv run workflow.py live my-project
~~~

The live screen guides initial project/Git setup and shows the active agent, exact stage, **active MVP and its UX/EDD sub-stages**, completed MVPs, latest report, source identity, evidence state and next approval choices. **[N]** authorizes the next Codex specialist sessions; **[O]** opens the G2 prototype in your local browser; **[C]** records actionable change feedback; **[A]** approves the currently displayed, integrity-bound version; **[V]** shows the full report; **[M]** schedules the next G4-listed MVP only after the current MVP passes human G7. No SHA-256 values need to be pasted. Reopen the same live command whenever you return; after the first live initialization, the default `uv run workflow.py` startup will also resume that initiative in the live interface.

uv manages Python 3.12 from the included `.python-version`; this is a source-based project with no third-party Python dependencies or build step. The command opens a **guided startup**: choose a new or existing project, optionally enter the local Git checkout path, and explicitly opt in to creating its integration branch. It automatically maintains the parent Markdown ticket; GitHub Issues remain an optional add-on.

The guided startup then opens the terminal dashboard. Press **N** to start. The interface has color-coded panels for the stage timeline, pending decision, current report, and recent activity. Press **V** to see **each MVP's two UX checkpoints, six EDD stages, current/next stage, observed UTC timestamps and completion count**; press **P** for project tickets and integration. Use **A** to approve, **C** to request changes, **D** to view full artifact/hash details, **H** to switch to event history, **R** to retry blocked handoffs, and **Q** to quit. The report scrolls independently of the fixed approval controls. You do not need to remember the SHA-256 approval command. The prototype persists state in SQLite between sessions. At G3 and G5, inspect **both** the feature and UX specifications; changes to either block approval.

**Prototype G2 review is interactive and versioned.** After G1, the Coordinator generates `prototype-review/iteration-001/preview/index.html` (a **synthetic local demo**) and an integrity-bound review bundle. The dashboard switches to **G2 PROTOTYPE REVIEW**: press **O** to open the preview, inspect it, then **C** to record structured change feedback and automatically rerun Prototype. A new `iteration-002` preview and **renewed G2 approval** are required; old hashes cannot approve a revised preview. Press **A** to accept that iteration's reviewed findings and automatically enter Specification/G3. The project ticket records each iteration; `prototype-acceptance.md` and the product specification capture the accepted version. This currently tests the review machinery, **not Codex's ability to implement change requests**. See [the prototype review contract](docs/prototype-review-contract.md).

For a read-only MVP status screen in a noninteractive terminal:

~~~bash
uv run workflow.py tui demo-orders --snapshot --mvp-panel --width 110 --height 34
~~~

The **simulation** still dispatches UIUX + EDD only for MVP-01 and marks other MVPs UNSCHEDULED. **Real Codex live mode** uses MVP 05's sequential scheduler to run future roadmap MVPs independently; the simulation tracker does not infer real passes from files on disk.

### MVP 01 — run a real Codex specialist (opt-in)

This separate pilot is **implemented and testable**, not a simulation. It invokes a fresh Codex CLI specialist session and then the reusable Handoff Agent in a fresh read-only session, captures structured results/JSONL traces, checks expected role/run IDs and paths, and records a quarantined candidate. It does **not** mutate Coordinator approval state, local source-of-truth tickets, or integration branches.

~~~bash
uv run workflow.py init live-sample
uv run workflow.py repo-attach live-sample --path /absolute/path/to/your-clean-git-repo
# Review and edit work/live-sample/brief.md
uv run workflow.py codex-stage live-sample --stage discovery --dry-run
uv run workflow.py codex-stage live-sample --stage discovery --confirm
~~~

The runner refuses dirty worktrees and missing input contracts. RED/GREEN/REFACTOR may edit the selected worktree under Codex's workspace-write sandbox; other specialists, including UIUX and HANDOFF, use the read-only sandbox and return local Markdown artifacts through structured output. Every live run is retained under `work/<initiative>/codex-runs/<run-id>/` with the original logs, source identity, staged reports, validated handoff or explicit failure. No live run automatically approves itself or claims genuine UX verification.

See **[MVP 01 execution, artifacts and limitations](docs/live-codex-mvp01.md)** before spending real Codex usage. Run `uv run --offline python -m unittest discover -s tests -q` to verify the adapter with a fake Codex executable without invoking an AI model.

### MVP 02 — real Coordinator and human approvals

Start **`uv run workflow.py live my-project`** once, enter the path to an existing clean Git checkout when prompted, and edit `work/my-project/brief.md` with your project goal. **[N]** runs eligible fresh Codex specialists and their separate Handoff session; the interface stops at G1–G5 until **[A]** accepts the exact current report or **[C]** requests changes. The live SQLite ledger and local parent/MVP tickets update across restarts. G3 and G5 also bind the separately generated UI/UX contracts by hash.

### MVP 03 — preview, feedback and renewed G2

At **G2_PROTOTYPE** choose **[O] Open preview**, actually test the browser experience, then **[C] Changes** or **[A] Approve**. Changes produce a fresh Codex Prototype session, numbered HTML preview, and **renewed G2**; the old approved hash cannot be reused. G2 approval asks what you personally observed and carries the accepted experimental findings into Specification, not production code. The browser preview uses the local loopback server and synthetic data. See [MVP 03 review safeguards](docs/live-prototype-mvp03.md).

### MVP 04 — collect evidence without command-line flags

After UX DESIGN → REQUIREMENTS → RED → GREEN → REFACTOR → VERIFY, the live screen pauses at **AWAITING_EVIDENCE**. Choose **[E] Collect evidence**. On the **first** run only, answer three short prompts:

1. The real functional test command for your checked-out project.
2. The real UI/UX journey/capture command that produces a screenshot.
3. The screenshot's relative output path.

These are stored in `work/my-project/evidence-plan.json`. For subsequent runs, **[E]** reuses the same plan; **[F]** lets you change it if the project's checks change. The interface shows the exact commands and requires **[Y]** every time before executing them. It uses a disposable source snapshot, captures logs/screenshots and produces source-bound receipts. **The snapshot is not an OS security sandbox: configure only commands you trust.** A successful test or screenshot is not by itself proof of UX quality.

When checks pass, the terminal **automatically** launches the independent UIUX INSPECT specialist when Auto-continue is ON (otherwise press **[N]**). **[A]** at G6 requires your actual review of the screenshots, inspection report and limitations; approval likewise starts PROVE if Auto-continue is ON, but its G7 proof still requires another explicit **[A]** and review notes. **[C]** at G6/G7 can send work back to GREEN or to new evidence, invalidating stale checks and requiring renewed inspection. No push, deployment or GitHub Issue write happens automatically.

See [the detailed MVP 04 integrity and verification contract](docs/live-evidence-mvp04.md).

### MVP 05 — schedule and track the next live MVP

After **G7_PROVE** approves MVP-01, the live screen offers **[M] Next MVP** if the human-approved `mvp-roadmap.md` names **MVP-02**. Confirm scheduling; with Auto-continue ON, a fresh MVP DESIGN/Handoff pair automatically creates MVP-02's feature and UI/UX scope (if those two files were not generated upstream) and pauses at **G5_MVP_02**. When Auto is OFF, press **[N]** instead. **[A]** at G5 must approve both scope files before any new EDD specialist runs. The next MVP then gets separate UX DESIGN, REQUIREMENTS, RED, GREEN, REFACTOR, VERIFY, host evidence, UX INSPECT/G6 and PROVE/G7.

The parent ticket now lists the active and completed MVPs; each child retains its own reports, approvals, receipts and notes. MVP-01's evidence stays bound to its original source; MVP-02 verification must include the relevant regression behavior. Evidence commands are configured separately for each MVP after the first. The scheduler runs **one MVP at a time in roadmap order**: no automatic Git merge/push, deployment, arbitrary dependency graph, or parallel worktree dispatch. See the [MVP 05 execution and limits](docs/live-multi-mvp-mvp05.md).

### MVP 07 — automatic continuation after human decisions

**[A]**, **[C]**, successful confirmed **[E]** evidence, and **[M]** next-MVP scheduling now resume exactly the next eligible Codex specialists without repeatedly pressing **[N]**. Every human gate (including renewed G2 previews and each G5/G6/G7) remains a hard stop. **[T]** toggles the project's persisted auto mode; it is ON by default but cannot dispatch just because you opened the terminal. Git Agent reviews, integration commits, pushes, evidence command execution and future MVP scheduling are **never** automatic. See [the MVP 07 state and authorization contract](docs/live-auto-orchestration-mvp07.md).

For scripted or noninteractive use, keep the more granular commands (for example, `uv run workflow.py status demo-orders`). Use `uv run workflow.py --help` for the full list. The guided screen requires an interactive terminal and does not run in CI.

### Attach to a real Git checkout

The Coordinator now has a **local Git-aware adapter** (repository inspection, source identity, registered worktrees, explicit MVP worktree creation/selection and revision-bound gate checks). Keep this kit outside the checkout. The guided startup prompts for your Git path; alternatively start an explicit project dashboard:

~~~bash
uv run workflow.py tui demo-git --repo /absolute/path/to/your-repo
~~~

In the dashboard: **G** opens Git details; **W** creates an isolated MVP branch/worktree after confirmation; **S** selects an existing registered worktree; **B** attaches another checkout before a workflow starts. A dirty or changed branch blocks simulation advancement, and a changed commit or dirty worktree invalidates Git-bound approval. **The dashboard's specialist sessions remain simulated; only the explicit `codex-stage --confirm` command launches live Codex.**

To resume the saved initiative on another day, omit --repo: start the same tui command with only the initiative ID; its Git binding is loaded from SQLite.

To inspect from a noninteractive terminal:

~~~bash
uv run workflow.py repo-status demo-git
uv run workflow.py tui demo-git --snapshot --git-panel
~~~

See **prototype/README.md** for initialization, worktree paths, base-ref overrides, safety rules, and the full keyboard reference.

### Local parent ticket, MVP sub-tickets, and integration

The Coordinator automatically maintains these files, **without any GitHub connection**:

~~~text
work/demo-git/
  project-ticket.md                   # Parent: stage progress, gates, plans, reports, MVP index
  discovery.md
  product-spec.md
  product-ux-spec.md                 # G3-bound UX acceptance contract
  mvp-roadmap.md
  mvps/
    mvp-01/
      ticket.md                       # Sub-ticket: scope, EDD status, report links and hashes
      spec.md
      ux/
        spec.md                      # G5-bound MVP UX scope
        design.md                    # UIUX DESIGN output
        design-review.md
        inspection.md                # UIUX INSPECT output
      edd/
        requirements.md
        red.md
        green.md
        refactor.md
        verify.md
        prove.md
~~~

Each ticket has a generated section that the Coordinator refreshes after state transitions and a separate **Human notes** section that is preserved. The parent tracks discovery, prototype findings, product + UX specs, roadmap, MVP scopes and progress. Each MVP ticket tracks **two UI/UX handoffs plus six EDD handoffs**, stage status, observed timestamps, review state and artifact hashes. The MVP tracker and local tickets distinguish valid simulated handoffs from unscheduled stages and unsupported claims.

Create one shared integration branch/worktree for the initiative, then an MVP worktree based on its HEAD:

~~~bash
uv run workflow.py init demo-git
uv run workflow.py repo-attach demo-git --path /absolute/path/to/checkout
uv run workflow.py integration-create demo-git
uv run workflow.py worktree-create demo-git --mvp mvp-01
uv run workflow.py project-status demo-git
uv run workflow.py tui demo-git --snapshot --project-panel
~~~

The shared branch is named **agentflow/demo-git/integration**. For real committed MVP work, explicit integration operations support only a **fast-forward** from a clean MVP worktree; divergent histories require review. You can then snapshot the local Markdown artifacts into a controlled docs directory, commit them to integration and non-force push it to a configured origin:

~~~bash
uv run workflow.py integration-promote demo-git --mvp mvp-01 --source /absolute/path/to/mvp-worktree
uv run workflow.py integration-snapshot demo-git --confirm
uv run workflow.py integration-push demo-git --confirm
~~~

Review reports for secrets/private information before publishing them. The simulated EDD workflow **does not automatically commit or push code as if PROVE had verified it**. The live MVP 06 terminal now exposes these same Git boundaries **after actual revision-bound G7 proof** with separate human confirmations; the lower-level CLI sequence above is optional for advanced users.

In the TUI, **P** opens project tracking: **I** creates integration, **J** promotes a committed MVP, **F** snapshots local reports, and **O** pushes integration. The parent ticket and sub-tickets are maintained automatically regardless of GitHub.

**Optional GitHub mirror:** when the origin is a matching GitHub repository and GitHub CLI is authenticated, you can add a native parent issue and linked MVP sub-issues and post status comments. This never replaces the local files:

~~~bash
uv run workflow.py tracker-create demo-git --repo OWNER/REPO --confirm
uv run workflow.py tracker-mvp demo-git --mvp mvp-01 --confirm
uv run workflow.py tracker-sync demo-git --confirm
~~~

The TUI offers the same optional actions under **P → T/M/U**. GitHub writes are deliberate, never triggered merely by running a mock specialist.

For a read-only, non-interactive snapshot after initialization:

~~~bash
uv run workflow.py tui demo-orders --snapshot
~~~

The original command-based interface remains available:

~~~bash
uv run workflow.py init demo-orders
uv run workflow.py run demo-orders
uv run workflow.py status demo-orders
~~~

The status command displays the pending human gate and exact SHA-256 hash to review. Approve using the displayed hash, then the next mock agent starts automatically. See **prototype/README.md** for the full walkthrough, retries, and simulated invalid handoffs.

## Setup

1. Copy AGENTS.md, agents/, and templates/ into your project. If your project already has AGENTS.md, **merge** shared rules; do not erase existing repository instructions.
2. Create work/<initiative-id>/brief.md from templates/initiative-brief.md.
3. Configure an OpenAI Codex CLI runner that supports separate specialist sessions, the read-only Handoff Agent, and a trusted human approval channel (docs/codex-cli-adapter.md). Initialize work/<initiative-id>/orchestration-state.yaml from templates/orchestration-state.yaml.
4. Launch the **Coordinator** with AGENTS.md, agents/00-coordinator.md, workflow.yaml, and the initiative brief.
5. The Coordinator dispatches Discovery → Prototype (or documented waiver) → Specification → MVP Design, **pausing for human approvals** at the required checkpoints.
6. Approve the roadmap and **the feature + UX specifications for one MVP**. The Coordinator dispatches UIUX DESIGN → the six EDD specialists with UIUX INSPECT between VERIFY and PROVE, invoking Handoff/deterministic checks after each specialist and routing failures to their owners.
7. Examine PROVE: PASS, FAIL, or INCONCLUSIVE. The next MVP is eligible only if its own scope is already approved and its dependencies are satisfied. Release remains a separate human decision.

Example:

~~~
work/orders-export/
  brief.md
  project-ticket.md                   # Local-first project status, artifact index and approvals
  approvals.md
  orchestration-state.yaml
  orchestration-events.jsonl
  handoffs/
  discovery.md
  prototype-report.md
  product-spec.md
  product-ux-spec.md
  mvp-roadmap.md
  mvps/
    mvp-01/
      ticket.md                       # Auto-updated local MVP sub-ticket
      spec.md
      ux/
        spec.md
        design.md
        design-review.md
        inspection.md
        evidence/
      edd/
        requirements.md
        red.md
        green.md
        refactor.md
        verify.md
        prove.md
        evidence/
    mvp-02/
      spec.md
      edd/...
~~~

Implementation code and tests remain in their **normal repository directories**; the edd/ files are audit handoffs, not source-code copies.

## Per-MVP gate checklist

| Gate | What needs to be true | Who acts |
| --- | --- | --- |
| Discovery → Prototype | Discovery scope/experiment explicitly approved | Human (G1) |
| Prototype → Specification | Evidence reviewed, or documented waiver | Human (G2) |
| Product spec → MVP Design | Product feature + UI/UX contracts explicitly approved by hash | Human (G3) |
| Roadmap review | Roadmap explicitly approved | Human (G4) |
| MVP specs → UX DESIGN | Specific MVP **feature and UX scope** hash-approved | Human (G5 per MVP) |
| UX DESIGN → REQUIREMENTS | Inspectable design, flow/state map and mapped UX IDs | Dedicated UIUX Agent (DESIGN session) |
| REQUIREMENTS → RED | Normalized handoff valid; frozen criteria match approved MVP spec | Handoff + validator + Coordinator |
| RED → GREEN | Genuine expected failure demonstrated, or exception explained | Coordinator |
| GREEN → REFACTOR | Focused tests pass; protected tests unchanged | Coordinator |
| REFACTOR → VERIFY | No regressions; intentional no-op allowed | Coordinator |
| VERIFY → UX INSPECT | Real technical acceptance/regression evidence for final build | Independent VERIFY |
| UX INSPECT → PROVE | Actual interface/accessibility/responsive evidence, UX_VERIFIED or approved N/A | Dedicated UIUX Agent (new INSPECT session) |
| PROVE → next MVP | PASS for current MVP, dependencies satisfied, next MVP approved | PROVE + Human |

**The Prototype Agent's quick experiment is not the EDD RED/GREEN implementation.** Prototype findings influence product decisions; final implementation is rebuilt or consciously reviewed under an approved MVP contract. EDD proofs are per-MVP, tied to tested source identity, and never deployment permission.

## Example agent invocation

**UIUX DESIGN for MVP-01 (before Requirements):**

> Act only as UIUX Agent in DESIGN mode. Read AGENTS.md, agents/edd/00-uiux.md, G3-approved product-ux-spec.md, G5-approved MVP-01/ux/spec.md and the feature spec. Produce only ux/design.md and ux/design-review.md with screen/state matrices, layout and interaction details and mapped UX IDs. Do not edit implementation or silently change approved scope.

**Requirements for MVP-01:**

> Act only as the EDD REQUIREMENTS Agent. Read AGENTS.md, agents/edd/01-requirements.md, approved work/orders-export/product-spec.md and product-ux-spec.md, approved MVP-01/spec.md and ux/spec.md, the UIUX DESIGN handoff, and relevant repository context. Produce edd/requirements.md with traceable functional and UX acceptance criteria. Stop if MVP scope requires a new human decision. Do not implement or write RED tests.

**RED for MVP-01:**

> Act only as the EDD RED Agent. Read AGENTS.md, agents/edd/02-red.md, the approved MVP-01 spec, its frozen edd/requirements.md, and the repository. Write only tests/fixtures plus work/orders-export/mvps/mvp-01/edd/red.md. Prove a genuine pre-implementation failure, record exact commands/exit status, and stop. Do not fix implementation code.

After VERIFY, invoke UIUX in **INSPECT mode in a fresh session** to record actual UI evidence before PROVE. Replace the role name/instruction, input handoffs, and output path to invoke GREEN, REFACTOR, VERIFY, UIUX INSPECT, and PROVE in separate sessions. Use the same method for the four hybrid roles. **One session reading all role files is not independent specialist execution.**

For every such specialist session, invoke agents/00-handoff.md as a **separate read-only Codex session**. Use schemas/handoff.schema.json for its final output and scripts/validate_handoff.py to check that output against the producer's structured result and actual project files. An invalid or ambiguous handoff returns to the producing specialist; it never silently advances.

## Templates

- templates/initiative-brief.md — starting user intent.
- templates/product-ux-spec.md — required product-level UX criteria, approved with product spec at G3.
- templates/mvp-ux-spec.md — per-MVP UX scope/acceptance, approved with feature spec at G5.
- templates/uiux-inspection.md — independent actual UI/UX inspection evidence and defects.
- templates/mvp-edd-requirements.md — per-MVP acceptance contract.
- templates/edd-handoff.md — common role execution report.
- templates/edd-proof.md — per-MVP PROVE receipt.
- templates/approval-log.md — human-sourced approval history.
- templates/approval-request.md — ready-to-review gate request.
- templates/orchestration-state.yaml — durable Coordinator progress state.
- docs/runtime-adapter.md — required launcher, approval, state and evidence interfaces.
- docs/codex-cli-adapter.md — concrete Codex CLI invocation design.
- schemas/specialist-result.schema.json — consistent specialist final responses.
- schemas/handoff.schema.json — normalized handoff contract.
- scripts/validate_handoff.py — deterministic identity, path, status and schema checks.
- tests/test_validate_handoff.py — failure and valid-handoff test cases.
- prototype/agentflow.py — executable mock Coordinator with SQLite, approval pauses, and `tui` entrypoint.
- prototype/codex_runtime.py — real isolated Codex specialist/Handoff runner with quarantined candidates.
- prototype/live_coordinator.py — MVP 02 durable live stage scheduler, hash-bound approvals, adoption and recovery.
- prototype/live_prototype.py — MVP 03 frozen Codex HTML preview, local loopback server, feedback/approval integrity.
- docs/live-prototype-mvp03.md — real G2 reviewer journey, CLI, hashes and security boundaries.
- docs/live-coordinator-mvp02.md — complete live CLI walkthrough and explicit remaining evidence/preview gaps.
- prototype/mvp_progress.py — per-MVP scope/EDD status model grounded in workflow events and explicit approvals.
- prototype/dashboard.py — terminal dashboard, preview renderer, and keyboard-driven actions.
- prototype/git_adapter.py — real local Git inspection and explicitly requested worktree creation.
- prototype/local_tickets.py — deterministic local project/MVP progress tickets with preserved human notes.
- prototype/project_artifacts.py — explicitly reviewed Markdown snapshots committed into the integration worktree.
- prototype/github_adapter.py — optional GitHub native parent/sub-issues and progress comments.
- prototype/README.md — dashboard walkthrough, optional commands, and clear simulation limitations.
- tests/test_agentflow_prototype.py, tests/test_dashboard.py, tests/test_git_adapter.py, tests/test_git_integration.py and tests/test_project_tracking.py — workflow, dashboard, Git/worktrees, local tickets, integration promotion/push, optional GitHub mirrors and recovery tests.

You can test the deterministic handoff checks locally with: **uv run python -m unittest discover -s tests -v** (from the workflow directory). This tests the validator, not the entire Codex scheduler.

## When checks fail

- Invalid tests → return to RED (not GREEN editing RED tests).
- Actual behavior fails → return to GREEN, reconsider REFACTOR, rerun VERIFY, then PROVE.
- Requirement changes → reopen the human-approved source-of-truth artifact and repeat affected phases.
- Inadequate or stale evidence → return to VERIFY; never mark incomplete evidence as PASS.

No workflow claims completeness merely because the agents returned a success message.

## Automatic execution boundaries

- The Coordinator may advance from a **validated** RED_CONFIRMED to GREEN and through the remaining EDD stages without asking you every time.
- The Handoff Agent is mandatory after each specialist; it can normalize observed information but cannot change the original acceptance contract, test results, or source files.
- It must pause for the specified human approval gates, a material requirements change, an unsafe or production-facing action, or a missing/ambiguous approval.
- VERIFY FAILED routes to implementation repair within a bounded retry policy; missing/stale evidence routes back to VERIFY. After retries are exhausted, it blocks or requests human direction.
- Prompt files, YAML, schemas, and the validator alone are *not* a running scheduler. MVP 02 supplies the durable **live Coordinator**; MVP 03 adds genuine Codex-generated **offline interactive HTML** prototype review; MVP 04 adds independently executed host test/image receipts and dedicated UIUX/G6/G7 review; MVP 05 handles sequential roadmap MVPs. Real app-backend hosting, automated UX grading, arbitrary dependency-DAG dispatch and release automation are not implemented.
# hybrid-edd-agent-workflow
