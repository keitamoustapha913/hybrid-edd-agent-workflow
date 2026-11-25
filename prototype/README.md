# Runnable Coordinator terminal dashboard (simulation and guided real Codex)

**Recommended for real work:** open **`uv run workflow.py live my-project`** once.
This initializes/resumes a live project, asks for its Git checkout when needed,
and provides keyboard actions **[N]** initially run specialists, **[A]** approve the
current bound report, **[C]** request changes, **[O]** open the G2 preview,
**[V]** read the current report, **[E]** run host evidence checks, and **[F]**
configure the test/screenshot plan. The first **[E]** asks for the real
functional test, UI/UX capture command and expected screenshot path **once**;
these are saved locally under `work/<initiative>/evidence-plan.json`. Future
evidence collection reuses that plan after your explicit confirmation.
The specialized `live-*` CLI remains for automation, not the daily workflow.

**MVP 07 update:** **Auto-continue ON** is shown in the live panel by default.
After a confirmed **A** approval, **C** change request, successful explicitly
authorized **E** evidence collection, **M** next-MVP scheduling, or **R** retry,
the Coordinator launches only eligible Codex specialists and separate Handoff
sessions until the next human/evidence/failure barrier. **T** toggles persistent
manual mode; **N** manually launches the next stretch when auto is OFF.
Reopening the terminal, turning auto ON, or accepting G7 never implicitly
starts a session or Git operation. See `docs/live-auto-orchestration-mvp07.md`.

**MVP 06 update:** The same **[I] Git delivery** menu prepares integration
and a dedicated MVP worktree before Codex starts. Once a human G7 is approved,
it provides separate confirmations for **[2] Commit approved source → [3]
Promote (fast-forward only) → [4] Snapshot local Markdown → [5] Push
(optional, non-force)**. **[6]** inspects an interrupted source commit. Git
status and per-MVP delivery progress appear in the live panel and local parent
and sub-tickets. After an integration snapshot, **[M]** creates the next
roadmap-approved MVP worktree from integration HEAD. See
`docs/live-git-delivery-mvp06.md`. No extra CLI command is necessary.

**MVP 05 update:** Once an MVP has a human-reviewed G7 proof, the same live
screen offers **[M] Next MVP** if the G4-approved roadmap names the next
sequential `MVP-NN`. A fresh read-only MVP DESIGN/Handoff pair generates
missing feature/UX scope (or reuses already prepared specs); **G5** reviews
both. The next MVP then runs its own complete UIUX/EDD/evidence/G6/G7 cycle
and is tracked separately in the local parent and child tickets. Evidence
plans are per-MVP. No automatic push, integration promotion or deployment is
part of [M]. See `docs/live-multi-mvp-mvp05.md`.

**MVP 02 update:** The `live-init` / `live-run --confirm` / `live-status` /
`live-approve` / `live-changes` / `live-retry` / `live-reconcile` commands now
operate a separate **live** Codex Coordinator with human-gated dispatch, validated
canonical adoption, local-ticket updates and restart safety. The `tui`
subcommand still shows the **simulation**, but the guided `live` command
shows real progress. MVP 03–05 subsequently added prototype hosting,
independent evidence/UX proof and sequential MVP scheduling. See
`docs/live-coordinator-mvp02.md`.

**MVP 03 update:** `live-preview <initiative> --open` now serves the actual
Codex-generated, frozen offline HTML prototype over loopback. `live-status`
shows the new numbered iteration, preview hash and review-bundle hash. Use
`live-changes --reason` to rerun a new Codex Prototype session; G2 acceptance
now requires `--sha`, `--bundle-sha` and `--review-notes`. The **simulation TUI
still uses a synthetic preview**, not this real Codex iteration. See
`docs/live-prototype-mvp03.md`.

**MVP 04 update:** After real Codex VERIFY, the guided interface's **[E]** action
runs **explicitly human-selected functional and UX-capture commands** in an
ephemeral source snapshot, freezes their logs and screenshots and records
source-bound SHA-256 receipts. When Auto is ON, successful evidence immediately
starts the real read-only UIUX INSPECT specialist and Handoff; otherwise press
**[N]**. The Coordinator pauses at **G6** for human evidence review via
**[A]**. A fresh PROVE specialist likewise pauses at **G7**, and only
human approval sets local `COMPLETED`. The commands are trusted user-supplied
processes, not an OS sandbox or an automatic UX grading engine. The default
TUI is simulated for projects without initialized live state, and resumes the
live interface for existing live projects. See `docs/live-evidence-mvp04.md`.

