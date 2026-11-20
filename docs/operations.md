# 研究操作与验证协议

CLI 和 Python API 使用同一套策略检查。所有写操作为一个 SQLite 事务；有依赖的操作依次提交，不使用批量无序回写。以下 JSON 作为操作输入保存后，用 `apply operation --file path` 执行。返回码 0 表示相应操作完成；2 表示错误、审计失败、未满足交付条件或任务未正常完成。

## 命题与研究路线

先创建无依赖的子命题，再创建依赖它们的父命题。语义摘要由 statement、domain 和 quantifier 共同计算。创建 claim 时必须回填 API 返回的 statement_digest。

| 操作 | 必需字段 | 关键规则 |
| --- | --- | --- |
| node.add | id, statement, domain, quantifier | quantifier 为 finite 或 universal；可选 statement_dependencies、proof_dependencies；义务数已达软上限时须给 supersedes 或 debt_justification |
| goal.set | node | workspace 的主目标只能设置一次 |
| node.plan | node, proof_dependencies, reason | 只改证明计划，不改数学含义；禁止环；旧计划下的候选 claim 需重提 |
| route.add | id, node, rationale, next_question, stop_condition, max_runs, stagnation_limit | 初始 proposed，必须属于目标可达锥；同一义务已有路线时记录 continuation_of / parallel_with，并在 min_route_rounds>1 时要求当前路线已走满深度 |
| strategy.configure | gate_interval, min_route_rounds, obligation_stagnation_limit | 已启用工作区可在不重建的前提下调整关口与深度阈值，改动留痕 |
| route.status | id, status, reason | active、parked、closed；已关闭或预算耗尽不能原地重启 |
| route.checkpoint | route, finding, next_question | `outcome` 可为 `claim`、`finding`、`discharged`、`falsified` 或 `no-progress`。`falsified` 须附冻结反例或失败探针。进展信用按实际有效闭合或经复核的决策变化计算。 |

`stop_condition` 是交给研究者和审稿者的数学判断条件；程序直接执行的是次数和无进展上限。有限支持可以保存，但不算证明义务闭合。预算耗尽后可以明确提出一条有不同理由的新路线，没有自动重新分配预算的循环。

## 义务级记账与净债务

路线是一份针对某个义务的计划，停滞与消耗记在**义务**上，不记在路线名上：

