# MVP 05 — sequential live multi-MVP execution

**Implemented:** after a genuine human G7 approval completes an MVP, the live
Coordinator can schedule the **next sequential MVP** from the unchanged,
G4-approved roadmap. Each MVP has its own G5 feature/UX scope review,
two fresh UIUX sessions, six fresh EDD specialist sessions, independent host
test/screenshot receipts and renewed G6/G7 decisions.

## One-command terminal flow

From the workflow-kit directory:

```bash
uv run workflow.py live my-project
```

1. Complete the initial discovery/prototype/specification/G4 roadmap review.
2. At **G5_MVP_01**, review and approve **both** `mvps/mvp-01/spec.md` and
   `mvps/mvp-01/ux/spec.md`.
3. Finish UIUX DESIGN → REQUIREMENTS → RED → GREEN → REFACTOR → VERIFY.
   At `AWAITING_EVIDENCE`, press **E** to configure and run project-native
   functional + UX capture checks with human consent. G6 and G7 each need
   independent review and explicit **A** approval.
4. When MVP-01 is `COMPLETED`, **[M] Next MVP** appears *only* if the
   **approved G4 roadmap** explicitly names `MVP-02`.
5. Press **M**, confirm, then **N**. If feature and UX scope files are not
   already present, the Coordinator launches **one fresh MVP DESIGN specialist
   and a separate Handoff Agent** to produce them. If both already exist,
   they are presented for review without an unnecessary new Codex call.
6. **G5_MVP_02 is mandatory**. Approve both version-bound feature and UX
   scopes before the new UX/EDD run is eligible.
7. Complete another full UX/EDD/evidence/inspection/PROVE cycle for MVP-02.
   Previous MVP receipts stay immutable and visible in the parent project
   ticket. After G7, the next roadmap MVP may be scheduled similarly.

The corresponding noninteractive/scripted control is
`uv run workflow.py live-next my-project --confirm`, but ordinary use only
requires the one live command and the terminal actions.

## Key records

```text
work/my-project/
  project-ticket.md
  mvp-roadmap.md                 # G4-approved, immutable scope of the roadmap
  agentflow.sqlite3             # active_mvp + live_mvp_completions + approvals
  evidence-plan.json            # MVP-01 test/UX capture configuration
  mvps/
    mvp-01/
      spec.md                   # G5_MVP_01
      ux/spec.md
      edd/verification-evidence.md
      edd/proof-acceptance.md
      ux/inspection-acceptance.md
      ticket.md
    mvp-02/
      scope-review.md           # only if next-scope Codex session was needed
      spec.md                   # G5_MVP_02
      ux/spec.md
      evidence-plan.json        # MVP-02-specific trusted host checks
      edd/verification-evidence.md
      edd/proof-acceptance.md
      ux/inspection-acceptance.md
      ticket.md
```

Each new specialist result and its Handoff is validated independently.
Later scope, requirements, verification and proof specialists receive the
immediately preceding MVP's approved feature/UX scope and G7 acceptance as
additional context, so cumulative regressions are visible in their assignment.
The real live Coordinator stores `mvp_id` on approval and host evidence
records, prevents a prior MVP's G6/G7 from being mistaken for a new decision,
and keeps old evidence frozen against its **original** Git content fingerprint.
Current-MVP evidence is rechecked against the **current** selected source.
Previous MVPs must be covered by relevant regression checks in later MVPs;
historical approval is not a claim that later changes cannot break behavior.

## Safety and limits

- **No G7, no next MVP.** The next MVP cannot be skipped or arbitrarily named.
- **No roadmap match, no scheduling.** MVP-02 must be explicitly named in
  `mvp-roadmap.md` approved at G4. The Coordinator does not rewrite that
  roadmap when creating a later scope.
- **No automatic source release.** It never commits, merges, pushes,
  creates GitHub Issues or deploys in the new scheduling operation.
- **No implicit test authority.** Each MVP stores separate evidence commands
  and requires explicit confirmation for every host execution. Host commands
  are not an operating-system sandbox.
- **No full dependency solver yet.** MVP 05 schedules strictly in numeric
  order rather than parsing an arbitrary dependency DAG, and it does not
  parallelize separate MVP worktrees. Review the roadmap's dependency and
  regression expectations at each G5/G6/G7.
- **No automatic human judgement.** Screenshot pixels, accessibility and
  usability still require actual human inspection.
- **Source changes outside the Coordinator are blocked.** Worktree HEAD and
  content fingerprint checks continue to apply. Switching Git branches or
  integrating an external commit requires a separate reviewed operation.

## Verification

Run `uv run --offline python -m unittest discover -s tests -q`.
`tests/test_live_multi_mvp.py` uses a fake Codex executable and real host
processes against disposable Git checkouts. It checks independent G5/G6/G7
and evidence per MVP, roadmap and sequencing guards, existing scope adoption,
and migration compatibility. It does **not** certify any actual product's
requirements, browser appearance or release readiness.
