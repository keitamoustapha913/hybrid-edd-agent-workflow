# MVP 06 — G7-bound Git integration and delivery

**Implemented:** the live, one-command terminal can create a dedicated project
integration worktree and MVP worktree, then deliver the *actual* G7-approved
source to integration. It never mistakes a Codex report for proof, merges
divergent branches, forces a push, or creates GitHub Issues by default.

## What you do in the terminal

From the workflow kit (which must live **outside** the Git repository being
built), start or resume the initiative:

```bash
uv run workflow.py live my-project
```

1. Before the first development Codex session, press **I → 1**. A dedicated
   **Git Agent** first launches in its own read-only Codex session, examines
   repository identity and risks, then provides a bounded review. Only
   `READY` reaches a separate human confirmation. The Coordinator
   creates/reuses `agentflow/my-project/integration` plus its own worktree,
   then creates and binds a **clean, dedicated `mvp-01` worktree** based on
   integration. Your existing checkout remains untouched.
2. Use **N**, **A**, **C**, **O** and **E** as before for Discovery → Prototype →
   Specifications → MVP Design → UIUX + EDD. Only real passing host evidence
   and the exact **human G7** review complete an MVP.
3. At `COMPLETED`, press **I** for Git delivery. **Every delivery action
   obtains a fresh Git Agent review** before human consent. The menu shows the current
   integration branch, worktree, revision and per-MVP delivery status:
   - **2 Commit** — preview modified/untracked filenames; inspect contents
     privately and confirm. Git stages the checked source and creates a
     normal commit on the MVP branch. No hooks are bypassed.
   - **3 Promote** — explicitly authorize **fast-forward only** from the clean
     committed MVP worktree to clean integration. Divergence blocks.
   - **4 Snapshot** — review local plans/specifications/reports/tickets for
     secrets and explicitly authorize a docs-only integration commit at
     `docs/agentflow/my-project/`. Local Markdown remains authoritative.
   - **5 Push (optional)** — separately authorize a **non-force** push of the
     named integration branch to `origin`; this is an external write.
   - **6 Reconcile** — inspect a potentially interrupted source commit. An
     uncompleted commit is never silently retried or treated as proved.
4. After the local snapshot, **M Next MVP** becomes eligible when a subsequent
   MVP is in the approved G4 roadmap. A new `mvp-02` worktree is based on the
   current integration tip (including the published documentation). The prior
   MVP worktree and its independent G7/evidence receipts remain unchanged.

Every accepted or blocked Git Agent review is recorded under
`work/<id>/git-agent-reviews/<request-id>/`, with an exact source/context hash
and result linked from the local parent ticket. Invalid JSON, changed source
HEAD, stale integration, missing completed trace, or an unexpected read-only
source modification blocks the action. The model's recommendation is **not
authority to commit, merge or push**: only the deterministic native Git
adapter performs the fixed action after renewed human consent.

The terminal is the intended interface; separate `integration-*` CLI commands
are legacy advanced controls and are not needed for this flow.

## Verification bridge across a Git commit

G7 initially binds the **pre-commit HEAD plus the working-tree fingerprint**.
Committing normally changes HEAD and may move untracked paths into Git's
index. MVP 06 records a stage-independent content manifest, the G7 approval
ID, original HEAD/fingerprint and resulting Git tree/commit. It checks the
full manifest again after staging, and checks the resulting clean commit tree
and parent before updating the live source baseline.

The original G7 test/UX receipts are **never rewritten** to claim they ran
against the later commit ID. The delivery record is the auditable bridge;
changing a file after approval blocks delivery.

If a Git hook fails or execution stops after staging, the receipt remains
`COMMITTING`. Use **I → 6** to reconcile; a retry requires unchanged reviewed
contents. An uncertain or changed result remains blocked for manual review.
No reset, force push, or automatic cleanup is performed.

## Local records

| Record | What it proves |
| --- | --- |
| `work/<id>/project-ticket.md` | Parent progress, G7 completions and Git delivery for every MVP |
| `work/<id>/mvps/mvp-NN/ticket.md` | Individual UX + EDD stage progress, human approvals and delivery status |
| SQLite `live_mvp_completions` + `live_approvals` | Historical, per-MVP human proof |
| SQLite `live_deliveries` | G7-to-commit-to-integration-to-snapshot-to-push ledger |
| `work/<id>/live-evidence/` | Immutable source-bound test logs and UX captures |
| Integration `docs/agentflow/<id>/` | Human-authorized, point-in-time copies of local Markdown |

GitHub Issues are optional mirrors; this live delivery feature never creates
or edits them. No deployment, PR, release, remote fetch or automatic push is
part of the workflow.

## Limitations

- Setup must occur **before the first Codex run**. A previously completed
  initiative on `main` cannot retrospectively masquerade as a reviewed,
  dedicated MVP branch. Start a new initiative/worktree for this mode.
- The content manifest excludes Git-ignored files; ensure important source
  files are not ignored. Private/secret files must be reviewed before commit
  and Markdown publication.
- External worktree mutations, unsupported file types, divergent history,
  failed hooks and uncertain delivery results **block** for review.
- A snapshot is a point-in-time Git commit. New local ticket updates after
  that commit remain local until a subsequent authorized snapshot.
- An approved G7 does not approve deployment. Remote push requires a separate
  affirmative decision; `origin` must already be configured.
