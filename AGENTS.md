# ai4math automation rules

This repository is a workflow engine for mathematical research. It coordinates
review, phase execution, evidence registration and paper delivery; it is not a
replacement for a project's independent mathematical validators.

1. A claim must carry one evidence level: `proved`, `disproved`,
   `finite_support`, or `unknown`.
2. A phase gate may authorize the next phase only when its required artifacts,
   validation result, SHA-256 manifest, import audit and scope statement pass.
3. A successful gate never upgrades `finite_support` to `proved`.
4. Producer programs and validators remain independent. The framework must not
   import a target project's graph adapter or solver to validate its output.
5. Registry files are append-only/content-addressed. Existing project data and
   original imports are never overwritten.
6. A failed gate is a review result, not a reason to silently create another
   phase.

## V2 workspace rules

7. Keep the framework workspace separate from the mathematical source project.
   Read-only historical intake does not authorize a new research phase.
8. Preserve locked target statements and their domains. Change mathematical
   meaning with a new node; change the main goal with a new workspace.
9. Review verdicts and command exit codes are not proof certificates. Record
   the trust basis of external checkers and actual human attestations explicitly.
10. Route budgets and interrupted tasks must not reset themselves. A failed,
    timed-out or output-limited task never becomes a completed proof.
11. Preserve the v0.1 archive and existing verification receipts. Keep new
    acceptance runs under data/verification/ with a command and version ledger.

## Exploration and academic knowledge (v0.4)

12. Before designing new exploration, use `knowledge search` on the separate
    academic library and read the selected source/mechanism records. For a new
    source, `knowledge discover` makes one explicit bounded Crossref/arXiv
    metadata request. Metadata and curated notes do not mean the paper was read
    or its hypotheses checked. Read the original passage before theorem transfer.
13. Use the workflow in docs/exploration.md. Attach only explicitly selected
    cards with `knowledge.attach`; respect clean-start allowed_roots and sealed
    evaluation materials. Never import an entire history or library by default.
14. Frame each exploration with an unresolved locked node and a fixed-capacity
    `exploration.open` pool. Use `exploration.propose` for representations,
    bridge lemmas, constructions or portfolio designs, and bind actual tasks
    with `exploration_candidate`. Do not bypass existing method reviews.
15. Select materially different mechanisms, not renamed versions of one idea.
    State assumptions, the bridge to the original goal, a falsifier and the
    first load-bearing probe. Structural signatures are a heuristic; the agent
    must still judge mathematical differences. Document revised parent designs.
16. Prefer probes with different candidate predictions and explicit decisions
    for success/failure. Predicted information gain is a scheduling suggestion,
    not observed evidence. Record obstacles and next changes after failures.
17. Candidate program scores require a completed frozen independent validator
    task, exact candidate/input binding, finite scope and explicit trust_basis.
    Keep producer and evaluator code independent. Frontier membership, source
    retrieval and candidate evaluation never accept a mathematical claim.
18. All new explore/produce task contracts include the methodology guidance.
    Use the exported PROMPT.md and input.json as the handoff to an external agent.
    This does not authorize spawning agents, calling a model, training, running
    an existing mathematical phase, publishing, or changing experiment budgets.
19. Pool reservations, route run limits and model/resource ledgers are distinct.
    Prepared, failed and interrupted exploration tasks do not refund pool slots;
    unknown costs remain unknown. New pools cannot override existing route or
    study limits. New source bytes or method versions never rewrite old records.
