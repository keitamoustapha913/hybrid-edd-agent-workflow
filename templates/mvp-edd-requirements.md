# EDD acceptance contract — <MVP-NN>

- Initiative: <initiative-id>
- MVP: <MVP-NN>
- Owner: EDD REQUIREMENTS Agent
- Status: DRAFT | FROZEN | BLOCKED
- Based on approved MVP spec: work/<id>/mvps/<mvp-nn>/spec.md [version / approved record]
- Based on parent product spec: work/<id>/product-spec.md [version / approved record]
- Frozen at: <timestamp/revision or not yet frozen>

## Goal and boundary

- User-visible/operational outcome:
- Includes:
- Explicit exclusions:
- Dependencies and completed earlier MVPs:

## Criteria — preserve these IDs through PROVE

| Acceptance ID | Given / initial state | When / action | Then / observable outcome | MVP requirement ID | Parent FR/NFR/PAC ID |
| --- | --- | --- | --- | --- | --- |
| MVP-01-AC-001 | ... | ... | ... | MVP-01-R-001 | FR-001 |

## Negative, boundary, and safety cases

| Acceptance ID | Case | Expected result | Why it matters |
| --- | --- | --- | --- |
| MVP-01-AC-002 | ... | ... | ... |

## Verification oracle

| Acceptance ID | Independent source of truth | Suggested test layer | Evidence required |
| --- | --- | --- | --- |
| MVP-01-AC-001 | Approved product/MVP spec | API / UI / CLI / etc. | Real assertion and artifact |

## Invariants inherited from the product spec

- Authorization and permissions:
- Privacy, data integrity and security:
- Accessibility and compatibility:
- Reliability, performance and localization (where required):
- Prior MVP regressions that must remain true:

## UI/UX criteria inherited from G3/G5 and UIUX DESIGN

- Approved product-ux-spec.md hash:
- Approved MVP ux/spec.md hash:
- UX design.md and design-review.md source:
| MVP UX ID | Parent UX ID | MVP acceptance ID | Automatable RED check | Independent UIUX INSPECT oracle |
| --- | --- | --- | --- | --- |
| MVP-01-UX-001 | UX-001 | MVP-01-AC-001 | | |
- Required screen/interaction/loading/empty/error/denied/success states:
- Responsive widths and keyboard/focus/accessibility cases:
- Explicit approved NOT_APPLICABLE rationale where relevant:

## Open decisions and divergence checks

| Question / proposed change | Why blocking | Owner | Human decision reference |
| --- | --- | --- | --- |

## Freeze checklist

- [ ] Every criterion maps to the approved MVP and parent product contract
- [ ] Failure, boundary, permission and negative cases are specified where relevant
- [ ] Verification is independent of current implementation behavior
- [ ] No new product scope or unstated policy was silently introduced
- [ ] Coordinator confirms frozen criteria match the approved MVP scope

## Handoff

- Status: READY | BLOCKED
- Output files: work/<id>/mvps/<mvp-nn>/edd/requirements.md
- Source revisions / approvals:
- Evidence and checks:
- Decisions requested:
- Known risks:
- Recommended next role: RED (only if READY and frozen)