- 每次派工给该义务加一次运行，每次 checkpoint 记一轮。新轮次产生统一的 `progress` 记录，由路线和义务共享。`advanced` 为真时清零停滞，为假时累加。原始 finding、完成任务、复核过的决策变化与有效闭合分别登记。
- 同一义务默认连续 12 轮没有合格进展后拒绝派工，可用 `obligation_stagnation_limit` 调整。`strategy.decide` 的显式 switch 或 replan 可释放选中义务的派工计数，既有进展信用和证据去重记录继续保留。 [进展资格及复盘](research-workflow.md#承重问题首检与策略复盘)
- 为同一义务新建路线不会重置计数：新路线记录 continuation_of 和继承的 obligation_rounds。
- 开放义务数达到软上限（8）时，新增叶子必须给出 supersedes=[...]（被替代的开放义务会被标记 superseded_by）或非空的 debt_justification；strategy-brief 的 obligations 与 obligation_debt 字段给出当前读数。

## 对应性门禁

模型（例如"两波 + 缓变帽"）只有在能把每条模型陈述映射回锁定节点时才算证据。对应性门禁把这件事变成阻塞条件：

- `correspondence` 记录一次映射：`node`、`verdict`（aligned / partial / mismatch）、
  `mapping` 行（model_claim、locked_target、status = mapped / open / mismatch）、以及至少一个冻结 artifact。
- 同一义务累计 `correspondence_after`（默认 6）个 checkpoint 后，若最近一次 checkpoint 之后没有对应的映射记录，
  为该义务新建路线会被拒绝（"record correspondence before opening another mechanism"）。
- `verdict=mismatch` 会直接置 halt：派工被拒，必须 replan 或关口评审。
- 评审者不能是最后一个派工者；以 human 身份评审必须给 `attestation`。

```bash
python -m ai4math --workspace WORKSPACE correspondence --file correspondence.json
```

模板见 `templates/correspondence.json`。

## 长周期关口与停机

策略关口（strategy.decide）每 3 轮一次，处理方向问题；长周期关口处理"要不要继续"的问题：

- `strategy.enable` 的 `gate_interval` 默认 20：每 20 个 `route.checkpoint` 轮次后，研究角色的派工被拒绝，
  直到用 `review-gate` 记录一次关口评审。评审必须给 `decision`（continue、switch、pause、close）、`reason` 和 `next_question`，
  并会把当时的健康面板指标与信号冻结进 `gate_review` 记录。
- 义务停滞达到上限时（默认 12 轮无合格进展），系统自动置 `halt`，任何研究派工都会被拒，
  只有记录一次 `review-gate`（decision 不是 pause）才解除；`pause` 则保留停机，等待人或下一次评审。
- `strategy-brief` 的 `gate` 字段给出 `due`、`halted`、`halt.reason`、`rounds_to_gate` 与上次评审摘要。
  持续运行的循环应把 `gate.due` 或 `gate.halted` 当作停止条件，而不是继续派工。

关口评审的独立性：评审者不能是最后一个派工者（`_last_dispatcher`），否则拒绝；
以 `human` 身份评审时必须给 `attestation`（一句关于看过什么、为什么继续的话），
这两条用来防止同一执行者在自己的循环里自签"continue"。

`review-gate` 用法：

```bash
python -m ai4math --workspace WORKSPACE review-gate --file gate.json
```

`gate.json` 至少包含 `decision`、`reason`、`next_question`；模板见 `templates/gate_review.json`。

### 机制深度

`min_route_rounds` 默认 1（不启用）。设为 k>1 后，为同一义务新建路线要求当前路线已有 k 个 checkpoint，
或最后一个 checkpoint 的 outcome 是 claim / discharged / falsified——否则拒绝，避免"每轮换机制"。
用 `strategy.configure` 在已有工作区上打开：

```bash
python -m ai4math --workspace WORKSPACE apply strategy.configure --json '{"min_route_rounds": 3}'
```

## 失败账本与重试上限

失败不是进度，也不该悄悄烧掉轮次预算：

- 每个终态失败（`failed`、`unknown_timeout`、`output_limit`、`interrupted`）都会写入一条 `failure` 记录，
  并按路线累计 streak；任务成功完成后 streak 归零。
- streak 达到 `retry_limit`（默认 2）时：该路线自动 `parked`（原因 repeated failure），并置 halt；
  必须登记 `strategy.decide`（switch/replan）或一次 `review-gate` 才能继续。
- `retry_limit` 可用 `strategy.configure` 调整。
- 健康面板的 `totals.failures`、`failures.streaks` 与 `failure_streak` 信号给出当前读数。

```bash
python -m ai4math --workspace WORKSPACE apply strategy.configure --json '{"retry_limit": 3}'
```

## 选择 worker 驱动

`dispatch-subagent` 从冻结合同准备原生子代理任务，由当前宿主发起实际委派。按实际宿主填写 `--host`。

```bash
python -m ai4math --workspace WORKSPACE dispatch-subagent --task TASK --host codex-cli --library LIB
```

Codex App Server 合同可用 `codex-run TASK --library LIB`。其预检同时冻结运行时版本和哈希。原生子代理通过 `worker-task` 操作任务，主会话通过宿主的状态和 broker 回执确认进展。 [项目 Skill 与派工](codex-research.md)

## 新版研究策略

新工作区可在准备首轮前用 `apply policy.migrate --json '{"to":"v2c4"}'` 启用这一策略。已有工作区需要先结束在途任务并结算轮次，再按授权迁移。

- `strategy.decide` 的 `research_steps` 按焦点路线 ID 键控。每项登记 `mode`（`direct`、`obligation` 或 `bootstrap`）、`probe` 和 `falsifier`。在主节点起步探索使用 `bootstrap`，范围截止第一次独立复盘。用 `node.plan` 登记与锁定目标相连的证明义务，继续研究时冻结当前依赖和证据依据。
- `review-gate --file FILE` 的 `next_action` 绑定 `kind` 和 `node`。可选 `decompose`、`validate`、`switch` 或 `blocked`。验收动作另带 `claim`，受阻动作另带 `blocker` 和 `reopen_condition`。独立关口可通过 `continuation` 登记 `node` 和当前阈值内的 `max_runs`。正式的续行授权保留历史消耗。
- 新研究任务预先绑定明确轮次。托管研究默认使用 `target_progress_v2` 交回。显式的 `continuation_of` 记录路线的实际前序。
- 失败后用新任务 ID 补跑。`task.supersede` 的 `replacement` 提供新 `task.add` 字段和新意图，沿用原任务角色和义务。迁移与补跑的登记都保留原始派工和结算记录。

### 独立验证请求

新版 `validate` 任务在 `task.add` 中提供 `validation_request`，并按通常方式指定 `validator_artifact` 和准确输入。根据用途填写下列字段。

| `purpose` | 输入 |
| --- | --- |
| `finite_replay` | `method`、`producer_task`、待复算的封存 `producer_artifacts` 和 `cases`。 |
| `candidate_score` | `producer_task`、唯一待评分产物组成的 `producer_artifacts` 和 `cases`。同时在任务上绑定探索候选及冻结评分协议。 |
| `claim` | `claim` 和 `method`。有限主张还需 `cases`。 |

检查器读取 `input.json` 中的 `result_contract`，按其绑定字段写入 `result.json`，提交布尔值 `passed`、检查项及限制。候选评分另填冻结协议中的指标。验收时查看 `task.validation_outcome`。其中 `process_status` 记录进程终态，`protocol_status` 为 `valid`、`rejected` 或 `not_run`，`verdict` 为 `passed`、`failed` 或 `unavailable`。合法的失败判决保留其范围，协议拒绝保留原始产物和原因。主张验收通过 `validation.add` 使用通过的冻结回执。

## 本地密钥（.env）

仓库根目录的 `.env` 保存本机密钥，已加入 `.gitignore`，不要提交：

```
DEEPSEEK_API_KEY=...
JUMU_ZEELIN_API_KEY=...
```

`ai4math.env.load()` 在 CLI 启动与 `codex-run` 派工前读取它。已存在的环境变量优先，并由本机启动的进程继承。用 `AI4MATH_ENV_FILE` 可以选择其他文件。

## 测试临时目录

测试在工作区内运行，不依赖工作区外的路径：仓库根目录的 `conftest.py` 在没有 `--basetemp` 时使用 `.pytest-tmp`，
并把目录创建模式改为 0o777（pytest 默认的 0o700 在 Windows 上会生成受保护 DACL，受限进程随后无法进入自己的临时目录）。
`.pytest-tmp` 是本机沙箱专用的临时目录：它单独去掉了"禁止删除子目录"的拒绝项，其它目录不受影响。

```bash
python -m pytest tests -q     # 工作区内运行，无需提权
```

该目录内容可随时清空；不要把研究材料放进去。

## 一轮一记录

同一个轮次的交回、验收与勘误不再各写一条：`summary --record` 的载荷带 `round` 与 `status` 时，
若该轮已有记录，就合并进同一条（保留原 id，追加 `transitions`），渲染出的历史里一轮只有一条。

| 字段 | 说明 |
|---|---|
| `round` | 轮次号（整数或字符串），用于查找同一轮的既有记录 |
| `status` | handoff、accepted、reviewed、erratum、closed 之一 |
| `transitions` | 自动追加：{from, to, at, actor, event} |

不带 `round` 的载荷保持原有行为（新记录追加一条）。

## 可选的评审与文献策略

两个开关默认关闭，用 `strategy.configure` 打开：

| 设置 | 取值 | 效果 |
|---|---|---|
| `adversarial_review` | true / false | 打开后，接受任何结论前必须已有一条 `stance: adversarial` 的评审（reviewer 仍需与作者、auditor 不同） |
| `literature_mode` | always / on-demand | on-demand 时，带 `literature` 块的任务必须在 `literature.need` 写明缺失的机制或假设，否则拒绝派工 |

```bash
python -m ai4math --workspace WORKSPACE apply strategy.configure --json '{"adversarial_review": true}'
python -m ai4math --workspace WORKSPACE apply strategy.configure --json '{"literature_mode": "on-demand"}'
```

评审记录里加 `"stance": "adversarial"` 即视为反方评审；不写则保持原有的支持性评审语义。

## 存储构成

`python -m ai4math --workspace WORKSPACE storage`（`--json` 可机器读）只读统计字节去向：按目录（objects、tasks、reports、exports…）
与按用途（bookkeeping = json/jsonl、raw-streams、build = olean、logs、source = lean/py、prose）分组，并给出
"簿记 + 原始流"占比。决定保留策略时先看这个数，而不是凭印象。

## 只读健康面板

`python -m ai4math --workspace WORKSPACE health` 输出义务级每轮消耗、停滞、距上次记录进展的轮数、已接纳结论数、
开放义务数与债务总量；加 `--json` 得到快照。它不写任何记录，也不改预算。

合成工作区的示例输出如下。

```
goal: demo-goal (open)
obligations open: 3 / nodes 4 | routes 5 (1 active) | accepted claims 1
obligation rounds: 4 | tasks 6 (running 0, failed 1) | artifacts 20

node                              rounds  runs  stagnant  since claim  claims
bridge-lemma                           2     3         0           2       0
```

信号分三级：`stop`（停滞已达上限，派工会被拒）、`warn`（长干旱、债务、范围膨胀、文献负载）、`info`。
老工作区的路线没有义务记录时，面板会回退到路线 checkpoint 统计，因此历史轮次仍可见。

## 冻结材料和提出结论

`artifact.import` 输入 `path` 和 `purpose`，返回 64 位十六进制 artifact ID。源路径可以位于项目之外；副本和回执只写到 workspace。相同字节去重，同一字节从不同路径或用途导入会保留各自回执。

`claim.add` 必需字段：

```json
{
  "id": "candidate-lemma-1",
  "node": "goal",
  "statement_digest": "REPLACE_WITH_NODE_STATEMENT_DIGEST",
  "evidence_level": "unknown",
  "scope": "universal",
  "summary": "Describe the exact conclusion and remaining gap.",
  "artifact_ids": [],
  "dependencies": []
}
```

claim 的 dependencies 引用已接纳的 proved claim。它们不是 node ID，也不是文件路径。有限实验支持全称目标时使用 `evidence_level=finite_support, scope=finite`；该 claim 不关闭全称目标。反驳全称命题可用有限反例，但必须通过 counterexample 验证。

## 导出和运行任务

`task.add` 创建 pending 任务；模板见 `templates/task.json`。角色为 explore、produce、validate 或 review。检查器任务使用冻结的 Python 文件，固定 argv：

```json
{
  "id": "check-lemma-1",
  "route": "route-1",
  "role": "validate",
  "argv": ["ABSOLUTE_CURRENT_PYTHON_EXE", "{validator}", "{input}"],
  "validator_artifact": "SHA256_OF_IMPORTED_VALIDATOR",
  "inputs": ["SHA256_OF_IMPORTED_ARGUMENT_OR_WITNESS"],
  "timeout_seconds": 60,
  "output_limit_bytes": 1048576
}
```

当前 Python 路径由 `python -c "import sys; print(sys.executable)"` 获取。普通任务允许其他非空 argv 数组。占位项必须是完整参数：`{input}` 替换为 input.json，`{validator}` 替换为冻结检查器，`{workdir}` 替换为任务目录；不会做 shell 展开。

新任务省略 `timeout_seconds` 和 `output_limit_bytes` 时默认为 `null`，也可显式填写 `null`。需要硬截止时填写正整数。无字节截止的任务完整留存本地输出、结果文件、模型和工具原始流及封存产物。工具预览继续分页或截短。路线次数、冻结输入、独立验证与停止控制继续生效，旧合同按其冻结配置运行。

```powershell
python -m ai4math --workspace workspaces/my-research export-task check-lemma-1
python -m ai4math --workspace workspaces/my-research --actor independent-validator run check-lemma-1
```

export-task 只导出契约和提示，不运行命令、不占用预算。可把 PROMPT.md、input.json 和显式输入交给外部代理；这不是自动调用代理的入口。run 才占用一次路线额度并执行。一个任务 ID 只运行一次，预先存在 result.json 或被改写的 input.json 会阻止运行。

## 全局复盘与多会话派工

0.6 新工作区默认每 3 个执行轮复盘，初始化时可用 `--review-interval` 指定间隔。已有工作区使用 `apply strategy.enable --json '{}'` 显式启用，从启用时开始计数。`explore` 和 `produce` 进入终态各计一轮，失败、超时、输出受限和明确恢复也计入。验证和审稿任务不计轮次。

登记关联开放证明义务的独立反例，或接纳目标依赖范围内的 `disproved` 主张，会提前触发。路线最后一次执行进入终态，或检查点达到停滞上限，也会触发。最后一个运行额度在启动时预留，其他可用路线可以在最后一项执行收尾前派工。

`status` 和 `strategy-brief` 从工作区生成全局简报。它包含锁定目标、证明依赖、开放及已就绪义务、路线与剩余额度、已接纳结论、可复查障碍、在途任务及 `brief_sha256`。复盘到期后，`task.add`、待执行任务的 `export-task`、`run` 和 `start-external` 会暂停。已启动的工作可以收尾，证据登记和独立验收可以继续。先结清在途任务，再把最新简报的 SHA 写入决定文件。

```json
{
  "id": "review-01",
  "brief_sha256": "REPLACE_WITH_LATEST_BRIEF_SHA256",
  "decision": "switch",
  "next_route": "lemma-a-route",
  "parallel_routes": ["lemma-b-route", "lemma-c-route"],
  "reason": "The original route has reached its stop condition. These obligations can be investigated independently.",
  "next_question": "Identify the first load-bearing step for lemma A."
}
```

```powershell
python -m ai4math --workspace $ws strategy-brief
python -m ai4math --workspace $ws apply strategy.decide --file research-inputs/review-01.json
```

`continue` 选择当前主路线，`switch` 选择另一条可用路线。`replan` 提供 `plan` 对象，包含 `node` 和新的 `proof_dependencies`。重拆与决定在同一个事务里提交，命题陈述和域沿用锁定版本。重拆可先省略 `next_route`，待新依赖进入目标范围后登记对应路线。主动复盘也可以登记决定。

多会话模式由主会话选择一条主路线和最多四条额外路线。并行路线必须对应不同的已就绪义务，且目标之间没有依赖。独立义务不足时按实际数量派工。工作区每条路线同时只运行一个任务，多个会话共享轮次和关口。主会话负责验收及决策，`--actor` 用于记录归属。

派给外部会话的 `explore` 或 `produce` 任务默认不设阶段截止。若配置 `phase_budgets`，四阶段时长之和必须等于 `timeout_seconds`。进程监督使用 `external_supervision: "process"` 和 `supervise-external <任务ID> --worker-argv-file <JSON数组文件>`，宿主监督使用 `start-external` 及停止确认。启动时预留路线额度并冻结任务包，worker 依次完成文献关口、首检与封存，协调方在停止后验收。 [外部截止协议](external-deadlines.md)

## 独立验证

检查器读取传入的 input.json，检查实际命题、参数域、字节输入和数学证书，成功时在当前任务目录写 result.json。协议为：

```json
{
  "task_id": "check-lemma-1",
  "statement_digest": "EXACT_NODE_STATEMENT_DIGEST",
  "input_artifacts": ["SORTED_EXACT_INPUT_IDS"],
  "method": "finite_certificate",
  "scope": "finite",
  "evidence_level": "proved",
  "passed": true,
  "checks": ["Independent necessary lower bound and attaining witness checked"],
  "limitations": ["Only the explicitly bound finite instance"]
}
```

必须精确对应任务 ID、命题摘要、全部输入、范围和证据等级，passed 必须是 JSON 布尔值 true。checks 与 limitations 为非空字符串数组。程序即使输出“passed”并以 0 退出，没有合格的 result.json 也不能登记验证。该协议保证绑定关系，检查器内部数学逻辑需要独立审查。

`validation.add` 需要 id、claim、method、trust_basis 和 task。方法是 finite_certificate、exhaustive_search、counterexample 或 formal_kernel。有限不可行叙述应包含容量证书，或确实完成的完整搜索日志；求解器超时不是证书。验证任务必须包含 claim 的全部 artifact_ids，运行身份和登记身份不能与作者相同。checks、假设和工具版本应进入冻结结果与 trust_basis。

人工论证另用 human_proof，提供绑定于 claim 的 artifact_ids 和逐字声明：

```text
I checked the exact statement, domain, dependencies and attached argument
```

人工证明必须由确实完成审查的人登记。自动程序不能借一个名为 human 的字符串冒充实际审查；框架没有账号认证，报告会显式展示 human_attestation 信任类型。不能用人工作证入口将一个模型判断改写为 proof。

## 审稿和反馈审计

`review.add` 输入 id、claim、reviewer、reviewer_kind、auditor、coverage_complete、issues、verdict。作者、reviewer 和 auditor 三者必须不同，提交 actor 必须等于 auditor。reviewer_kind 为 human、model 或 automation，按真实来源记录。

每条 issue 包含 description、severity 和 audit。severity 为 blocking 或 minor；audit 为 confirmed、rejected、unresolved。只有 coverage_complete=true 且不存在未排除的 blocking 问题时，才允许 verdict=ready_for_human。其他 verdict 为 revise、reject、inconclusive。反馈审计不修改原提交论证，修正论证后另建 claim。

`claim.accept` 提供 claim、最新 review，并为 proved/disproved 提供 validation。接纳 actor 与作者、审稿者、反馈审计者不同；这是归属检查，不是人员认证。接纳时重新核验字节、证书绑定和全部证明前提。只有最新审稿为 ready_for_human 才能接纳。

`claim.revoke` 提供 claim 与 reason，传播到所有直接和间接依赖 claim。旧事件、证明和文稿条目均保留；台账将被撤销的前提标为 BLOCKED，交付检查失败。

## 恢复与交付

崩溃后先检查并停止对应 PID，再用 `task.recover`，输入 task、reason、`process_confirmed_stopped=true`。恢复只标 interrupted；重跑创建新任务，不能重用 ID 或把未知结果变为成功。

`ledger.add` 输入 id、node、claim、text、use。use=theorem 仅允许 proved；observation 允许 finite_support 或 proved；conjecture 允许 unknown；counterexample 允许 disproved。文本含义仍需语义审查，字段约束不能理解任意自然语言。

`delivery-check` 要求主目标已经 proved/refuted、台账有对应主目标条目、没有撤销前提且完整性审计通过。它检查研究材料可交付条件，不提供新的数学证明，也不表示论文新颖性或期刊接受。

## 研究总汇总与轮次交接

工作区的 `reports/research-summary.md` 是研究者入口。创建工作区、验收、接纳或撤销声明、登记路线检查点、暂停和停机确认时，框架刷新登记状态和产物链接。`reports/research-history.md` 可回查已登记的轮次叙述、路线检查点、终态任务和验收回执。查看实时任务用 `reports/research-progress.html`。

每轮验收或暂停交接时，协调者用 `summary --record` 提交经过核对的数学叙述。可先用 `artifact.import` 冻结需要链接的额外推导或评审记录。旧轮次可用 `kind: historical` 补录。同一 ID 和内容可以重试，有修订时用新 ID 并在叙述中指明前一条记录。原始轮次记录追加到 `reports/rounds/`，汇总文件由框架生成。

```json
{
  "id": "round-01-review",
  "kind": "review",
  "title": "第 1 轮验收",
  "progress": "写本轮确实推出或证伪的结论，区分候选推导与独立接纳。",
  "scope": "写参数、假设、有限覆盖范围和适用域。",
  "gaps": "写仍缺哪一步，以及它影响哪个目标或证明义务。",
  "next_step": "写恢复后先解决的数学问题。暂停时也记录仍待确认的停机。",
  "tasks": [],
  "claims": [],
  "artifacts": [],
  "passages": []
}
```

`tasks`、`claims` 使用工作区登记 ID。`artifacts` 使用已冻结的 SHA-256。`passages` 只填本轮实际选用的段落 ID。用 `--library` 从指定学术库定位段落和原件，省略时使用本工作区已附加的段落。汇总链接到实际版本、定位和归档字节，学术库原件仍保存在学术库中。新叙述记录和当前投影的证据状态都可追查，暂停请求按注册表记录的实际停机状态展示。

```powershell
python -m ai4math --workspace $workspace --actor coordinator summary --record round-01.json --library $library
python -m ai4math --workspace $workspace summary
```

第二条命令可用于初始化已有工作区的汇总和重试刷新。历史检查点未登记的字段沿用其现存记录。

报告与事件导出只允许写到 workspace 的 exports/ 下。导出后把审计 tail_hash 存在外部，后续可用 `audit --anchor <sha>` 检查是否与该锚一致。新增合法事件也会改变 tail_hash，因此锚比较用于固定快照核对。

## 理论优先的新研究接口

先理论后验证工作流使用 research.configure、method.add、method.review 和 research.note。配置 theory_first 后，任务还必须指定 method、activity、learning_question；focused_probe 要额外声明 cases。activity 为 symbolic_analysis、focused_probe、validation 或 review，分别匹配探索生产、验证和审稿角色。 [探索指南](exploration.md)

新 finite_support claim 还必须指定 evidence_domain，写明实际有限实例或来源中可核对的范围，且绑定具体 artifact。已有旧库保持原字节，不因升级补写历史数学证据。0.2 已导出的 pending 任务若契约不一致，需要重新建任务 ID；已完成任务与冻结材料继续保留。
## 外部研究的两次文献关口

主会话先以 `strategy.decide` 登记下一方向和焦点路线。该次决定是检索与统一计划的共同绑定。命令行 `init` 新建的工作区默认要求探索和生产任务经过这套流程，单路线也适用。纯本地脚本可在 `init` 时显式选用 `--local-research`。Python 调用 `Workbench.create` 时可用 `external_research=True` 启用。已有工作区可用 `apply literature.enable --json '{}'` 启用。工作区一旦选择并行路线，后续即使改为单路线也继续执行此流程。独立验证与审阅仍沿用原入口。

新合同的两个关口评估现有的本地资料。需新增获取的文献交给独立会话，并把请求 ID 和影响记在检索记录中。可先按本地资料完成评估、放行和独立首检。见[异步文献流程](literature-workflow.md)。

当复盘已形成续作方向、派工条件尚未齐备时，可提交 `decision: "switch"` 和 `defer_dispatch: true`，留空焦点路线。框架结清本轮复盘并拦截探索和生产任务的准备、导出和启动。恢复时另登记一个绑定可用路线的决定，再走常规文献和派工关口。

1. 主会话分别检索各焦点路线的领域文献并核对原文。策略决定登记后，用同一 actor 通过 `artifact.import` 首次冻结本轮检索记录，再为每条路线执行 `apply literature.search --file …`。JSON 字段为 `decision`、`route`、`queries`、`search_artifact`、`sources`、`limitations`。每项来源含 `url`、`locator`、`finding`、`artifact_id`。选定的原文或阅读材料可按原摘要引用。无合适原文时使用空 `sources`，并保留实际检索记录与范围。
2. 主会话在全部路线的回执登记后评估各路线，首次冻结统一计划文档并执行 `apply literature.plan --file …`。JSON 字段为 `decision`、`assessment`、`plan_artifact` 和 `routes`。其中 `routes` 是包含全部且仅包含已选路线的对象，键为路线 ID，值为针对该路线的探索计划。计划齐全前，框架阻止新建、导出待派研究任务和启动执行，`status` 不会把这些路线列为可立即执行。每次登记新策略决定都要重新检索并制定计划，包括继续同一路线。已有记录按原 ID 保留。
3. `task.add` 将计划、检索记录和来源材料纳入冻结输入。主会话只启动当前决定允许的任务。框架核对冻结的 `PROMPT.md` 和 `input.json`，登记开始时间、合同 SHA-256 和 `external_stage=literature`。子会话用独立 actor 读取全部输入、检索并冻结自己的检索记录，执行 `apply task.literature_ready --file …`。JSON 字段为 `task`、`contract_sha256`、`read_input_artifacts`、`queries`、`search_artifact`、`sources`、`limitations`、`exploration_plan`。字段清单也随任务合同交付。
4. 子会话在收到派工后，用提交回执的同一 actor 首次导入本任务专用的检索记录。`exploration_plan` 写清独立调研如何影响计划及第一个可证伪检验。新合同（`worker_receipt_version=3`）的回执返回 `awaiting_coordinator_review` 和 `worker_receipt_sha256`。子会话把摘要交回主会话，等待放行。`strategy-brief` 的在途任务也会显示该摘要。
5. 主会话核对来源原文、适用条件和首个检验，用派工时的 actor 执行 `apply task.release_exploration --file …`。JSON 字段为 `task`、`worker_receipt_sha256` 和 `assessment`。框架复核冻结输入、回执绑定和角色，登记放行后返回 `external_stage=exploration`。各任务可分别放行，子会话收到放行后开始探索与实验。
6. 首检后用 `task.begin_seal` 进入封存，再用 `task.submit_seal` 提交 SHA-256 清单。输出在放行后首次导入，清单在封存开始后首次导入并绑定原输入和候选。设置了截止的合同须在截止前提交。`external_stage=sealed` 表示待验收，监督器停止本机 worker 树并登记确认。主会话用同一输出集合和清单调用 `task.finish_external` 登记终态。预绑显式轮次时，先完成各任务的终态登记，再用 `round.assess` 和 `round.settle` 评估及结算。超期或失败的产物可存档，数学证据按独立验收登记。 [流程与实现索引](research-workflow.md#托管外部任务如何进入轮次结算)

