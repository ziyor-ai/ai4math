---
name: ai4math-coordinate
description: Coordinate an AI4Math project. Plan proof obligations, supervise independent native workers, review evidence, and record round decisions.
---

# Coordinate AI4Math research

Use the workspace named by the user. Inspect `strategy-brief`, current tasks and the locked goal before making decisions. Use the registry and exported artifacts as memory across context compaction. Preserve the exact statement, domain, dependencies and historical budgets.

Resolve the latest workspace policy from `ai4math.policy.VERSIONS[-1]`. A new workspace uses that policy by default unless the user or study protocol pins a version. After initialization, use `apply policy.migrate` if the recorded policy is older, before preparing its first task. For an existing workspace, check its recorded policy first. A forward migration requires explicit authorization and settled rounds with no pending or running tasks. Continue an already current workspace directly.

## Start a new workspace

Check `status` for the requested workspace. If it has no goal, use the user's statement, domain and assumptions to create the node with `node.add`, then lock it with `goal.set`. Surface any ambiguity that changes the mathematics before locking it. Use `node.plan` to link specific proof obligations to the goal. An explicitly scoped direct proof, direct refutation, or bounded starting probe can begin at the root. Register a route for the selected obligation, activate it with `route.status`, then record its mechanism, bridge, first load-bearing probe and falsifier with the current `strategy-brief` and the reviewed plan. Use the user's academic library. Keep task preparation and authorized launch distinct.

Use the workspace policy's existing operations. For `v2c4`, put each selected route's mode, probe and falsifier in `strategy.decide.research_steps`. Bind each research task to its round, and report the independent gate's next action. See [the versioned operations](../../../docs/operations.md#新版研究策略) for payload fields.

## Coordinate ongoing work

A persistent research request can name just the workspace. Apply these rules on resumption and across rounds.

