# MVP 07 — human-gated automatic continuation

**Implemented:** The local-first `live` terminal resumes eligible OpenAI Codex
specialists immediately after a **human-confirmed** decision, until the next
decision barrier. This removes repeated **N** keystrokes between approvals,
prototype revisions, host evidence and new MVP scope review.

## Daily use: one command

```bash
uv run workflow.py live my-project
```

The panel shows the active MVP and specialist, every EDD/UX sub-stage, the
current report, Git source revision and **Auto-continue ON/OFF**. Auto-continue
defaults to **ON**. Press **T** to persistently turn it **OFF** for that project;
when off, **N** manually authorizes the next specialist stretch. Toggling ON
does **not** itself start agents.

**Opening or reopening `live` never spends model usage automatically.** A
project in `READY` needs an explicit initial **N → Yes**. After that,
each of the following confirmations authorizes just the *next eligible*
stretch, not an entire unsupervised release:

| Your terminal action | Automatic work if enabled | Stop condition |
| --- | --- | --- |
| **A** approve G1, G2, G3, G4 or G5 | Start the next Codex specialist and read-only Handoff; advance eligible stages | Next G1–G5, independent-evidence barrier, or failure |
| **C** request changes | Save human feedback, invalidate affected approvals, rerun the correct specialist/Handoff | New report and fresh human gate; a revised G2 always has a **new preview** |
| **E → Yes** run configured checks | Run *exact* human-reviewed host test and UX capture commands; if all pass, start UIUX INSPECT/Handoff | Human **G6** review, failed command, or blocked run |
| **A** approve G6 | Start independent PROVE/Handoff | Human **G7** review |
| **A** approve G7 | **No Codex, no Git action** | Local MVP marked `COMPLETED` |
| **M → Yes** schedule next MVP | Start MVP DESIGN/Handoff if needed | New **G5_MVP_NN** scope review |
| **R** retry a terminal `BLOCKED` run | Re-dispatch only if preflight accepts the explicit retry | Next gate or new failure |

For example, the G2 feedback path is:

1. **O** opens the frozen `iteration-001` prototype preview in your browser.
2. **C** saves concrete changes and revokes the old iteration's authority.
3. Auto-continue launches a new Prototype Agent and Handoff with that feedback.
4. The Coordinator freezes `iteration-002` and pauses at a **new G2**.
5. You open the new preview, actually try it, and either request more changes
   or **A** approve with written observations. Only then does Specification run.

Every automatic transition is still controlled by the Coordinator's
source-fingerprint, artifact-hash, Handoff schema and evidence checks.
`AUTO_CONTINUATION_AUTHORIZED` events record which human action caused the
next dispatch, the MVP and the stage. The `live_console_preferences` setting
is local SQLite state; the parent Markdown ticket shows the preference.

## Deliberate stops and limitations

- **No self-approval.** G1–G7 and every new G5 must be decided by a person;
  completing a report is never equivalent to approving it.
- **No auto-execution of host commands.** At `AWAITING_EVIDENCE`, E prints the
  saved commands and asks you to confirm their execution each time. These
  are trusted host processes in a disposable *file copy*, **not an OS sandbox**.
- **No auto-Git or release.** The dedicated read-only Git Agent still reviews
  each Git delivery action under **I**; preparing, committing, promoting,
  snapshotting, pushing and external publication still require distinct human
  confirmation. GitHub Issues remain optional.
- **No resuming interrupted Codex by guesswork.** An in-progress/uncertain
  Codex session must be inspected and reconciled manually; restarting the
  terminal neither relaunches it nor trusts an incomplete outcome.
- **No auto-next-MVP.** G7 marks local completion only. You must deliberately
  choose **M** for another roadmap MVP. Delivery requirements still apply if
  Git integration is enabled.
- **No automated proof from UI pixels.** UIUX INSPECT can examine captured
  evidence, but a human must review actual outcomes and limitations at G6.

## Test contract

`tests/test_live_auto_mvp07.py` exercises G1-to-G2 auto continuation, G2
change-request/new-preview loop, denied authorization, persisted manual mode,
safe restart, explicit host evidence to G6, and the G7 stop with no Git
delivery. These tests use a **fake Codex CLI** and a disposable local Git
repository; they do not claim real model, browser usability or production
application correctness.
