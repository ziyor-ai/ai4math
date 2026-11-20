# AI4Math

[简体中文](README.zh-CN.md)

AI4Math is a local Python workbench for long-running mathematical research. It organizes research around a locked statement and its proof obligations, coordinates work and independent verification, and preserves the evidence needed to resume across sessions.

The Python package is version `0.7.1`. The latest workspace research policy in this checkout is `v2c4`.

## Quick start

Use Python 3.10 or newer. The core runtime relies on the Python standard library and SQLite. From the repository root, run the offline example with a fresh workspace path.

```console
python -m pip install -e .
python -m ai4math --version
python -m examples.run_finite_demo workspaces/finite-demo
python -m ai4math --workspace workspaces/finite-demo status
python -m ai4math --workspace workspaces/finite-demo audit
python -m ai4math --workspace workspaces/finite-demo delivery-check
```

Separate producer and validator programs determine the minimum coprime labeling number of the finite graph $P_2^4$. The example uses synthetic reviewers and a checkable finite certificate.

## Research with Codex or Claude Code

Open a session from the AI4Math source root. Codex uses the project Skills in `.agents/skills/`. The Claude Code entries in `.claude/skills/` load the same instructions. [Project Skill guide](docs/codex-research.md)

| Task | Codex | Claude Code |
| --- | --- | --- |
| Coordinate research, review, and acceptance | `$ai4math-coordinate` | `/ai4math-coordinate` |
| Execute a frozen research task | `$ai4math-research` | `/ai4math-research` |
| Handle independent literature requests | `$ai4math-literature` | `/ai4math-literature` |

### Start a new problem

```text
Use $ai4math-coordinate to research the following statement in a new workspace.

Prove that for every natural number n there is an integer N > n such that N,
N+1, and N+2 are each a sum of two integer squares. The three representations
may use different pairs, and zero is allowed.
```

In Claude Code, invoke `/ai4math-coordinate` and supply the same statement. If paths are omitted, the new-problem workflow uses a fresh workspace under `workspaces/` and the library at `data/academic/`. The coordinator locks the statement, records proof obligations, and freezes the first round's strategy and literature plan. [New-problem workflow](docs/codex-research.md#新命题验收)

### Resume an existing workspace

In Codex, replace the placeholder with the actual workspace path.

```text
/goal Use $ai4math-coordinate to continue research on the locked main statement in <absolute workspace path>.
```

The Skill resumes from the latest registered checkpoint and uses the existing evidence and budget. After each round is settled and recorded, the coordinator posts its number and the main statement's status, `goal_impact`, verification evidence, and the remaining obligations and next step in the main chat. The next round starts after that report. The Goal finishes once a proof or rigorous refutation of the locked statement is formally accepted, tasks are closed, and the acceptance materials are archived. [Persistent research guide](docs/codex-research.md#用-goal-持续研究)

```console
python -m ai4math --workspace workspaces/my-research strategy-brief
python -m ai4math --workspace workspaces/my-research health
```

### Prepare a contract for review

```text
Use $ai4math-coordinate. Workspace: workspaces/my-research. Library: data/academic.
My statement is <statement, domain, and known assumptions>.
Record the goal and open obligations, consult the library, and export the
first-round PROMPT.md and input.json. Authorization covers preparation only.
```

The exported contract carries the frozen inputs. When an authorized managed task runs, the framework supplies the project research Skill and checks its hash against the frozen manifest. [Contract preparation and dispatch](docs/codex-research.md)

Use the literature Skill in a separate session when a proof obligation needs new source material. Give it the same academic library and ask for the version, exact passage, and applicable hypotheses. It can publish a finding or a search limitation against the request ID. [Literature workflow](docs/literature-workflow.md)

## Research workflow

1. **Lock the statement.** Record its domain and quantifiers. Use `node.plan` to connect proof obligations to the main statement.
2. **Choose a direction.** Record candidate mechanisms, the first decisive check, and its falsifier. Freeze the round's proof basis and selected sources in the strategy and task contract.
3. **Execute and verify independently.** Review the worker's literature receipt before releasing the first check. Bind inputs, checkers, outputs, and receipts by SHA-256. Record the process outcome, `result.json` protocol status, and review verdict separately.
4. **Accept evidence and settle.** Record claims at their actual scope and carry cumulative usage forward when continuing a route. Independent review can prioritize a candidate for verification or identify a prerequisite that blocks the next step.

A claim has one evidence level from `proved`, `disproved`, `finite_support`, and `unknown`. Finite checks cover their registered cases. The framework records the trust basis of program checkers, external Lean replays, and human attestations. [Workflow and implementation](docs/research-workflow.md) · [Operations](docs/operations.md) · [Trust boundaries](docs/architecture.md)

Both hosts use the same task broker. Managed research uses `target_progress_v2` to record derivations, load-bearing sources, and remaining gaps. The time and output limits in a task contract default to `null` and can be set explicitly. DSH-specific instructions and tests are maintained separately as legacy material. [Managed task contracts](docs/codex-monitored-contracts.md)

<details>
<summary>Initialize a workspace and select its policy manually</summary>

The coordinator uses the latest policy for new workspaces by default. Run these commands from the source root with a new workspace path to select it manually.

```powershell
python -m ai4math --workspace workspaces/my-research init --project-id my-research --title "My research"
$policyVersion = python -c "from ai4math.policy import VERSIONS; print(VERSIONS[-1])"
$policyRequest = @{to = $policyVersion} | ConvertTo-Json -Compress
python -m ai4math --workspace workspaces/my-research apply policy.migrate --json $policyRequest
```

