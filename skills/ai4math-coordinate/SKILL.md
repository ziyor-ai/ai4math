---
name: ai4math-coordinate
description: Coordinate a long-running AI4Math project in the main Codex chat. Plan and review independent tasks, trace counterexample impact and theorem conditions, and maintain round summaries and pause handoffs.
---

# Coordinate AI4Math research

Use the workspace named by the user. Inspect `strategy-brief`, current tasks and the locked goal before making decisions. Use the registry and exported artifacts as memory across context compaction. Preserve the exact statement, domain, dependencies and historical budgets.

## Start a new workspace

Check `status` for the requested workspace. If it has no goal, use the user's statement, domain and assumptions to create the node with `node.add`, then lock it with `goal.set`. Surface any ambiguity that changes the mathematics before locking it. Record the proof obligations as they become clear. Register an initial route with `route.add`, activate it with `route.status`, and record the direction with `strategy.decide` using the current `strategy-brief` digest. Use the same academic library the user selected. One well-defined route is enough to start. Prepare the first contract with the existing literature and exploration gates, and keep task preparation and authorized launch distinct.

## Coordinate ongoing work

At the global review gate, check the whole proof graph and where results reconnect to the goal. Choose three to five materially different, ready mathematical directions when the obligations support it. An independent certificate replay, a follow-up experiment on the same mechanism, or a renamed route does not fill a second research slot. Record the reason and falsifier for each selected direction. Search the separate academic library, read the relevant original passages, and freeze a unified plan before new research dispatch. Keep source applicability separate from evidence.

Use `knowledge search --kind scan` for each selected obligation. Start from the previous receipt and identify what changed in the hypotheses, mechanism, bridge or falsifier. Share exact source versions and passages across related routes. Send missing references to the separate literature chat using `--workspace PATH knowledge --root LIB request --route ROUTE --file GAP.json`. Include `question`, exact `assumptions`, `queries` already tried, and `purpose` stating the decision the result could change. Record the request ID alongside the local search. Continue with the available evidence and an independent probe while acquisition is queued. Use `--workspace PATH knowledge --root LIB inbox --status published --after SEQ` at natural checkpoints and retain the last inspected `delivery_seq`. Freeze selected passages for follow-up, or steer a running worker within the same contract. Review the exact hypotheses before transferring any theorem. Network discovery, original capture, and shared profile registration are the literature chat's work.

Register and export the actual reviewed contracts using the framework. Start authorized Codex workers with `codex-run TASK --codex-exe PATH --library PATH`. Keep the main chat for coordination and independent review. Use `codex-queue --after JSON --wait 15` with the returned cursor for incremental updates. On a worker literature receipt, check its local queries, source scope, limitations and probe. As the dispatching actor, call `task.release_exploration` with the task ID, exact `worker_receipt_sha256` and your assessment. An unfilled literature request can leave a theorem transfer open while other work proceeds. Progress and soft alerts leave the task's budget and evidence unchanged.

New tasks default to `null` for both `timeout_seconds` and `output_limit_bytes`. Set a cap only when the user or the evaluation protocol specifies one. Continue monitoring and use the stop and pause controls as directed. Follow preview offsets or inspect the complete retained artifact.

When authorized to correct a worker, bind `codex-steer TASK --turn ID --event SEQ --text TEXT` to the observed live turn and event. A queued correction and a worker acknowledgment are separate. Use `codex-control hold|continue [TASK]` or `codex-control stop [TASK] --reason REASON` for the requested scope. Hold is cooperative and takes effect at safe boundaries. Check the resulting live and native states.

## Review and accept a handoff

Verify the sealed contract, artifact hashes, scope and local stop receipt. Record the task's actual terminal status using `task.finish_external`. Evaluate the mathematical result separately.

- **Candidate proof or counterexample.** Bind a proposed claim to the exact node, statement digest, scope and sealed artifacts. For executable checks, freeze the independent checker as `validator_artifact` and include all claim artifacts in the `validate` task's inputs. Have the independent validator both register the task and run it with `run TASK` under an actor distinct from the claim author. Record its result and trust basis in `validation.add`. For a proof reviewed directly by a person, record `human_proof` only with the actual independent human attestation. Record the latest review with `review.add`, and use `claim.accept` only when its review, validation and prerequisite checks pass.
- **Finite support.** Record the tested domain, cases and limitations. Keep the universal obligation open.
- **Blocked or interrupted.** Record the first unmet obligation and the evidence actually available. Preserve the terminal status and used budget. Use `route.checkpoint` for the precise next mathematical question when the route permits it.

Read `reports/research-progress.html` for progress and the registry for acceptance. At a due global review, record the strategy decision before dispatching more work, including a validator. See `docs/operations.md` for claim, validation and review inputs.

## Diagnose changes to a proof route

Run `python -m ai4math --workspace WORKSPACE diagnose --node NODE` on the affected obligation. Read `docs/research-diagnosis.md` from the AI4Math source directory for operation payloads and independent checker contracts. Use the relevant operation when the corresponding evidence exists.

- **Accepted counterexample.** Check the exact refuted node, dependency edges, routes and manuscript entries flagged for review. Keep unrelated accepted results in view and check the registered status of the main goal. For a counterexample to a candidate mechanism, record its candidate scope separately.
- **Revised hypothesis or conclusion.** Keep the original node and register the revision with `diagnosis.revision`. Have a validator distinct from the counterexample author register and run the replay against the new node. Have a separate registrar use `diagnosis.replay` to distinguish exclusion by the new condition from survival of the counterexample or an inconclusive replay.
- **Insufficient invariants.** If two frozen witnesses agree on the chosen invariants and give different conclusions, have an independent validator check the pair. Register `diagnosis.indistinguishability`, state whether the witnesses are actual or formal, and name the distinguishing condition to investigate next.
- **Load-bearing theorem.** Attach the selected exact passage with `knowledge.attach`. Use `diagnosis.source_check` to map each hypothesis to an accepted basis, an open obligation, or a mismatch at the use point. Use a new record with `supersedes` when the passage or mapping is corrected.

## Maintain the round summary

After each reviewed round and when pausing for handoff, append a `review` or `pause` record with `summary --record FILE`. Add `--library LIB` for explicitly selected passage IDs in that library. Write what was derived, its exact scope, the remaining gap and the first action on resumption. Link task and claim IDs, frozen derivations and review artifacts, and the original passages used. Include relevant diagnosis results and their effect on the next mathematical question. For a pause, record work left at the checkpoint and any pending stop confirmations.

Use `reports/research-summary.md` for handoff and `reports/research-history.md` for earlier rounds. Check that the new entry and its links resolve. Reuse the same ID when retrying an identical submission. See `docs/operations.md` for the round record format.
