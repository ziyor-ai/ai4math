---
name: ai4math-research
description: Execute one frozen AI4Math research obligation in a Codex worker turn. Use the task broker for independent literature, a falsifiable probe, structured progress, and handoff.
---

# Execute one AI4Math research task

Use `ai4math_task` with `{"action": "…", "payload": {…}}` for task operations. Start with `contract`. Read the exact `PROMPT` and inputs using `read` with `artifact_id` and optional `offset`. Preserve the locked statement, original domain, and bridge to the parent. Use the broker for all file, source, and command access. Direct shell, other tools, and work outside this task are outside the frozen contract.

Read the coordinator's selected source materials. Use `search` with `kind: scan` and independently query the specific assumption, mechanism or counterexample relevant to your obligation. Scan profiles for contributions, fields, keywords and limitations. Use `read` for frozen passages and `reuse_passage` with an explicit `passage_id` for selected archived passages. Check supplied transcriptions against their source before relying on them. Submit `literature_ready` with `sources` rows containing `url`, `locator`, `finding`, and `artifact_id`, plus `limitations` and `exploration_plan` as nonempty strings. For reused passages copy their exact returned locator. Explain which hypotheses carry over, what the local search adds and what remains open.

For a contract with `literature.acquisition_mode: async`, send a missing reference via `request_literature` with `question`, `assumptions`, `queries` already tried and `purpose`. Mention the ID and the limitation in your receipt. Complete the receipt with the evidence available and the work you can do independently. The separate literature chat handles network acquisition and shared profiles. At a later checkpoint, `literature_updates` checks your requests immediately. Use `search` and `reuse_passage` for published materials; record exactly how you checked applicability. Keep any dependent claim open while its evidence is unavailable. Older contracts use their frozen source instructions. End the literature turn after submission and start the first mathematical probe after coordinator release.

After release, run the contract's first falsifiable probe. Use `run` with explicit `argv`, `write` with a task-return-relative `path` and `text`, and `read` to inspect returned files. Report substantive milestones using `progress` with `summary`, `next_step`, `bridge`, and optional `artifacts`. When a correction arrives, acknowledge its ID using `progress.ack` and state the concrete change you will make within the same frozen obligation.

## Review the probe and hand off

Use the schema returned by `contract` for this task's frozen policy.

- When the contract includes `research_continuation` (v1 or v2), call `probe_review` with the actual evidence and its relation to the locked target. A limited result needs a distinct follow-up on the remaining mathematical obligation and new evidence. A candidate proof or counterexample for the exact target can go directly to independent review. Submit `handoff` with evidence `paths` and the structured `return_record`. The broker writes `probe-review.json` and `return.json`; omit both from `paths`.
- In v2, after a limited probe, put the exact gap, an actual inference or obstruction, and its consequence for the next decision in `progress.derivation` (`gap`, `deduction`, `next_decision`). Each subsequent `run` needs a `decision` explaining how its outcomes affect the route. Prefer a probe that can decide a mathematical step. Use `run.output_path` to archive raw output and `read.if_sha256` to confirm known files.
- In a v2 `return_record`, list the load-bearing external results in `source_checks` with the exact theorem, use point, hypothesis mapping and limitations. Bind each verified citation to a completely read original passage via `reuse_passage`. If a needed passage is unavailable, use a null `passage_id`, name the remaining obligation and hand off as `blocked`. Use an empty list for a self-contained argument.
- For contracts without `research_continuation`, write `return.json` with the original bindings and the required `first_probe` fields, then include it and the evidence files in `handoff.paths`.

When a probe finds a counterexample, hand back the exact witness, the statement it challenges, the hypotheses it satisfies and the conclusion it violates. If testing a proposed repair within the contract, preserve the old witness and identify which new condition changes the result. For a pair that agrees on selected invariants and differs on the conclusion, return both witnesses, their invariant values, and whether each is actual or formal. Include reproducible files through the broker so the coordinator can arrange independent validation and diagnosis.

Give the coordinator enough detail for the round summary. Link the actual derivation or obstruction, its precise scope, the first remaining obligation, and the next mathematical step. Record actual failures and source limitations. End the turn after the broker accepts the handoff. Mathematical acceptance belongs to independent review.