An existing workspace uses its registered policy. Forward migration requires explicit authorization, resolved pending tasks, stopped workers, and settled rounds. It rebuilds the ledger from existing events and preserves registered inputs, evidence, and consumed budgets. [Workspace policy and migration](docs/operations.md#新版研究策略)

</details>

## Worked example · Counterexample diagnosis

One proposed route for First Proof Problem 5 looks for a common element of three pairwise sums in a hyperfield. A common element would carry a zero-sum relation into the sum of six cubic monomials. In one exact five-element example, the pairwise sums are $\lbrace a,d\rbrace$, $\lbrace b,d\rbrace$, and $\lbrace a,b\rbrace$. The sets intersect pairwise and have empty triple intersection. The six-term sum still contains zero in that case. [Problem statement](https://github.com/1stproof/batch-3/blob/5908aab1a1067d0e858294950537d0c471d7bb7f/batch-3-problems/problem-05/problem.tex)

1. **Trace the counterexample.** Find the proof routes and manuscript entries affected by the failed common-element argument. Keep the independently checked pairwise-intersection result for the finite table.
2. **Check a revision.** Add the premise that one pairwise sum is a singleton. Replay the original witness to see where the new premise excludes it.
3. **Compare two witnesses.** Find another triple on the same table with pairwise intersections and a common element. It shows which shared observations fail to determine the conclusion.
4. **Track the theorem's hypotheses.** Bind the set-theoretic lemma to an exact passage and record the singleton condition as an open obligation. A separate route asks if selecting and weighting one element from each sum can give zero.

```console
python -m examples.run_hyperfield_diagnosis workspaces/hyperfield-diagnosis
python -m ai4math --workspace workspaces/hyperfield-diagnosis/workspace diagnose --node common
python -m ai4math --workspace workspaces/hyperfield-diagnosis/workspace diagnose --node cubic
python -m ai4math --workspace workspaces/hyperfield-diagnosis/workspace diagnose --node singleton-route
```

The common-element argument is refuted within its registered scope. The original six-term statement remains open in the example workspace. [Diagnosis guide](docs/research-diagnosis.md)

## Testing and documentation

```console
python -m pip install -e . "pytest>=8,<10"
python -m pytest -q -p no:cacheprovider
python -m ai4math codex-skills check
```

The `v2c4` offline acceptance tests simulate Codex CLI and Claude Code with synthetic workspaces and the actual Python broker. The installed CLI versions were checked separately. Earlier release checks also ran the hyperfield example with real managed Codex and four independent validation tasks, and verified the bytes, SHA-256, and page-level excerpt of a 9,437,797-byte PDF. [`v2c4` tests](tests/test_research_flow_v2c4.py) · [Literature ingestion](docs/exploration.md) · [Source packaging](PUBLISHING.md)

| Topic | Guide |
| --- | --- |
| Proof obligations, rounds, and continuation | [Research workflow](docs/research-workflow.md) |
| Commands, verification, and evidence records | [Operations](docs/operations.md) |
| Native workers and managed contracts | [Host and Skill guide](docs/codex-research.md) |
| Exploration, sources, and literature requests | [Exploration](docs/exploration.md) · [Literature workflow](docs/literature-workflow.md) |
| Benchmarking and Inspect AI | [Benchmark operations](docs/benchmark-operations.md) |

With `inspect-ai` installed, `study inspect-export` exports registered evaluation records for reviewing usage, termination, and blinded reviews.

<details>
<summary>Related projects</summary>

| Project | Focus | AI4Math design |
| --- | --- | --- |
| Pharos | Portfolio coordination, proof search, and independent review in a shared fact graph. [Project](https://github.com/frenzymath/Pharos) | Task contracts bind first checks, follow-up work, termination, and outputs to proof obligations. |
| Danus | Parallel proof search over a shared graph of verified facts and dependencies. [Paper](https://arxiv.org/abs/2607.06447) | Claim records capture evidence levels, checker trust, and the effects of revoking a premise. |
| Archon Horizon | Long-running Lean 4 formalization across projects. [Project](https://github.com/archon-agi/horizon) | The evidence workflow records program checks, external formal replays, and human proof review. |
| OpenEvolve | Iterative generation, evaluation, and selection of candidate programs. [Project](https://github.com/algorithmicsuperintelligence/openevolve) | Frozen evaluation contracts preserve reproducible finite counterexamples and candidate revisions. |
| OSVerify | Mathematical error discovery, verification, and feedback audit. [Project](https://github.com/OSVerify/OSVerify) | Feedback and coverage connect to specific claims and their acceptance history. |

</details>

## Repository layout and local state

| Path | Contents |
| --- | --- |
| `ai4math/` | Core engine, CLI, policy, and verification |
| `docs/` | Architecture, operations, and research workflows |
| `.agents/skills/` and `.claude/skills/` | Project Skill instructions and Claude Code entry points |
| `examples/` and `templates/` | Reproducible examples and research templates |
| `tests/` and `scripts/` | Offline tests, Lean replay, and packaging tools |

Research state, academic libraries, and validation receipts live in local workspaces and data directories outside the source package. Each workspace has an append-only registry and content-addressed artifacts. Inside a workspace, `reports/research-summary.md` shows current progress and `reports/research-history.md` lists earlier rounds. [Round summaries and handoffs](docs/operations.md#研究总汇总与轮次交接)

## Contributing and license

Reproducible bug reports, independent tests, and documentation improvements are welcome. [Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md) · [Apache License 2.0](LICENSE) · [Notice](NOTICE)