This is an **executable preview** of the human-gated 14-role architecture (including a dedicated UIUX specialist with two sessions per MVP and a live-only read-only Git Agent). The mock TUI exercises persistent Coordinator transitions, SHA-256-bound local approvals, simulated specialist/handoff results and deterministic validation; the Git Agent runs only in the real live terminal.

**The default terminal/dashboard flow does not launch Codex, build application code, run real RED tests, or independently verify software.** Even when its mock PROVE stage returns PASS, the final state is clearly SIMULATED_COMPLETE. A *separate* opt-in `codex-stage` CLI command now launches one live specialist and a read-only Handoff Agent, placing all candidate results in an isolated folder without advancing the simulated workflow. See docs/live-codex-mvp01.md.

**Local-first project management:** initialization creates work/<initiative>/project-ticket.md; MVP DESIGN creates work/<initiative>/mvps/mvp-NN/ticket.md. These files automatically update after each Coordinator transition, with hashes and links to the generated local plans, specifications and reports. Human notes outside the managed section remain untouched. No GitHub account or remote is necessary.

## Start the interactive dashboard (recommended)

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then in a terminal, from the extracted workflow directory:

~~~bash
uv run workflow.py
~~~

This **one command opens guided startup**: choose an existing project (or type a new project ID), optionally attach a local Git repository/worktree, and optionally approve creation of a local integration branch. A new initiative gets its local parent ticket automatically. A new project's dashboard is a **simulation**, and does not run anything until you press **N** or **Enter**. For an initiative with an initialized live state, the same command opens the **real Codex guided interface**. To opt into real Codex immediately use **`uv run workflow.py live my-project`**.

uv manages the Python version pinned in `.python-version` (3.12). The project runs directly from source with zero external Python packages, so it does not need an editable install, setuptools, or a build step. If you prefer an explicit ID, `uv run workflow.py tui demo-orders` still works; the long commands below are **optional scripting/diagnostic controls**.

The dashboard uses a fixed-height, color-coded layout: progress and state at the top; a **13-stage workflow tracker** (windowed around the active step on shorter terminals) on the left; a review card and scrollable report on the right; recent activity and actions at the bottom. Press **V** for the nested **MVP / UX / EDD tracker**: scope approval, **UX DESIGN → REQUIREMENTS → RED → GREEN → REFACTOR → VERIFY → UX INSPECT → PROVE**, next/blocked step, UX 0/2 and EDD 0/6 completion counts, observed UTC times and links to local tickets. Narrow terminals switch to stacked cards.

### UX design and inspection quality gates

After accepting the G2 prototype, the simulated Specification Agent writes **both** `product-spec.md` and a `product-ux-spec.md` example. **G3 is hash-bound to both**: changing the UX file blocks the G3 approval. MVP Design then writes both the feature spec and `mvps/mvp-01/ux/spec.md`; **G5 binds both**, so a changed UX scope also invalidates G5. These are human review contracts, not optional attachments.

After G5, the same **dedicated UIUX role** runs in two separate simulated invocations: **UX DESIGN** (writes `ux/design.md` and `ux/design-review.md` before REQUIREMENTS) and **UX INSPECT** (writes `ux/inspection.md` after VERIFY, before PROVE). The independent inspector must examine visual hierarchy, responsiveness, all important states, keyboard/focus/accessibility, messaging and recovery against the approved UX IDs. Real Codex execution would collect screenshots/interaction logs and block PROVE on UX_FAILED or INCONCLUSIVE; **the included mock does not run a browser or claim UX_VERIFIED**. See `docs/uiux-quality-gates.md`.

## Prototype change requests and renewed G2 review

When G1 Discovery is approved, the next mock role generates a **local HTML preview** plus a versioned G2 review bundle. The G2 dashboard automatically shows the iteration ID, preview path, bundle hash, integrity and previous change-request notes.

