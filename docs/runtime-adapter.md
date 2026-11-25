# Runtime adapter contract for an actual Coordinator

The bundled AGENTS.md, agents/00-coordinator.md, agents/00-handoff.md and workflow.yaml describe **desired orchestration**, but do not provide a running multi-agent scheduler. **OpenAI Codex CLI is the selected runtime.** See docs/codex-cli-adapter.md for the specialist and Handoff Agent invocation contracts.

The bundled **local-first prototype is executable today**. It creates an auto-updated parent project ticket and MVP sub-tickets as Markdown without any GitHub connection. Its Git adapter discovers a local repository, prepares isolated MVP and project integration worktrees, supports safe fast-forward promotion and an explicit non-force push, and checks revisions around simulated transitions. An optional GitHub adapter can create/link Issues or post progress only when explicitly invoked. **The MVP 01 live Codex process runner is now implemented separately:** it executes one explicitly confirmed specialist and one read-only Handoff session, quarantines results and never advances simulated gates or claims verified proof. See docs/live-codex-mvp01.md.

## Runner capabilities needed

| Operation | Inputs | Required reliable result |
| --- | --- | --- |
| start_specialist | Role instruction path, common policy, approved inputs, task/scope, allowed write paths, dispatch ID, source revision | Distinct session/run ID or a clear failure |
| normalize_handoff | Producer result, actual files/logs, expected role and output paths | Separate read-only Codex Handoff session and schema-conforming candidate |
| validate_handoff | Producer structured result, normalized candidate, approved roots | Deterministic schema/path/status check; reject ambiguous or missing evidence |
| observe_run | Session/run ID | RUNNING, COMPLETED, FAILED or INTERRUPTED with actual handoff/artifact references |
| validate_changes | Baseline source identity, role ownership rules, changed paths | Real diff/owner validation result |
| request_human_approval | Gate ID, exact artifact hash/version, review summary, allowed decisions | Authenticated human decision event or PENDING |
| validate_ux_companions | Product or MVP feature spec, matching UX spec, approved gate ID | Distinct SHA-256 identities for both specs at G3/G5; any change invalidates approval |
| run_uiux_design | G5-approved UX criteria, one MVP, read-only source context, UIUX DESIGN mode | Dedicated Codex run with UX design/review files, criterion map and mode-specific valid handoff |
| run_uiux_inspection | Final tested build, G5 UX criteria, UIUX INSPECT mode, bounded browser/device checks | Separate read-only Codex run, real observations/screenshots and revision-bound UX_VERIFIED/UX_FAILED/INCONCLUSIVE |
| launch_prototype_preview | Isolated experiment worktree, bounded preview command/file, port/host policy, process/health checks | Stable local preview reference, log, code revision, manifest, cleanup status or explicit failure |
| record_prototype_feedback | Exact rejected G2 iteration, structured human request, current report/preview/bundle IDs | Durable feedback record and new Prototype dispatch; old approval cannot be reused |
| persist_transition | Previous state version, next state, event log entry | Atomic durable update; conflict prevents double-dispatch |
| source_identity | Repository/worktree/configuration | Commit + dirty status or suitable content/build digest |
| bind_worktree | Existing checkout, initiative and MVP ID | Canonical Git common directory, selected worktree/branch/HEAD, validated ownership and clean baseline |
| prepare_mvp_worktree | Approved MVP, source commit, intended branch/path, explicit permission | New isolated branch/worktree or a safe refusal; no overwrite, deletion or implicit remote operation |
| update_local_tickets | Validated handoff, gate event, current local artifact inventory | Parent project-ticket.md and MVP-NN/ticket.md managed sections refreshed; human notes untouched |
| integrate_verified_mvp | Real PROVE PASS, clean committed MVP source, verified integration base | Safely integrate approved work or BLOCKED if history/conflicts cannot be handled |
| publish_integration | Authorized remote and reviewed source/document changes | Exact integration revision pushed non-force, or explicit refusal without rewriting history |
| mirror_issues_optional | Local tickets and validated status, opted-in GitHub repo | Parent and MVP sub-issues/comment receipts, never a source of approval or a local workflow blocker |
| verify_artifacts | Report/log/test/trace paths, source identity | Existence, provenance and freshness observations |

## Non-negotiable requirements

