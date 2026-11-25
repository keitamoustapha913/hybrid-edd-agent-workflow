# MVP 04 — host verification, UI/UX inspection, and evidence-gated PROVE

**Implemented:** the live Coordinator no longer ends permanently at
`AWAITING_EVIDENCE`. After Codex VERIFY, the human explicitly authorizes
project-specific real test and UX-capture commands. A Python host runner executes
them in a disposable copy of the selected Git worktree, saves actual exit codes,
stdout/stderr and fresh image artifacts, records a SHA-256-bound receipt, and
unlocks a **new, read-only Codex UIUX INSPECT session**. The human reviews the
inspection at **G6**; PROVE then runs in its own Codex session and awaits a
separate **G7** evidence/proof review. The original G1–G5, separate Handoff
sessions and MVP 03 prototype feedback loop remain intact.

**Not claimed:** automatic grading of UX screenshots, automated accessibility
auditing, code coverage thresholds, a true OS-level sandbox, or release
authorization. A successful process exit is observable evidence that *that
command* ran; it is not automatically evidence that every product criterion is
satisfied. The dedicated UIUX agent and reviewer must interpret the actual
captured behavior and explicitly state what is still untested.

## 1. Reach the independent evidence barrier

Open the same guided live interface for the entire project:

~~~bash
uv run workflow.py live storefront
~~~

Choose **[N]** to authorize eligible specialist sessions, **[A]** to review/approve each gate, and **[C]** to provide corrections. Complete G1–G5; after VERIFY the interface pauses at the evidence barrier.

When VERIFY has returned a valid report/Handoff, the status is
`AWAITING_EVIDENCE`. The Coordinator does **not** accept a model's `VERIFIED`
claim as independent proof.

## 2. Run observed functional tests and capture UI screenshots

At `AWAITING_EVIDENCE` press **[E] Collect evidence**. The first time, the
interface asks for a real functional test command, the UX journey/capture
command, and the resulting screenshot path. It saves these trusted,
project-specific choices in `work/storefront/evidence-plan.json`. No long
`--test` / `--ux-check` / `--ux-artifact` command line is required.

At every run, **[E]** displays the exact saved commands and asks for explicit
permission before running them; **[F] Configure checks** changes the saved
plan when tests or capture behavior change. Use commands that **actually exist
in your selected Git checkout**. For a web app, the UX command might
run an existing Playwright journey that launches the app, checks an interaction
and saves screenshots at known paths. For a CLI, use a test/capture adapter
appropriate to that user experience. The MVP 04 runner requires at least one
functional command, one UX command, and one captured image. It does not yet
support a no-image, N/A user interface exception.

What the runner actually checks:

1. Confirms the saved selected Git HEAD, branch and content fingerprint.
2. Copies current tracked **and untracked, non-ignored regular files** into a
   disposable snapshot outside the selected repository. Symlinks and snapshots
   over 30,000 files or 500 MB are rejected.
3. Removes stale expected screenshot targets **only inside that disposable
   snapshot**, so an older screenshot cannot satisfy a new run.
4. Executes each expressly supplied command as an argument vector, **without
   implicit shell expansion**, under a bounded per-command timeout.
5. Captures exact process exits and bounded stdout/stderr, verifies newly
   produced screenshot signatures and copies them to permanent evidence.
6. Checks the selected source fingerprint again. Only when all required
commands pass and every image is present does it adopt the local receipt
   and move the Coordinator to `READY` for UIUX INSPECT.

If a test fails, the workflow remains `AWAITING_EVIDENCE`; the failed run's
command logs remain for diagnosis. Nothing is marked PASS on a failed exit or
missing screenshot. Passing checks do **not** automatically launch another
paid Codex session: press **[N]** and authorize it when ready.

**Security boundary:** The temporary checkout copy prevents ordinary build/test
outputs from modifying your selected source worktree, but a process can still
access other files, network and credentials available to your user. **Only run
trusted commands in an appropriately restricted environment.** The snapshot
does not include `.git`, ignored dependency directories, or external services;
projects requiring those need explicit adapters. Do not run untrusted test
scripts or assume that `--confirm` creates an operating-system sandbox.

## 3. Independent UIUX INSPECT and G6

Press **[N]** in the same live interface to run the separate Codex UIUX
INSPECT session, then review its report with **[V]** and local screenshots.

The Coordinator runs the dedicated UIUX specialist in its own **read-only**
Codex session, with the host receipt as required context. A second, fresh
Handoff Agent normalizes the result. If it reports UX_FAILED or INCONCLUSIVE,
the workflow blocks; it cannot automatically become UX_VERIFIED. A validated
UX_VERIFIED report instead **pauses** at `G6_UX_INSPECTION`.

