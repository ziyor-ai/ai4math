# 用 Codex 主会话长期推进 AI4Math 研究

## 安装和冻结技能

在 AI4Math 源码目录执行下面的命令。安装器将仓库中的技能及 `agents/openai.yaml` 复制到 `~/.agents/skills`，默认保留相同字节并拒绝覆盖有差异的受管文件。已安装旧版时使用 `codex-skills install --upgrade`。其他技能和同目录的本地文件保留。

```powershell
python -m ai4math codex-skills install
python -m ai4math codex-skills check
```

`check` 核对仓库和已安装的文件，输出技能文件 SHA-256 的 JSON 清单。把该 JSON 保存到项目允许的输入位置，再用 `artifact.import` 冻结，作为新任务的一个输入。研究使用 `$ai4math-coordinate` 和 `$ai4math-research`，独立文献会话使用 `$ai4math-literature`。 [OpenAI 官方技能发现与调用说明](https://learn.chatgpt.com/docs/build-skills)

主会话可使用这个可复用的目标提示词。

```text
使用 $ai4math-coordinate。我的 AI4Math 工作区是 <工作区绝对路径>。
持续推进已锁定主命题。主会话负责文献关口、派工、独立验收和全局复盘。
每次复盘从完整证明图选择 3–5 个真正不同且可并行的证明义务。
保留原陈述、定义域、历史消耗、失败记录和候选证据等级。
派工前冻结本轮文献计划，运行中用队列与进度看板监控独立 worker。
文献回执核验后才放行首检。封存及进程停止后独立审查产物并记录回接条件。
遇到全局复盘到期，先登记方向决定再派新任务。
```

## 启动一组独立任务

主会话先读 `strategy-brief`，登记本轮方向和统一文献计划，再冻结任务、方法审查和探索候选。托管 Codex 是新任务的显式合同模式。`task.add` 仍填写常规 `id`、`route`、`role`、`argv`、`inputs`、`output_limit_bytes` 等字段，并按现有研究策略绑定方法与 `exploration_candidate`。在有独立文献关口的 `explore` 或 `produce` 任务中加入以下配置。

```json
{
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

先准备好各条任务再分别启动。`codex-run` 调用本机 App Server，将每个任务绑定到独立线程和工具 broker。`--library` 必须指向合同中显式允许的库路径。

```powershell
$workspace = (Resolve-Path 'workspaces/my-research').Path
$library = (Resolve-Path 'data/academic').Path
$codexExe = (Get-Command codex).Source
$task = 'my-branch-r1'
python -m ai4math --workspace $workspace --actor coordinator export-task $task
python -m ai4math --workspace $workspace --actor coordinator codex-run $task --codex-exe $codexExe --library $library
```

## 主会话操作

在主会话用队列的 `cursor` 继续增量读取。将上次返回的 `cursor` JSON 原样传给 `--after`，按任务 ID 保留事件序号。收到独立文献回执后及时核验原文阅读、假设、局限和最小首检，然后登记 `task.release_exploration`。等待主会话时不运行首检。运行中的心跳用于监控，15 分钟无结构化进度的提醒用于检查，提醒不改变原合同或证据等级。

`search` 默认使用 `kind: scan`。`reuse_passage` 接收 `passage_id`，将明确选中的归档段落按哈希纳入本任务记录。已冻结的文献直接 `read`。新合同缺原文时使用 `request_literature`，独立文献会话负责获取；本地证据关口按当前材料完成，等待中的请求可在检查点用 `literature_updates` 回收。子会话使用的库须同时符合合同的 `source_roots` 和工作区已有的 clean-start allowlist。参见[异步文献流程](literature-workflow.md)。

```powershell
python -m ai4math --workspace $workspace codex-queue --after '{}' --wait 15
python -m ai4math --workspace $workspace report --format html
```

浏览工作区的 `reports/research-progress.html` 查看全局关口、路线、运行阶段、待处理事项、结构化进度、纠偏送达与确认，以及已接纳和待审候选。页面由框架在本机原子刷新，不需要网页服务。页面展示受限的进度投影和工作区内的产物链接，原始协议与工具输出留在工作区日志中。

需要调整进行中的尝试时，用实际队列记录中的任务、执行轮 ID 和触发事件序号来绑定纠偏。收到送达回执后，继续查看 worker 的明确确认。暂停与继续是协作式控制，暂停在安全阶段边界生效。停止须填写原因。

```powershell
python -m ai4math --workspace $workspace --actor coordinator codex-steer $task --turn $turnId --event $eventSeq --text '保留原参数域并核对两个端点'
python -m ai4math --workspace $workspace --actor coordinator codex-control hold $task
python -m ai4math --workspace $workspace --actor coordinator codex-control continue $task
python -m ai4math --workspace $workspace --actor coordinator codex-control stop $task --reason '记录已完成部分与未决障碍'
```

封存后由主会话核对实际返回、停止回执、冻结字节和作用域。局部结果按实际覆盖的义务回接。全局关口到期时先登记方向决定，保持独立数学验证和声明接纳流程。 [操作手册](operations.md) · [独立证据与研究探索](exploration.md)

### Lean 工具链预检

正式 Lean 验证派工前可运行 `python -m ai4math lean-preflight --file lean-probe.json`。配置写入 `executable`、`executable_sha256`、`version`、`source`、`source_sha256` 和 `dependencies`。依赖列表的每项有 `path` 和 `sha256`，同一依赖正常出现两次时显式列两项。路径使用绝对路径，可用 `lean_path` 指定搜索目录。命令运行 `--version` 和 `--deps`，核对文件身份和精确出现次数，返回原始双流及结果。保存通过的预检回执后再登记验证任务。此步骤检查工具链和导入格式，证书仍需实际编译和独立审查。