1. **Distinct agents:** launch an actual new specialist session with only the assigned role instruction file. Prompt organization alone does not ensure independence.
2. **Human approval integrity:** approval must come from a trusted human interaction bound to the exact artifact; never infer it from a generated Markdown file, YAML flag, or an agent's claim.
3. **Scope enforcement:** permissions, CI gates, branch protection, worktrees or sandbox constraints should enforce the role's allowable files/actions where possible. Prompt text is not a security boundary.
4. **Sequential writes by default:** protect the same workspace from competing agents; overlapping edits require an isolation/integration strategy.
5. **Idempotent recovery:** store launch IDs and durable state, reconcile interrupted runs, enforce retry budgets, and never double-launch an uncertain run.
6. **Real evidence:** source identities, commands, test logs and artifacts must come from execution. The coordinator validates references but is not the independent VERIFY agent.
7. **Safe stop:** inability to launch independent sessions or obtain a trusted approval means WAITING_FOR_HUMAN or BLOCKED, never fictitious progress.
8. **Dedicated handoff interpretation:** every specialist result is normalized by the single reusable Handoff Agent, and then checked deterministically. A passing schema check never authorizes a human gate or establishes software correctness by itself.
8a. **Dedicated UI/UX quality:** G3/G5 each bind both feature and UX specifications; UX DESIGN precedes REQUIREMENTS and independent UX INSPECT follows VERIFY. PROVE cannot claim real UX PASS with `INSPECTION_RECORDED` (the bundled mock status), missing screenshots where required, or stale experience evidence. See docs/uiux-quality-gates.md.
9. **Git scope and revision binding:** capture repository identity, worktree location, branch, HEAD and working-tree diff state before each agent run. Bind human decisions to artifact hashes and the observed Git revision; reject a switched branch, unauthorized changes, or a stale approval. Real coding runs may leave expected uncommitted changes; record their content/diff identities and verify them rather than treating a clean-tree-only simulation policy as sufficient proof.
10. **Safe integration:** never automatically merge, rebase, force-push, reset, clean, delete a worktree, or open a GitHub PR without explicit authorization and integration checks. Keep state/evidence outside the target checkout or in a dedicated, documented path so orchestrator artifacts cannot contaminate source status.
11. **Local-first tracking:** one project parent and one sub-ticket per generated MVP remain accessible without network access. Progress is derived from actual file paths, hashes, gate states and revisions. Optional GitHub mirrors must not become required for progression.

## Example state transition

1. SPECIFICATION finishes product-spec.md; Coordinator verifies the handoff.
2. Coordinator records WAITING_FOR_HUMAN and requests G3_PRODUCT_SPEC against the exact artifact revision.
3. Human approves through the trusted approval channel; the runner records that decision.
4. Coordinator verifies that the artifact has not changed and launches a **new MVP DESIGN agent session**.
5. After human approval of the roadmap and MVP-01 spec, Coordinator starts EDD REQUIREMENTS automatically.
6. A valid READY / RED_CONFIRMED / GREEN_CONFIRMED sequence advances automatically through EDD.
7. VERIFY finds an unmet criterion: Coordinator returns the failure to GREEN, reruns affected phases, and never treats the stale verification as PASS.

## Suggested acceptance tests for the runner

- Pending human approval prevents the next phase from launching.
- A modified approved spec invalidates the approval for that revision.
- GREEN cannot change protected RED tests without a RED-owned correction.
- VERIFY runs in a distinct session and has read-only implementation scope.
- A failed or stale VERIFY result cannot produce a PROVE PASS.
- A retry budget is enforced and a crash/resume does not duplicate a run.
- MVP-02 verification considers impacted MVP-01 regression criteria.
- A changed worktree branch or stale commit prevents approving an old gate, even when the report hash did not change.
- An already-existing branch or directory is never overwritten when requesting a new MVP worktree.
- The live Codex runner distinguishes authorized test/code changes from foreign changes instead of demanding a perpetually clean worktree.
- A local parent ticket and MVP sub-ticket update after each gate/stage, preserve human notes and never report mock PROVE as real proof.
- A failed GitHub mirror operation does not lose or invalidate the local ticket and workflow state.
- A modified product UX spec invalidates G3; a modified per-MVP UX spec invalidates G5 even when its companion feature spec is unchanged.
- A UIUX DESIGN/INSPECT mode mismatch is rejected by deterministic handoff validation; each inspection uses a fresh session with real evidence, not GREEN's self-approval.
- An actual UX_FAILED or INCONCLUSIVE sends defects to the owning agent and cannot yield real PROVE PASS until reverified on the current revision.
- Automatic code integration/push is attempted only after a real, revision-bound PROVE PASS and within an authorized remote policy.

The local ticket engine, Git integration primitives, explicit artifact snapshot/non-force push and optional GitHub Issue adapter are implemented and tested in isolation. **MVP 01** adds a live, manually dispatched Codex specialist process plus a distinct read-only Handoff session, bounded timeouts, captured logs, staged candidate output and deterministic validation. Automatic scheduling/adoption, trusted live human gates, PROVE-to-integration hooks, production-grade diff-owner checks, crash recovery and actual UI/UX evidence remain **runtime-adapter requirements**.