- **Resume the registered goal.** Read the current registry and the latest settled round or pause handoff. Preserve the locked statement, domain, existing budgets, source restrictions and evidence levels. Use the academic library explicitly requested within that scope, or recover the registered library from workspace records or a verified handoff or frozen contract. Check it against the current source policy. If the policy lists a single allowed root and it is a verified AI4Math academic library, use it. Ask for the path if a library is needed and the authorized choice remains unresolved.
- **Run the round lifecycle.** Prepare the round as described below, execute the authorized tasks, then independently review, validate as applicable, and settle the round. Record and report the outcome in the main chat before opening the next round. Use the evidence and remaining budget to select the next eligible proof or refutation obligation. Continue through intermediate closures and route failures while an authorized step remains. Apply the same rules after context recovery.
- **Complete the goal.** Require formal acceptance of a proof or rigorous refutation of the locked main statement at its recorded scope. Confirm the actual termination of in-flight tasks, settle the round, and preserve the archived acceptance materials before reporting completion.
- **Record a pause.** When the user asks to pause, the applicable budget is exhausted, or a gate leaves no authorized path forward, record verified progress at its actual evidence level, the open obligations, any blocker, and the precise condition for resumption. Use the pause handoff in [Maintain the round summary](#maintain-the-round-summary).

### Prepare each round

1. **Recover the current decision.** Read `strategy-brief`, `health`, the latest handoff in `reports/research-summary.md`, and the relevant proof graph. Reconcile any open round and its task receipts before opening the next one. Check which prerequisites and evidence versions still hold, the results of prior probes, and the remaining obligation, route, and study budgets. Resolve a due or halted independent review gate before dispatch.
2. **Select a ready obligation.** Prioritize independent validation when a sealed, load-bearing candidate is ready for it. Bind the validation to the exact claim or candidate version and frozen artifacts. If a premise blocks validation, record the premise and what would resolve it. Select another eligible obligation if available. For work scoped to the root, decide whether to decompose it, validate a candidate, switch mechanisms, or pause. At a global review, choose three to five ready directions when the proof graph and budgets support that many. Count a replay, follow-up probe, or continuation under its existing mechanism.
3. **Explore before choosing a new probe.** For each selected research obligation, use `knowledge search --kind scan` in the authorized academic library before designing the probe. Read the selected mechanism and source records, and verify the original passage and hypotheses for any theorem transfer. Use the previous receipt to identify changes in hypotheses, mechanisms, bridges, or falsifiers. Compare materially different mechanisms and record the assumptions, connection to the locked goal, first bounded probe, falsifier, and next decision for success, failure, or an inconclusive result. Attach only the selected material through `knowledge.attach`. For exploratory tasks, use a valid fixed-capacity pool or `exploration.open`, register the candidate with `exploration.propose`, and observe the existing method review and research intent requirements.
4. **Freeze the next round's basis.** Record the selected routes under `strategy.decide` where required by the workspace policy. For a decision subject to the literature gate, the same actor freezes a `literature.search` receipt for each focus route after the decision, then submits one `literature.plan` covering those exact routes. Proceed with `round.open` when there is an eligible research or validation task. Bind the task to the round and its applicable intent, method, candidate or validation request. Use `task.preflight` and the exported `PROMPT.md` and `input.json` before authorized dispatch. If no step is eligible, record the blocker and what would make a step possible. See [round planning](../../../docs/research-workflow.md#研究轮次的规划与控制) and [exploration](../../../docs/exploration.md).

Send missing references to the separate literature chat using `--workspace PATH knowledge --root LIB request --route ROUTE --file GAP.json`. Include `question`, exact `assumptions`, `queries` already tried, and `purpose` stating the decision the result could change. Record the request ID alongside the local search. While acquisition is queued, continue with in-scope work whose premises are available. Use `--workspace PATH knowledge --root LIB inbox --status published --after SEQ` at natural checkpoints and retain the last inspected `delivery_seq`. Freeze selected exact source versions and passages for follow-up, or steer a running worker within the same contract. Network discovery, original capture, and shared profile registration are the literature chat's work.

## Dispatch and supervise

Register the reviewed contract and freeze the project Skill manifest. For native delegation set `worker_driver=subagent` and use `dispatch-subagent --task TASK --library LIB --host HOST`, naming the actual host. Then delegate through the host with the returned `PROMPT.md`, `input.json`, task ID, workspace and library. The worker uses `worker-task` or the supplied `ai4math_task` tool. Use the host's child-session status and the broker receipts to monitor the task. See [native dispatch](../../../docs/codex-research.md#项目内子代理派工) for commands.

On `literature_ready`, verify the worker's actual queries, source scope, limitations and first probe. As the dispatching actor, call `task.release_exploration` with the task ID, exact `worker_receipt_sha256` and your assessment. A missing original can leave a theorem transfer open while other authorized work proceeds.

For a Codex App Server contract, use `codex-run TASK --library LIB` and monitor with `codex-queue --after JSON --wait 15`, retaining the returned cursor. Bind `codex-steer` to the observed turn and event. Check corrections separately for delivery and acknowledgment. Use `codex-control` for cooperative hold and continue or an authorized stop, then inspect the actual terminal state.

New tasks default to `null` for both `timeout_seconds` and `output_limit_bytes`. Set a cap only when the user or the evaluation protocol specifies one. Continue monitoring and use the stop and pause controls as directed. Follow preview offsets or inspect the complete retained artifact.

## Review and accept a handoff

Verify the sealed contract, artifact hashes, scope and actual worker termination. For native workers seal the broker handoff with `seal-subagent`, then use `task.confirm_stop` with observed evidence and record the independent adversarial and verification reviews before `task.finish_external`. Record the actual terminal status. Assess and settle prebound rounds after all tasks reach terminal state. Only a formal acceptance can count as a closed mathematical obligation.

- **Candidate proof or counterexample.** Bind the claim to the exact node, statement digest, scope and sealed artifacts. Have a validator distinct from the author freeze its checker, register the `validate` task with the exact claim artifacts, and run it. Use the frozen validation request to interpret `result.json`. Record the process status, protocol status and verdict separately. Register a passing receipt and its trust basis with `validation.add`; for `human_proof` record an actual independent human attestation. Use `review.add` and `claim.accept` only after the review, validation and dependencies pass. A composition must cover all premises and the inference to the target.
- **Finite support.** Bind and report the tested cases and limitations. Keep the universal obligation open. Record conditional deductions with their hypotheses and indicated proof obligations.
- **Blocked or interrupted.** Record the first unmet obligation and the evidence actually available. Preserve the terminal status and used budget. Use `route.checkpoint` for the precise next mathematical question when the route permits it.

Read `reports/research-progress.html` for progress and the registry for acceptance. At a due global review, record the strategy decision before dispatching more work, including a validator. See `docs/operations.md` for claim, validation and review inputs.

## Obligation accounting

Routes are plans for individual obligations. Read the obligation's cumulative dispatches, reviewed decision changes, proof closures, and rounds since the last closure before using a new route.

- Each started task adds one dispatch. Use `round.assess` and `round.settle` for prebound rounds. Record a decision change separately from a closure. Bind `falsified` to frozen probe evidence. A human gate review needs an actual `attestation`, and the gate reviewer must differ from the last dispatcher.
- At the configured stagnation limit, use the independent review gate to authorize a bounded next step. Name the verification target and available scope. A strategy switch by itself does not clear a halted gate.
- When continuing a route, bind the actual predecessor explicitly with `continuation_of`. The continuation inherits obligation accounting and is the same mechanism for scheduling purposes.
- When the workspace already has 8 open obligations, a new leaf needs `supersedes` (the replaced obligation is marked `superseded_by`) or a nonempty `debt_justification`. Read `obligations` and `obligation_debt` in `strategy-brief` before adding one.
- A terminal failure is not progress: every failed, timed-out or interrupted task enters the failure ledger, and `retry_limit` (default 2) consecutive failures park the route and halt the workspace. Read `failures.streaks` in the panel; do not retry the same contract twice.
- Run `python -m ai4math --workspace WORKSPACE health` before every global review.
- Before opening another mechanism on an obligation that already has 6 checkpoints, record a `correspondence` mapping (model claim → locked statement) with a frozen artifact; a `mismatch` verdict halts the workspace. The reviewer must not be the last dispatcher.
- When `strategy-brief` reports `gate.due` or `gate.halted`, stop dispatching: write the gate review with `review-gate` (decision, reason, next question) or pause for the human. A halted workspace ignores `strategy.decide` alone. Treat `long_drought`, `stagnation_limit` and `obligation_debt` as decision inputs, not as background noise.

## Diagnose changes to a proof route

Run `python -m ai4math --workspace WORKSPACE diagnose --node NODE` on the affected obligation. Read `docs/research-diagnosis.md` from the AI4Math source directory for operation payloads and independent checker contracts. Use the relevant operation when the corresponding evidence exists.

- **Accepted counterexample.** Check the exact refuted node, dependency edges, routes and manuscript entries flagged for review. Keep unrelated accepted results in view and check the registered status of the main goal. For a counterexample to a candidate mechanism, record its candidate scope separately.
- **Revised hypothesis or conclusion.** Keep the original node and register the revision with `diagnosis.revision`. Have a validator distinct from the counterexample author register and run the replay against the new node. Have a separate registrar use `diagnosis.replay` to distinguish exclusion by the new condition from survival of the counterexample or an inconclusive replay.
- **Insufficient invariants.** If two frozen witnesses agree on the chosen invariants and give different conclusions, have an independent validator check the pair. Register `diagnosis.indistinguishability`, state whether the witnesses are actual or formal, and name the distinguishing condition to investigate next.
- **Load-bearing theorem.** Attach the selected exact passage with `knowledge.attach`. Use `diagnosis.source_check` to map each hypothesis to an accepted basis, an open obligation, or a mismatch at the use point. Use a new record with `supersedes` when the passage or mapping is corrected.

## Maintain the round summary

After each reviewed round and when pausing for handoff, append a `review` or `pause` record with `summary --record FILE`. Add `--library LIB` for explicitly selected passage IDs in that library. Write what was derived, its exact scope, the remaining gap and the first action on resumption. Link task and claim IDs, frozen derivations and review artifacts, and the original passages used. Include relevant diagnosis results and their effect on the next mathematical question. For a pause, record work left at the checkpoint and any pending stop confirmations.

Use `reports/research-summary.md` for handoff and `reports/research-history.md` for earlier rounds. Check that the new entry and its links resolve. Reuse the same ID when retrying an identical submission. See `docs/operations.md` for the round record format.

### Report each round in the main chat

After each settlement and summary registration, post one standalone Markdown report in the main chat before opening or dispatching the next round. This includes validation-only rounds, failed rounds that reached settlement, and rounds with no mathematical progress. During an ongoing Codex `/goal`, send a user-visible progress message in the `commentary` channel and then continue. If the work ends at this round, use the same structure in the final reply. The coordinator posts the report even when the result already appears in a worker chat, tool output, or a workspace report. After context recovery, report any round settled in the current run whose report has yet to be sent.

Use the following order and write in the conversation's language.

1. **第 <轮号> 轮 · <短标题>**　Use the recorded round number or ID. State the actual round outcome and the locked main statement's current status.
2. **`goal_impact`**　Name the proof obligation, state the concrete mathematical result or obstruction with its assumptions and scope, and explain what changed in the proof path to the locked goal. Link the recorded goal impact or round summary and the relevant frozen result.
3. **复核与证据**　Say what the independent check actually covered and its verdict. Give the evidence level and acceptance status from the registry. Keep conditional derivations, finite cases and formal acceptance distinct. Include material failures or untested cases and the round's recorded launches and applicable budget use. Link the verification receipt when available.
4. **剩余义务与下一步**　Name the first open prerequisite and the next eligible check, decision or gate. If pausing, give the blocker and the condition for resumption.

For an aborted round or a pause before settlement, use the same structure with the observed round and task states, verified progress, and any pending stop confirmation. If a summary export fails, retry it and disclose any remaining gap using the verified registry facts.