1. At **G2_PROTOTYPE**, press **O** to open the preview in your browser (`work/<id>/prototype-review/iteration-NNN/preview/index.html`). It is a synthetic order-search/export sample; search for `1002`, export CSV and judge the demo manually. If the system cannot launch a browser, open the displayed local file path yourself.
2. Press **C** to request changes. Enter the intended behavior and acceptance signal. The Coordinator writes immutable `feedback.json`/`feedback.md` for the reviewed iteration, marks it `CHANGES_REQUESTED`, and reruns the mock Prototype Agent. No old preview is overwritten.
3. A **new iteration, preview and G2 gate** appear. The new mock HTML displays your previous feedback as a reviewer note, **not as a claim that Codex implemented it**. Press **O** to inspect this new file. You may keep requesting changes.
4. Press **A** only when you accept the reviewed findings. Approval requires matching hashes for the current report, HTML, manifest, checks, previous feedback and review bundle, plus a valid Git worktree/HEAD if configured. An old approval hash is rejected.
5. The Coordinator records `prototype-acceptance.md` and `approval.json`, updates the local parent ticket's prototype iteration table, and automatically executes the simulated Specification Agent, pausing at **G3_PRODUCT_SPEC**. The spec records the accepted prototype evidence; experiment code does **not** become production code.

For a **read-only terminal preview**, once G2 is pending:

~~~bash
uv run workflow.py tui demo-orders --snapshot --width 110 --height 38
~~~

For the artifact layout, deterministic integrity contract, error handling and future real Codex preview-host adapter, see `docs/prototype-review-contract.md`. The runnable demonstration launches **no real specialist sessions or application servers**, and checks no human interaction automatically.

To preview the MVP tracker without a full-screen terminal:

~~~bash
uv run workflow.py tui demo-orders --snapshot --mvp-panel --width 110 --height 34
~~~

The **simulation runner** still executes a mock EDD cycle only for `mvp-01`; additional simulated specs remain **UNSCHEDULED**. The **live** Coordinator now has a separate sequential MVP scheduler. A file on disk is never proof that a live specialist passed: only the recorded Handoff, real tests and human gates establish live progress.

## Connect an existing Git repository or worktree

Keep this workflow kit **outside the target repository**, so its SQLite state and simulated report files never dirty the real checkout. With Git installed and a committed, named branch in the target checkout:

~~~bash
uv run workflow.py tui demo-git --repo /absolute/path/to/your-repo
~~~

The command initializes a new initiative if necessary and attaches the local checkout. It **does not** fetch, commit, push, merge, or run Codex. The guided one-command startup can ask for the same Git path and request confirmation before creating an integration worktree. In the dashboard:

For later sessions, reopen the already bound initiative with just the same initiative ID, without --repo:

~~~bash
uv run workflow.py tui demo-git
~~~

The original binding is restored from SQLite, including the selected worktree and expected branch.

1. Press **G** to view repository identity, current branch/HEAD, dirty status, and registered worktrees.
2. Press **W** to create an isolated MVP worktree (default MVP ID: mvp-01), and confirm the proposed branch/directory. Git creates branch agentflow/demo-git/mvp-01 from the **selected checkout's current HEAD**. This is an explicit local Git mutation.
3. Optionally press **S** to select a different already-registered worktree by its number in the Git panel.
4. Press **G** again to return to the report, then **N** to start the same **simulated** agent lifecycle.

Press **P** for the project-level view. It shows the local parent/sub-tickets, the integration branch, its current revision, and any optional GitHub mirror. The default local tracking requires no GitHub or Git origin.

## One integration branch for the entire project

Create the integration branch **before** the first specialist starts. From the TUI, press **P → I** and confirm. Or:

~~~bash
uv run workflow.py integration-create demo-git
uv run workflow.py project-status demo-git
uv run workflow.py tui demo-git --snapshot --project-panel
~~~

The Coordinator creates a dedicated sibling checkout on branch agentflow/demo-git/integration. An MVP worktree created after that defaults to the integration revision as its base. This allows the next MVP to be based on already-integrated prior MVP work, rather than the old initial branch.

The integrated Git history and local ticket progress serve different purposes:

- **Local parent ticket:** the single project-level index of generated plans, specs, reports, approvals, MVP dependencies and progress.
- **Local MVP sub-tickets:** one for each generated spec, tracking UX DESIGN → REQUIREMENTS → RED → GREEN → REFACTOR → VERIFY → UX INSPECT → PROVE with links/hashes.
- **Integration branch:** the accepted, committed code and documentation history for the project. This is the **only target branch** for an explicit integration push; MVP branches remain separate.

The prototype supports explicit, safe integration actions:

~~~bash
uv run workflow.py integration-promote demo-git --mvp mvp-01 --source /absolute/path/to/mvp-worktree
uv run workflow.py integration-snapshot demo-git --confirm
uv run workflow.py integration-push demo-git --confirm
~~~