Open these local materials:

- `work/storefront/mvps/mvp-01/edd/verification-evidence.md` — host-observed
  commands, exit codes, source identity, logs and screenshot paths.
- `work/storefront/live-evidence/evidence-.../` — immutable frozen bundles,
  command logs and captured screenshots.
- `work/storefront/mvps/mvp-01/ux/inspection.md` — the independent UIUX
  specialist's observations, criterion coverage, defects and limitations.

Review the actual screenshot pixels, responsive layouts, keyboard/focus states
and criterion-specific findings. **A screenshot existing is not proof of all
visual states or accessibility checks.** If inspection is insufficient, stop,
request more tests/screenshots, or route the change back to the appropriate
owner. The CLI currently requires a reviewer to explicitly decide whether
the available evidence is sufficient; it does not perform automated image
judgment.

To accept after actual review, press **[A]** at G6. Provide your observations;
the interface supplies the bound report SHA and host-evidence bundle SHA
internally, so no hashes need to be typed. Only the exact current, untampered
materials can be approved.

G6 writes `ux/inspection-acceptance.md`, bound to the specific UIUX report,
source fingerprint, independent test receipt and evidence-bundle hash.

### UX feedback and repair loop

For an inspection-report clarification, press **[C]**, describe the needed
behavior/evidence, choose **`ux_inspect`**, then **[N]** to authorize a fresh
review.

For **missing or insufficient screenshots/checks** without changing code,
press **[C]**, request the specific missing scenarios and choose **`evidence`**.
The status returns to `AWAITING_EVIDENCE`; use **[F]** to update the saved
capture plan, then **[E]** to run and review new screenshots.

This invalidates the old bundle and requires a **new UIUX inspection and G6**.
It is also available when the UIUX specialist returned INCONCLUSIVE.

For an implementation defect, press **[C]**, describe the defect and expected
behavior, choose **`green`**, and press **[N]** to authorize the repair loop.

That route runs **GREEN → REFACTOR → VERIFY**, invalidates the earlier host
evidence and stops again at `AWAITING_EVIDENCE`. Collect fresh host checks and
screenshots, rerun UIUX INSPECT, and require **renewed G6**. A BLOCKED UIUX
Inspector can also be explicitly routed to GREEN via the latter command.

## 4. Separate PROVE session and G7 human proof review

After G6, a fresh PROVE Codex specialist receives the actual host receipt, the
UIUX inspection report and your G6 acceptance. It may audit source-bound
evidence but may not rewrite the tests or approve its own final result.

Press **[N]** to authorize the distinct PROVE session. The Coordinator then
**pauses at G7_PROVE**, even when the specialist returned PASS. Review the
report/evidence with **[V]** and the local artifacts, then press **[A]** and
write your own review notes. The interface automatically binds the exact
current source, proof report and host-evidence bundle hashes.

Only after that exact human decision does the live MVP-01 state become
`COMPLETED`. No Git commit, merge, integration promotion, GitHub Issue update,
deployment or push is implicit in either approval.

## Frozen local evidence layout

~~~text
work/storefront/
  evidence-plan.json               # locally saved, human-approved check settings
  mvps/mvp-01/
    edd/
      verify.md                     # Codex VERIFY report; not itself proof
      verification-evidence.md      # actual host command/screenshot receipt
      prove.md                      # distinct Codex PROVE report
      proof-acceptance.md           # G7 human decision
    ux/
      inspection.md                 # independent UIUX session report
      inspection-acceptance.md      # G6 human decision
  live-evidence/
    evidence-<run-id>/
      bundle.json                   # content-addressed manifest/source binding
      commands/*.stdout.txt        # actual host-captured output
      commands/*.stderr.txt
      images/*.png                 # freshly captured approved screenshots
  project-ticket.md                # evidence status and G6/G7 history
  agentflow.sqlite3                # separate live_evidence_runs ledger
~~~

Both the source fingerprint and all recorded artifact SHA-256 hashes are
rechecked before any later specialist dispatch or approval. Tampering, stale
source, missing command logs, or changed screenshot bytes block progression.

## Verify the implementation without real Codex usage

~~~bash
uv run --offline python -m unittest discover -s tests -q
~~~

The suite runs actual local processes against disposable Git snapshots and
uses a **fake Codex executable** for specialist/Handoff orchestration. It
demonstrates correct evidence collection and gate behavior; it does not prove
an arbitrary product's UX is acceptable or its production build is releasable.
