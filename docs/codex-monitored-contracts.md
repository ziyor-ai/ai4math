# Managed Codex contract version 2

New contracts may opt into the owned Codex workflow. Existing tasks and their
version 1 phase budgets remain unchanged. This does not migrate or reopen any
previous task, refund a route or pool reservation, or certify mathematics.

In addition to the usual research task fields and actual literature/method gates,
`task.add` requires the following explicit fields:

```json
{
  "external_supervision": "process",
  "timeout_seconds": null,
  "output_limit_bytes": null,
  "codex_workflow": {
    "skill_manifest_artifact": "<frozen JSON object SHA-256, also in inputs>",
    "worker_actor": "<worker distinct from coordinator>",
    "source_roots": ["<existing canonical absolute directory>"]
  }
}
```

`toolchain_artifact` is optional within `codex_workflow`; if supplied it must
also be an input containing a frozen JSON object. The owned runtime validates
the executable tool/skill schema. Core registration checks byte binding and JSON
object form. Source roots describe intended read scope; they do not override
clean-start import restrictions or create an OS sandbox. Only trusted-local,
literature-gated `explore` and `produce` tasks support this contract.

Omit `phase_budgets`: version 2 records trusted stage start times without stage
deadlines. `timeout_seconds` defaults to null and also accepts a positive integer. Null
means no automatic time cutoff. An integer sets one total hard limit before
worker dispatch; literature, review, exploration, seal and holds all consume
this same time. No stage change or hold resets or extends it. The supervisor
uses a fixed monotonic deadline, capped by the original total duration and the
trusted UTC deadline. Its watchdog does not consult SQLite before stopping the
owned local process tree. Output overflow under a configured byte limit and
capture failures also trigger that watchdog. Version 1 keeps its original
stage and total deadline behavior.

`output_limit_bytes` defaults to null. A positive integer sets a task cap.
With null, the
App Server raw streams, broker command output, native supervisor streams,
written artifacts and seal manifest have no task byte cap. Byte counts and
hashes are still recorded. Tool previews remain paged or shortened while the
complete corresponding raw data is retained. Source-acquisition quotas and
route/pool reservations remain independent and unchanged. Existing frozen
contracts retain their original integer limits.

Use the owned Codex runtime to dispatch. Generic `run`, `start-external` and
`supervise-external` cannot start these tasks. Internally the runtime calls
`supervise_external(..., managed_codex=True)`. This is a trusted-local API
boundary, not authentication against code able to import or modify Python.
Actor labels likewise identify accountable workflow roles, not human signatures.

The workflow remains literature → worker receipt → coordinator review and
explicit release → exploration → begin seal → seal → confirmed local stop →
coordinator acceptance. The worker receipt must use the pinned worker actor.
All original input, contract, candidate, source ownership, configured output size and
first-import freshness checks remain in force. With no total cutoff, the
freshness lower bounds still require outputs after release and a new manifest
after begin-seal. Sealing does not mean task acceptance or a mathematical proof.
After timely seal, acceptance is separate from the worker execution clock.

Coordinator controls (via `apply`) are:

* `codex.dispatch_hold`: `{"held": true, "reason": "..."}` blocks new v2
  dispatch transactions. False permits future dispatch. Existing tasks continue.
* `task.codex_hold`: `{"task": "...", "held": true, "reason": "..."}`
  blocks new managed tool dispatch and core phase transitions; false releases
  the hold. It does not physically suspend an already running remote generation
  or pause a configured total clock. Only the dispatching coordinator may set it.
* `task.codex_stop`: `{"task": "...", "reason": "..."}` records an
  interrupted terminal task and requests stop. Only the dispatching coordinator
  may request it. The runtime attempts its controlled interrupt; the native
  supervisor contains/stops local processes and separately records confirmation.

Stop confirmation describes local containment. It does not prove remote model
generation has stopped or that costs are zero. Failed, interrupted, expired or
output-limited tasks cannot become completed. No control operation refunds their
route/pool use. A future attempt needs a new contract and existing strategy gates.

## Research contracts aimed at the locked target

Set these fields in a new task's codex_workflow when a study uses a fixed local
corpus and asks the worker to advance the original question.

~~~json
{
  "research_stop_policy": "target_progress_v1",
  "literature_acquisition": "local_only"
}
~~~

The two fields can be selected independently. With target_progress_v1, the
literature plan identifies a load-bearing proof or construction step and a probe
that can change the choice of approach. In exploration, the worker submits
probe_review with the first result, its actual coverage, and its relation to the
locked target. A candidate proof or counterexample for the target can go directly
to independent review. A limited probe needs a follow-up aimed at its remaining
obligation, with a deduction, a counterexample attempt, or a concrete obstruction
to the route. An unresolved handoff records that attempt's result and remaining
obligations.

The broker records probe-review.json, binds the first evidence hashes and
requires at least one follow-up artifact written by the broker after the review.
Submit paths and a structured return_record through handoff. The broker checks
the content and writes return.json. Paths and run arguments are relative to the
task's return directory. Sealing checks the two records against the frozen task
and evidence files. A failed check can be corrected before handoff within the
same task. The exploration clock, route use and independent validation still
follow the original task contract.

New studies can opt in to `target_progress_v2`. After a limited first probe,
record the remaining gap, an actual inference or obstruction, and its consequence
for the next decision in `progress.derivation`. A subsequent `run` declares its
`decision` before execution. Use the smallest calculation that can distinguish
the relevant outcomes, and expand when broader coverage bears on the argument.
`run.output_path` publishes the captured bytes and SHA-256 under a new return
path. `read.if_sha256` lets a worker confirm a known return file without reading
it back into the model context.

In a v2 handoff, `source_checks` maps each load-bearing external result to
its exact use and maps original hypotheses to the argument. A cited passage
must have been read in full through the task's source tools and must bind its
archived original and locator. Use a null passage ID and a blocked, conditional
handoff if the original cannot be checked. Use an empty list for a self-contained
argument. Independent review still evaluates the inference, the applicability
of each cited result and the relation to the locked target.

With local_only the worker searches and reads only the selected academic
library. It records missing originals and conditional dependencies at the
relevant argument step. The broker excludes remote fetch and asynchronous
acquisition requests in this mode. The coordinator still controls the chosen
corpus and its access boundaries.