Promote requires a clean, committed MVP branch and a clean integration branch, and permits only a fast-forward. If branches diverge, the operation stops; it never silently rebases, merges conflicts or rewrites history. Snapshot copies the local parent ticket, MVP tickets, specs and reports under docs/agentflow/demo-git/ in integration and commits only those managed files. **Review files for private information before snapshot and push.** Push is non-force and requires a configured remote/permissions.

Because the specialists are simulated, no actual application commits are automatically promoted. The real Codex scheduler must gate these operations on authentic PROVE evidence, then update local tickets and optionally mirror progress.

### Optional GitHub Issues mirror

This is an **add-on, not a replacement for local files**. If origin points to the intended OWNER/REPO and GitHub CLI is authenticated:

~~~bash
uv run workflow.py tracker-create demo-git --repo OWNER/REPO --confirm
uv run workflow.py tracker-mvp demo-git --mvp mvp-01 --confirm
uv run workflow.py tracker-sync demo-git --confirm
~~~

This creates one GitHub parent issue, links each generated MVP as a native GitHub sub-issue, and posts progress comments containing observed paths, stage status and hashes. Raw Markdown bodies remain local until an explicitly confirmed integration snapshot/push. The GitHub adapter records a durable operation before writes; if its outcome is uncertain, it blocks automatic retries so it cannot silently create duplicate tickets. Inspect/reconcile that operation manually before continuing.

**B** attaches a checkout by entering its local path. Attach/create/select are allowed **only before any stage starts**, to preserve the relationship between the initiative, branch, and source revision. Use another initiative to work with another MVP or change a bound repository after running.

Read-only Git inspection occurs on each dashboard refresh. The Coordinator checks that the selected worktree remains on its bound branch and is clean before running a simulated stage. Human gate requests record the Git branch, path and commit SHA; approval is rejected if the revision changes, the branch changes, or uncommitted changes appear.

The default new worktree path is a sibling directory of the main checkout: REPO-agentflow-INITIATIVE-MVP. Existing directories, branches, worktrees, and user files are never overwritten. Nothing is deleted automatically. GitHub origin is detected and displayed when present; GitHub API requests occur only when explicitly selecting optional parent/sub-issue/sync actions. No GitHub pull requests are created.

Equivalent commands, for scripts or non-interactive setup:

~~~bash
uv run workflow.py init demo-git
uv run workflow.py repo-attach demo-git --path /absolute/path/to/your-repo
uv run workflow.py worktree-create demo-git --mvp mvp-01 --base HEAD
uv run workflow.py repo-status demo-git
uv run workflow.py tui demo-git --snapshot --git-panel
uv run workflow.py tui demo-git
~~~

You can also use worktree-create with --path /absolute/new/path or worktree-select with --path /absolute/existing/worktree. The CLI performs the same preflight checks as the dashboard.

**Git-aware does not mean live Codex execution yet.** Specialist stages and Handoff remain simulated. Explicit local integration commits, non-force pushes and optional GitHub issue writes are available, but no application-code edits, real EDD test runs, automatic proof-triggered pushes or PRs occur.

Keys:

| Key | Action |
| --- | --- |
| N / Enter | Start or continue the simulated workflow |
| A | Approve the displayed gate after a confirmation prompt; the next mock agent launches automatically |
| C | Request changes; at G2 save versioned feedback, rerun Prototype and issue a **new preview and renewed G2** |
| O (G2 panel) | Open the current, hash-checked local HTML prototype preview in a browser |
| R | Retry a blocked handoff (within the bounded retry budget) |
| D | Inspect the full artifact path, SHA-256 hash, integrity result, and error details |
| H | Switch the report panel between the current report and event history |
| G | Switch to the Git repository/worktrees panel |
| P | Project ticket, MVP child tickets, integration branch and optional GitHub mirror |
| V | Each MVP's feature + UX scope, two UX checkpoints, six EDD stages, current/next status and observed times |
| I (project panel) | Create local project integration worktree with confirmation |
| J (project panel) | Fast-forward selected, committed MVP into integration; refuse divergence |
| F (project panel) | Snapshot local Markdown into the integration branch with explicit review |
| O (project panel) | Non-force push integration to origin after confirmation |
| T / M / U (project panel) | Optional GitHub parent / sub-issue / progress publication |
| B | Attach an existing local repository/worktree (before starting) |
| W | Create an isolated MVP worktree after explicit confirmation (before starting) |
| S | Select a registered worktree by number (before starting) |
| Up/Down, PageUp/PageDown, Home/End | Scroll the report or event-history panel without losing the approval buttons |
| Q | Quit; SQLite state persists for the next session |

