# 从 Codex 或 Claude Code 协调 AI4Math 研究

## 发现和冻结项目 Skill

从 AI4Math 源码根目录启动会话。Codex CLI 选择 `.agents/skills/` 下的项目 Skill。协调、研究和文献会话分别用 `$ai4math-coordinate`、`$ai4math-research` 和 `$ai4math-literature`。Claude Code 的项目入口使用对应的 `/ai4math-coordinate`、`/ai4math-research` 和 `/ai4math-literature`，从 `.claude/skills/` 转到同一份正文。通过工具委派原生子代理时，直接传递正文路径与冻结任务绑定。 [Codex 项目 Skill](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills) · [Claude Code 项目 Skill](https://code.claude.com/docs/en/skills#choose-where-skills-load)

```powershell
python -m ai4math codex-skills check
```

`check` 从项目读取 Skill 和 `agents/openai.yaml`，输出 SHA-256 的 JSON 清单。将清单保存到允许的输入位置，再用 `artifact.import` 冻结，并将它纳入任务输入。派工时根据冻结清单核验项目文件及任务目录里的 worker 副本。 [总控 Skill](../.agents/skills/ai4math-coordinate/SKILL.md) · [清单实现](../ai4math/codex_skills.py) · [派工实现](../ai4math/codex_runner.py)

## 新命题验收

在源码根目录的新会话里发送以下文字。

```text
请使用 $ai4math-coordinate 运行下面的新命题。

<粘贴文字命题>
```

总控 Skill 用当前源码中 `ai4math.policy.VERSIONS[-1]` 确认最新工作区策略。新工作区在准备首轮前采用该策略，明确固定版本的研究协议沿用其版本。续研时先读取已有策略，升级较旧策略须获得明确授权，并确认轮次已结算、没有待派发或运行中的任务。 [新版研究策略](operations.md#新版研究策略)

1. 从文字题面核对量词、定义域和结论。用户没有指定路径时，在 `workspaces/` 下用未占用的新目录初始化工作区，用同一个框架项目下的 `data/academic` 作为本地学术库。锁定主命题，登记当前证明义务。
2. 在导入证据和登记任务前执行 `research.configure`，使用 `mode=theory_first`、`exclude_history=true` 和显式 `allowed_roots`。默认只把本地学术库列为工作区外的允许来源。允许读取框架文档、本地通用方法和通用 Mathlib 引理。避开题目出处、同题成解、已有同题 Lean 声明和旧运行产物。记录实际资料读集及任务角色。 [干净起步实现](../ai4math/research.py)
3. 通过 `node.plan` 登记与目标相连的证明义务及其依赖。直接证明、直接反证或有界起步探索可以从主节点开始。按轮次协议冻结选定方向、承重首检、反证条件及复核方式。worker 先完成文献门禁，再执行获准的首检。
4. 有完整证明时，用锁定题面核对 Lean 4 声明并独立重放。绑定证明、独立验证回执与审查结论，满足条件后执行 `claim.accept`。收拢任务的实际终态，执行 `round.assess`、`round.settle`，导出只读 `goal_impact`。存在未决义务时保留实际证据等级和状态，并指出下一步。 [流程和文件索引](research-workflow.md)

已有工作区的主会话可使用下面的提示词。

```text
使用 $ai4math-coordinate。我的 AI4Math 工作区是 <工作区绝对路径>。
持续推进已锁定主命题。主会话负责文献关口、派工、独立验收和全局复盘。
每次复盘从完整证明图选择 3–5 个真正不同且可并行的证明义务。
保留原陈述、定义域、历史消耗、失败记录和候选证据等级。
派工前冻结本轮文献计划，运行中用队列与进度看板监控独立 worker。
文献回执核验后才放行首检。封存及进程停止后独立审查产物并记录回接条件。
遇到全局复盘到期，先登记方向决定再派新任务。
```

## 用 `/goal` 持续研究

从源码根目录新开 Codex 会话，将工作区换成实际路径，提交下面整行。总控 Skill 负责恢复进度、选择学术库、准备和结算每轮，以及判断目标完成或暂停。工作区无法确定所需学术库时，再提供库路径。 [总控 Skill 的持续研究规则](../.agents/skills/ai4math-coordinate/SKILL.md#coordinate-ongoing-work) · [Codex Goals 官方指南](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex)

```text
/goal 使用 $ai4math-coordinate 持续研究 <工作区绝对路径> 中锁定的主命题。
```

每轮结算并登记摘要后，总控在当前主会话发布一条进度汇报，再开下一轮。汇报依次展示轮号与主命题状态、`goal_impact`、独立复核与证据、剩余义务与下一步。纯验证轮和无数学进展的轮次也照此汇报。 [总控 Skill 的逐轮汇报](../.agents/skills/ai4math-coordinate/SKILL.md#report-each-round-in-the-main-chat)

## 启动一组独立任务

主会话先读 `strategy-brief`，登记本轮方向和统一文献计划，再冻结任务、方法审查和探索候选。`task.add` 填写常规 `id`、`route`、`role`、`argv`、`inputs` 等字段，并按当前研究策略绑定方法和 `exploration_candidate`。在使用项目原生子代理的 `explore` 或 `produce` 任务中加入以下配置。

```json
{
  "worker_driver": "subagent",
  "external_supervision": "process",
  "timeout_seconds": null,
  "output_limit_bytes": null,
  "codex_workflow": {
    "skill_manifest_artifact": "<已冻结的技能清单 ID>",
    "worker_actor": "<独立于主会话的归属标签>",
    "source_roots": ["<已授权且规范的学术库绝对路径>"]
  }
}
```

技能清单 ID 也应出现在 `inputs`。需要通过 broker 运行工具时，可附加项目已冻结的 `toolchain_artifact` 并加入同一输入列表。托管任务记录文献、等待审核、首检与封存阶段。常规研究将总时长和输出字节上限都显式设为 `null`。用户指定上限或评测协议要求固定预算时，填入对应的正整数。路线运行次数、全局复盘及证据关口继续生效。旧版限时合同按原规则执行。

超过单次 24 KiB 展示上限的合同会返回 `contract_sha256` 和 `prompt_artifact_id`。子会话用 `read` 按 ID 分页读取原冻结合同和提示稿，再读取合同中的 `input_artifacts`。分页保留原文件哈希和全部输入约束。

### 项目内子代理派工

先准备好各条任务，再按已授权的范围启动。以下示例使用 Codex CLI。在 Codex Desktop 或 Claude Code 中，分别填写 `codex-desktop` 或 `claude-code`。

```powershell
$workspace = (Resolve-Path 'workspaces/my-research').Path
$library = (Resolve-Path 'data/academic').Path
$task = 'my-branch-r1'
python -m ai4math --workspace $workspace --actor coordinator dispatch-subagent --task $task --library $library --host codex-cli
```

用宿主原生委派工具把任务 ID、工作区、库路径、冻结 `PROMPT.md`、`input.json` 和研究 Skill 路径交给子代理。子代理通过 broker 逐项操作。从项目根运行下面的第一条调用，随后按合同送出读取、查询和交回动作。

```powershell
python -m ai4math --workspace $workspace worker-task --task $task --library $library --action contract
```

主会话审核 `literature_ready` 回执后登记 `task.release_exploration`。收到交回后核对宿主状态和捕获记录，用 `seal-subagent --task TASK` 封存。宿主提供可核对的子代理标识时传入 `--host-run-id ID`。以实际观察登记 `task.confirm_stop`，登记独立 `adversarial` 和 `verify` 评审，再以同一清单和产物集登记 `task.finish_external`。核对本轮任务终态后评估、结算及汇报。 [交回与结算](research-workflow.md#托管外部任务如何进入轮次结算)

### Codex App Server 兼容入口

使用 App Server 时将任务的 `worker_driver` 设为 `codex`。`codex-run` 调用本机 App Server，将任务绑定到独立线程和工具 broker。`--library` 指向合同中显式允许的库路径。

```powershell
$workspace = (Resolve-Path 'workspaces/my-research').Path
$library = (Resolve-Path 'data/academic').Path
$codexExe = (Get-Command codex).Source
$task = 'my-branch-r1'
python -m ai4math --workspace $workspace --actor coordinator export-task $task
python -m ai4math --workspace $workspace --actor coordinator codex-run $task --codex-exe $codexExe --library $library
```

## 在主会话查看进展

原生子代理用宿主的子会话状态与任务 `live/` 下的 broker 回执核对进展。App Server 可以用队列的 `cursor` 继续增量读取。将上次返回的 `cursor` JSON 原样传给 `--after`，按任务 ID 保留事件序号。收到独立文献回执后及时核验原文阅读、假设、局限和最小首检，再登记 `task.release_exploration`。放行前保持等待。App Server 的心跳与进展提醒用于检查，保留原合同和证据等级。

`search` 默认使用 `kind: scan`。`reuse_passage` 接收 `passage_id`，将明确选中的归档段落按哈希纳入本任务记录。已冻结的文献直接 `read`。新合同缺原文时使用 `request_literature`，独立文献会话负责获取；本地证据关口按当前材料完成，等待中的请求可在检查点用 `literature_updates` 回收。子会话使用的库须同时符合合同的 `source_roots` 和工作区已有的 clean-start allowlist。参见[异步文献流程](literature-workflow.md)。

```powershell
python -m ai4math --workspace $workspace codex-queue --after '{}' --wait 15
python -m ai4math --workspace $workspace report --format html
```

浏览工作区的 `reports/research-progress.html` 查看全局关口、路线、运行阶段、待处理事项、结构化进度、纠偏送达与确认，以及已接纳和待审候选。页面由框架在本机原子刷新，不需要网页服务。页面展示受限的进度投影和工作区内的产物链接，原始协议与工具输出留在工作区日志中。

需要调整进行中的 App Server 尝试时，用实际队列记录中的任务、执行轮 ID 和触发事件序号来绑定纠偏。收到送达回执后，继续查看 worker 的明确确认。暂停与继续是协作式控制，暂停在安全阶段边界生效。停止须填写原因。

```powershell
python -m ai4math --workspace $workspace --actor coordinator codex-steer $task --turn $turnId --event $eventSeq --text '保留原参数域并核对两个端点'
python -m ai4math --workspace $workspace --actor coordinator codex-control hold $task
python -m ai4math --workspace $workspace --actor coordinator codex-control continue $task
python -m ai4math --workspace $workspace --actor coordinator codex-control stop $task --reason '记录已完成部分与未决障碍'
```

封存后由主会话核对实际返回、停止回执、冻结字节和作用域，用 `task.finish_external` 按封存清单登记各任务的终态。显式轮次在附属任务齐备后执行 `round.assess` 和 `round.settle`。局部结果按实际覆盖的义务回接，数学声明独立验证和接纳。全局关口到期时先登记方向决定。 [操作手册](operations.md) · [轮次与实现索引](research-workflow.md#托管外部任务如何进入轮次结算) · [独立证据与研究探索](exploration.md)

### Lean 工具链预检

正式 Lean 验证派工前可运行 `python -m ai4math lean-preflight --file lean-probe.json`。配置写入 `executable`、`executable_sha256`、`version`、`source`、`source_sha256` 和 `dependencies`。依赖列表的每项有 `path` 和 `sha256`，同一依赖正常出现两次时显式列两项。路径使用绝对路径，可用 `lean_path` 指定搜索目录。命令运行 `--version` 和 `--deps`，核对文件身份和精确出现次数，返回原始双流及结果。保存通过的预检回执后再登记验证任务。此步骤检查工具链和导入格式，证书仍需实际编译和独立审查。
