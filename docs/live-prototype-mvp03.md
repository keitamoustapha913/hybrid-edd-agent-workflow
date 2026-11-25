# MVP 03 — real Codex prototype preview and renewed G2 review

**Implemented:** the real Prototype specialist returns an **interactive, self-contained HTML document** as a validated artifact alongside its Markdown report. The live Coordinator freezes immutable, numbered iterations, serves the exact HTML through a loopback-only browser preview, records human change feedback, reruns a fresh Codex Prototype session, and requires a **new G2 acceptance** bound to the report + preview + checks + review-bundle digests. Acceptance is then carried into Specification.

**Not claimed:** that an agent wrote a production application, that a browser screenshot or user interaction was automatically tested, or that actual usability/accessibility/EDD proof passed. A *static interactive experiment* can simulate a product scenario and test whether the direction makes sense; real backend-hosted application previews are a future adapter.

**Later milestone:** MVP 04 adds host-executed software checks, captured actual
UI evidence, a separate UIUX INSPECT Codex session and G6/G7 human reviews
**after the real implementation's VERIFY stage**. These checks do not turn the
earlier synthetic prototype into production code. See
[MVP 04](live-evidence-mvp04.md).

The normal `uv run workflow.py` TUI still shows the **synthetic simulation preview**. The `live-*` commands described here operate the actual Codex-produced prototype and have their own SQLite state.

## 1. Start and reach G2

Start from the workflow kit root, with Codex CLI already installed and authenticated:

~~~bash
uv run workflow.py init storefront
uv run workflow.py repo-attach storefront --path /absolute/path/to/clean/git-checkout
# Fill in work/storefront/brief.md with a concrete hypothesis and example user task.
uv run workflow.py live-init storefront
uv run workflow.py live-run storefront --confirm
uv run workflow.py live-status storefront
~~~

After reviewing the actual Discovery report, approve the exact G1 SHA displayed in live-status:

~~~bash
uv run workflow.py live-approve storefront \
  --gate G1_DISCOVERY --sha COPY_DISCOVERY_SHA \
  --continue
~~~

The Coordinator runs one fresh Prototype Agent and another fresh **read-only Handoff Agent**. It will reject missing, invalid or unsafe HTML, incorrect artifact paths and unexpected source modifications. On success, it creates `iteration-001` and pauses at **G2_PROTOTYPE**. It never approves based on an agent's self-report.

## 2. Open the actual generated prototype

~~~bash
uv run workflow.py live-status storefront
uv run workflow.py live-preview storefront --check-only
uv run workflow.py live-preview storefront --open
~~~

The last command starts a local HTTP server on a random `127.0.0.1` port and attempts to open the browser **on the same computer as the CLI**. It prints the URL, report hash, preview hash and bundle hash. If a browser cannot open automatically, open the displayed URL on that computer. Stop the server with Ctrl+C when finished.

If the workflow runs inside a remote container or remote development machine, its loopback address **does not automatically refer to your laptop**. Use an explicitly authorized local port-forward to the printed port, or run the CLI and browser on the same host. No public deployment, shared preview server or external tunnel is created automatically.

### What the reviewer checks

- Can I complete the **one central user task** this experiment is meant to demonstrate?
- Does the behavior make sense with the **synthetic sample data**?
- Are empty, loading, error, success and recovery states understandable where the prototype includes them?
- Can I use the important actions with **keyboard and focus**, and understand them at narrow/mobile widths?
- What is mocked, what is missing, and what should enter the product UX specification?

The HTML may provide mocked states to try; the Coordinator's automatic checks do **not** perform these actions for you. The `checks.json` document records static parsing and offline constraints as PASS while visual usability, keyboard evaluation and real backend integration remain explicitly **NOT_TESTED / NOT_CONNECTED**.

The local preview host only serves that one frozen HTML document and sends restrictive Content-Security-Policy headers. The HTML comes from a coding agent and is **untrusted**: the host rejects external resources, API calls, embedded documents, service workers and persistent browser storage. Use the **loopback URL**, not the raw `file://` path, to retain the preview-host security headers.

## 3A. Request changes and see a new preview

~~~bash
uv run workflow.py live-changes storefront \
  --reason "Add a visible loading state and make the primary action keyboard operable."
uv run workflow.py live-run storefront --confirm
uv run workflow.py live-preview storefront --open
~~~

The Coordinator:

1. Records your exact feedback and the rejected iteration in local SQLite and Markdown.
2. Returns the workflow to **Prototype**, preserving the approved Discovery contract.
3. Starts a **new Codex specialist session**, with the previous change request in its input context.
4. Performs a fresh read-only Handoff and deterministic artifact checks.
5. Freezes **iteration-002**, with a new directory and cryptographic bundle identity even if the specialist happens to return the same HTML.
6. Requests **renewed G2 approval**. The old report SHA/preview SHA/review bundle cannot approve the next iteration.

Each further request repeats the process. Earlier iterations remain intact and are linked from `project-ticket.md`. The reviewer must open and examine the **new** preview and record observations again; the Coordinator does not claim a change is implemented merely because the agent mentioned it.

## 3B. Accept the reviewed iteration

From `live-status`, copy the exact report and review-bundle SHA-256 values:

~~~bash
uv run workflow.py live-approve storefront \
  --gate G2_PROTOTYPE \
  --sha COPY_EXACT_REPORT_SHA \
  --bundle-sha COPY_EXACT_REVIEW_BUNDLE_SHA \
  --review-notes "I opened iteration-002, tried the main action by mouse and keyboard, and observed the updated loading and success states. Error recovery remains a product requirement." \
  --continue
~~~

Approval is accepted only if the latest frozen report, HTML, checks, manifest, bundle, prior feedback and selected Git revision still agree. Blank review notes, stale hashes or modified files block it. **The CLI treats the written review notes as your statement of what you observed**; it cannot prove that a person actually interacted with the browser.

After G2 acceptance:

- `prototype-acceptance.md` records the accepted iteration, run ID, hashes, source revision, human observations and carry-forward boundaries.
- The Specification Agent receives the accepted findings and acceptance file, not the experimental HTML as deployable code.
- Product UI/UX requirements should include observations and unresolved gaps. Later MVP Design, UX DESIGN, RED/GREEN, VERIFY and UX INSPECT still have independent responsibilities.

Using `--continue` authorizes the next stretch of live Codex work until G3. Omitting it records approval without starting another model session.

**Waiver compatibility:** An already-running MVP 02 initiative whose G2 was generated without any live preview can still use its explicit legacy waiver. A newly generated MVP 03 preview **cannot be waived**; review it, request changes, or stop.

## Frozen local evidence

~~~text
work/storefront/
  prototype-report.md                    # latest live Codex Prototype report
  prototype-preview/index.html           # latest live Codex HTML artifact
  prototype-acceptance.md                # only after exact G2 approval
  prototype-review-live/
    iteration-001/
      prototype-report.md                # immutable reviewed report snapshot
      preview/index.html                 # immutable Codex-produced HTML snapshot
      checks.json                        # static host checks; NOT_TESTED human actions
      preview-manifest.json              # source/run/type/preview hash
      review-bundle.json                 # report + preview + checks + manifest + feedback hashes
    iteration-002/
      ...                                 # new session, new bundle, new review
  live-feedback/<uuid>.md                # reviewer feedback, hashed into the next bundle
  live-revisions/<run-id>/...            # prior coordinator-owned canonical revisions
  codex-runs/<run-id>/...                 # original isolated specialist/Handoff traces
  project-ticket.md                      # real iteration history + parent progress
~~~

The SQLite `live_preview_iterations` table records READY / CHANGES_REQUESTED / ACCEPTED for each iteration. `live_preview_decisions` binds the human approval row to an exact bundle and preview hash. The Coordinator verifies these hashes again before later stages; changing an accepted preview or report **cannot silently change the approved experiment**.

The archive and SQLite metadata live outside the selected source worktree. No automatic Git commit, push, GitHub Issue update, deployment, external network access or production code promotion happens during prototype review.

## Error and restart behavior

- Unsafe, missing or noninteractive HTML is marked **BLOCKED** before adoption. Review the run's specialist/Handoff logs; then `live-retry` and `live-run --confirm` after correcting the issue.
- A crash after the preview was frozen but before SQLite adoption can be reconciled against that exact frozen run. It does not launch a replacement agent or reuse an earlier approval.
- Uncertain live Codex processes are never replayed automatically. Inspect and use `live-reconcile` only when the run record is terminal.
- Content tampering in a frozen or approved review bundle blocks G2 or later dispatch.
- Running a loopback preview is temporary: stopping it does not delete the frozen version or change the human gate.

## Verification without a model call

~~~bash
uv run --offline python -m unittest discover -s tests -q
~~~

The suite uses a fake Codex executable to exercise real process dispatch, preview output contracts, browser HTTP headers, response hashes, feedback reruns, gate decisions, tampering and crash recovery. **These are Coordinator implementation tests, not proof that a real Codex-generated application meets your product specification.**