The report preview is limited to 256 KiB, but the generated file remains available in the normal `work/` directory. If a report changes after the gate was created, the dashboard flags it as changed and the Coordinator rejects stale approval. The approval is a **local unauthenticated demo action**, not a trusted production human authorization. The MVP tracker uses the Coordinator's actual stage index, approval log and handoff events, never bare report existence, to decide which mock steps have completed.

The full-screen interface requires a terminal with Python's `curses` support (commonly Linux/macOS) and at least **48 columns × 19 rows**. It uses a two-column layout at 76 columns and above; smaller terminals use stacked cards. For a non-interactive shell, CI, or unsupported platform, first initialize an initiative and use:

~~~bash
uv run workflow.py init snapshot-demo
uv run workflow.py run snapshot-demo
uv run workflow.py tui snapshot-demo --snapshot
~~~

`--snapshot` prints the **same boxed layout** as plain text, without running agents or launching the full-screen terminal UI. Preview different terminal sizes with:

~~~bash
uv run workflow.py tui snapshot-demo --snapshot --width 100 --height 30
uv run workflow.py tui snapshot-demo --snapshot --width 55 --height 20
uv run workflow.py tui snapshot-demo --snapshot --git-panel
~~~

## Equivalent commands (optional)

If you prefer scripts, the existing CLI actions still work. Commands below use a fresh initiative called `cli-orders`.

~~~bash
uv run workflow.py init cli-orders
uv run workflow.py run cli-orders
uv run workflow.py status cli-orders
~~~

Review the generated discovery report and copy the exact SHA-256 printed by status into:

~~~bash
uv run workflow.py approve cli-orders --gate G1_DISCOVERY --sha <the-displayed-hash>
~~~

This automatically runs the next simulated agent, then pauses for G2. Repeat for G2_PROTOTYPE, G3_PRODUCT_SPEC (feature + product UX contract), G4_ROADMAP, and G5_MVP_01 (feature + MVP UX spec). After G5 approval, both simulated UIUX passes and the six simulated EDD stages run automatically to SIMULATED_COMPLETE.

To request a correction during a gate:

~~~bash
uv run workflow.py changes cli-orders --reason "Add an authorization case"
uv run workflow.py run cli-orders
~~~

To demonstrate invalid handoff detection on a fresh initiative:

~~~bash
uv run workflow.py init broken-demo
uv run workflow.py run broken-demo --simulate-invalid-handoff
uv run workflow.py status broken-demo
uv run workflow.py retry broken-demo
~~~

To inspect an audit trail:

~~~bash
uv run workflow.py history cli-orders
~~~

## Storage and boundaries

- SQLite state: work/<initiative>/agentflow.sqlite3
- Local parent: work/<initiative>/project-ticket.md
- Local MVP sub-tickets: work/<initiative>/mvps/mvp-NN/ticket.md
- Simulated reports: work/<initiative>/...
- Product UX contract: work/<initiative>/product-ux-spec.md
- MVP UX contract and reports: work/<initiative>/mvps/mvp-NN/ux/{spec,design,design-review,inspection}.md
- Prototype review iterations: work/<initiative>/prototype-review/iteration-NNN/ (preview, manifest, preflight, review bundle, human feedback or approval)
- Accepted prototype: work/<initiative>/prototype-acceptance.md, carried into Specification as reviewed findings
- Proposed handoffs and validation results: work/<initiative>/handoffs/<run-id>/
- The CLI refuses to overwrite existing non-simulated reports.
- This demo's local approval command is **not authenticated**. A production Coordinator must use a trusted human approval channel.
- No real Codex agent was executed. The integration contract is in docs/codex-cli-adapter.md.

## Automated tests

~~~bash
uv run python -m unittest discover -s tests -v
~~~

See tests/test_dashboard.py, tests/test_agentflow_prototype.py, tests/test_git_adapter.py, tests/test_git_integration.py and tests/test_project_tracking.py for dashboard rendering/actions, local tickets, integration/push safety, optional GitHub mirrors, approval revision binding, stale-hash, failure-routing, and completion scenarios.
