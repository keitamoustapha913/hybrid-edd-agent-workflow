# Human approval log — <initiative-id>

This is an **evidence record**, not a self-approval mechanism. Only record decisions actually communicated by the authorized human, with a source and the precise artifact version/hash approved. An agent may propose an entry but cannot invent a signature, timestamp, waiver, or approval. The Coordinator must verify the original decision through a trusted channel before launching the next agent.

| Gate ID | Artifact | Exact revision/hash | Decision | Approver | Trusted decision source | Date/time | Authorized scope |
| --- | --- | --- | --- | --- | --- | --- | --- |
| G1_DISCOVERY | discovery.md | pending | pending | ... | ... | ... | Prototype experiment |
| G2_PROTOTYPE | prototype-report.md | pending | pending | ... | ... | ... | Specification |
| G3_PRODUCT_SPEC | product-spec.md | pending | pending | ... | ... | ... | MVP design |
| G4_ROADMAP | mvp-roadmap.md | pending | pending | ... | ... | ... | Roadmap review only |
| G5_MVP_01 | mvps/mvp-01/spec.md | pending | pending | ... | ... | ... | EDD implementation of MVP-01 |

Valid recorded human decisions: APPROVED, CHANGES_REQUESTED, REJECTED, and (only for G2) WAIVED. A placeholder or PENDING entry never authorizes execution. A material change to the artifact requires new approval against its new version.

## Waivers

If the human explicitly waives the prototype, record:

- Gate waived:
- Explicit human instruction and date:
- Reason:
- Affected uncertainty and risk:
- Where the exception is carried forward in the product/MVP spec:

## Scope changes

Track approved re-openings of earlier product/MVP requirements and the downstream tests/evidence that were invalidated.
