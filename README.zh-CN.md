# AI4Math

[English](README.md)

AI4Math 是面向长周期数学研究的本地 Python 工作台。它围绕锁定命题和证明义务组织研究，协调执行与独立验证，并保存跨会话续研所需的证据。

当前软件包版本为 `0.7.1`，源码支持的最新工作区研究策略为 `v2c4`。

## 快速开始

使用 Python 3.10 或以上版本。核心运行时依赖 Python 标准库和 SQLite。在仓库根目录运行离线示例，工作区须使用尚未存在的路径。

```console
python -m pip install -e .
python -m ai4math --version
python -m examples.run_finite_demo workspaces/finite-demo
python -m ai4math --workspace workspaces/finite-demo status
python -m ai4math --workspace workspaces/finite-demo audit
python -m ai4math --workspace workspaces/finite-demo delivery-check
```

独立的生产与验证程序核对有限图 $P_2^4$ 的最小互素标号数。示例使用合成审稿角色，结论由可检查的有限证书支持。

## 在 Codex 或 Claude Code 中研究

从 AI4Math 源码根目录打开会话。Codex 使用 `.agents/skills/` 下的项目 Skill，Claude Code 从 `.claude/skills/` 的入口读取同一份正文。 [项目 Skill 手册](docs/codex-research.md)

| 用途 | Codex | Claude Code |
| --- | --- | --- |
| 协调研究、复盘和验收 | `$ai4math-coordinate` | `/ai4math-coordinate` |
| 执行已冻结的研究任务 | `$ai4math-research` | `/ai4math-research` |
| 处理独立文献请求 | `$ai4math-literature` | `/ai4math-literature` |

### 运行新命题

```text
请使用 $ai4math-coordinate，在新工作区研究下面的命题。

证明对任意自然数 n，存在整数 N > n，使 N、N+1、N+2 分别都能表示为两个整数的平方和。三组平方见证可以不同，允许使用 0。
```

Claude Code 先调用 `/ai4math-coordinate`，再提供同一题面。省略路径时，新命题流程默认使用 `workspaces/` 下的独立工作区和 `data/academic/` 下的本地学术库。总控锁定题面，登记证明义务，冻结首轮策略和文献计划。 [新命题流程](docs/codex-research.md#新命题验收)

### 续研已有工作区

在 Codex 中，把下面的占位符换成实际工作区路径。

```text
/goal 使用 $ai4math-coordinate 持续研究 <工作区绝对路径> 中锁定的主命题。
```

Skill 从最新登记的检查点恢复，沿用工作区证据和预算。每轮结算并登记摘要后，总控先在主会话汇报轮号与主命题状态、`goal_impact`、复核证据及剩余义务与下一步，再开下一轮。锁定命题的证明或严格反证正式通过验收，且任务结清、验收材料归档后，持续目标完成。 [持续研究说明](docs/codex-research.md#用-goal-持续研究)

```console
python -m ai4math --workspace workspaces/my-research strategy-brief
python -m ai4math --workspace workspaces/my-research health
```

### 只准备合同供审阅

```text
使用 $ai4math-coordinate。工作区为 workspaces/my-research，学术库为 data/academic。
我的研究命题是 <命题、参数域和已知假设>。
请登记目标和未解的证明义务，查阅学术库并导出首轮 PROMPT.md 与 input.json。本次授权仅限合同准备。
```

导出的合同包含已冻结的输入。获准启动托管任务时，框架提供项目研究 Skill，并核对其哈希与冻结清单。 [合同准备与派工](docs/codex-research.md)

需要补充原文时，在独立文献会话中使用同一个学术库，并要求记录所用版本、原文段落和适用假设。检索结果或限制可按请求 ID 发布。 [文献工作流](docs/literature-workflow.md)

## 研究流程

1. **锁定命题。** 明确陈述、定义域和量词，用 `node.plan` 把证明义务接回主命题。
2. **选择方向。** 登记候选机制、首个承重检验和证伪条件，在策略与任务合同中冻结本轮证明依据和选定来源。
3. **执行与独立验证。** 先审核研究任务的文献回执，再放行首检。按 SHA-256 绑定输入、检查器、产物和回执，分别记录进程终态、`result.json` 协议状态和复核判决。
4. **接受证据并结算。** 按实际适用范围登记主张，路线延续时累计已用预算。独立复盘可优先安排承重候选的验证，或明确阻塞下一步的前提。

主张的证据等级为 `proved`、`disproved`、`finite_support` 或 `unknown`。有限检查覆盖登记的算例。框架记录程序复核、外部 Lean 重放和人工证明审查的信任依据。 [流程与实现](docs/research-workflow.md) · [操作手册](docs/operations.md) · [信任边界](docs/architecture.md)

两个项目入口共用任务 broker。托管研究用 `target_progress_v2` 记录推导、承重来源和剩余缺口。任务时限和输出上限默认为 `null`，可在合同中显式设置。DSH 专用指引与测试按历史版本单独保留。 [托管任务合同](docs/codex-monitored-contracts.md)

<details>
<summary>手动初始化工作区并选择策略</summary>

总控为新工作区默认采用最新策略。手动选择时，从源码根目录运行以下命令，使用尚未存在的工作区路径。

```powershell
python -m ai4math --workspace workspaces/my-research init --project-id my-research --title "My research"
$policyVersion = python -c "from ai4math.policy import VERSIONS; print(VERSIONS[-1])"
$policyRequest = @{to = $policyVersion} | ConvertTo-Json -Compress
python -m ai4math --workspace workspaces/my-research apply policy.migrate --json $policyRequest
```

已有工作区沿用已登记策略。前向迁移须取得明确授权，先处置待派发任务、结束在途任务并结算开放轮次。迁移从历史事件重建账本，保留登记材料、证据和已用预算。 [策略与迁移](docs/operations.md#新版研究策略)

</details>

## 示例 · 发现反例后如何续研

First Proof 第 5 题的一条候选路线是在超域中为三个二次和寻找共同元素，用它把零和关系带到六项三次和里。在一个精确的五元超域算例中，三个二次和分别为 $\lbrace a,d\rbrace$、$\lbrace b,d\rbrace$ 和 $\lbrace a,b\rbrace$。它们两两相交，三者交集为空。该算例的六项三次和仍包含零。 [公开题面](https://github.com/1stproof/batch-3/blob/5908aab1a1067d0e858294950537d0c471d7bb7f/batch-3-problems/problem-05/problem.tex)

1. **追踪反例的影响。** 找到依赖共同见证的路线和文稿条目，保留有限超域表上经独立检查的两两相交结论。
2. **重放修订。** 加入「至少一个二次和是单元素集合」的条件，核对旧见证在哪一步被排除。
3. **比较两个见证。** 在同一超域表上找出另一组三个二次和两两相交且有共同元素的三元组，定位现有观测无法决定的部分。
4. **跟踪定理的假设。** 绑定集合论引理的原文，把单元素条件登记为开放义务。另一路线考察从三个二次和分别选取一个元素后，三项加权和能否取到零。

```console
python -m examples.run_hyperfield_diagnosis workspaces/hyperfield-diagnosis
python -m ai4math --workspace workspaces/hyperfield-diagnosis/workspace diagnose --node common
python -m ai4math --workspace workspaces/hyperfield-diagnosis/workspace diagnose --node cubic
python -m ai4math --workspace workspaces/hyperfield-diagnosis/workspace diagnose --node singleton-route
```

共同见证论证在登记范围内已被反驳。演示工作区中的六项三次和原命题仍开放。 [诊断流程](docs/research-diagnosis.md)

## 测试与文档

```console
python -m pip install -e . "pytest>=8,<10"
python -m pytest -q -p no:cacheprovider
python -m ai4math codex-skills check
```

`v2c4` 离线验收使用合成工作区，模拟 Codex CLI 和 Claude Code 并运行实际的 Python broker，另行核对已安装的 CLI 版本。早期发布验收还使用真实托管 Codex 运行超域演示，完成四项独立验证，并核对一份 9,437,797 字节 PDF 的字节、SHA-256 和页码摘录。 [`v2c4` 离线回归](tests/test_research_flow_v2c4.py) · [文献归档](docs/exploration.md) · [源码打包](PUBLISHING.md)

| 主题 | 入口 |
| --- | --- |
| 证明义务、轮次与续研 | [研究流程](docs/research-workflow.md) |
| 命令、独立验证与证据登记 | [操作手册](docs/operations.md) |
| 原生子代理与托管合同 | [宿主与 Skill](docs/codex-research.md) |
| 探索、来源与文献请求 | [探索](docs/exploration.md) · [文献工作流](docs/literature-workflow.md) |
| 基准评测与 Inspect AI | [基准操作](docs/benchmark-operations.md) |

安装 `inspect-ai` 后，可用 `study inspect-export` 导出已登记的评测记录，用于复查用量、停机和盲审。

<details>
<summary>相关项目</summary>

| 项目 | 侧重点 | AI4Math 的对应设计 |
| --- | --- | --- |
| Pharos | 多路线协调、证明搜索和共享事实图中的独立复核。 [项目](https://github.com/frenzymath/Pharos) | 任务合同将首检、续查、停机和交回产物绑定到证明义务。 |
| Danus | 围绕已验证事实与依赖开展并行证明搜索。 [论文](https://arxiv.org/abs/2607.06447) | 登记证据等级、检查器信任依据及前提撤销后的影响。 |
| Archon Horizon | 跨项目编排长周期 Lean 4 形式化。 [项目](https://github.com/archon-agi/horizon) | 在证据流程中记录程序核验、外部形式化重放和人工证明审查。 |
| OpenEvolve | 迭代生成、评估和选择候选程序。 [项目](https://github.com/algorithmicsuperintelligence/openevolve) | 按冻结评估合同保留可复算的有限反例与候选修订。 |
| OSVerify | 数学错误发现、验证和反馈审计。 [项目](https://github.com/OSVerify/OSVerify) | 把反馈和覆盖范围接回具体主张及验收历史。 |

</details>

## 仓库布局与本地数据

| 路径 | 内容 |
| --- | --- |
| `ai4math/` | 核心引擎、命令行、策略和验证 |
| `docs/` | 架构、操作手册和研究流程 |
| `.agents/skills/` 与 `.claude/skills/` | 项目 Skill 正文和 Claude Code 入口 |
| `examples/` 与 `templates/` | 可复现示例和研究模板 |
| `tests/` 与 `scripts/` | 离线测试、Lean 重放和打包工具 |

研究状态、学术库和验收回执保存在源码包之外的本地工作区和数据目录。每个工作区保存追加式账本和按内容寻址的工件。工作区内的 `reports/research-summary.md` 展示当前进度，`reports/research-history.md` 保存历轮汇报。 [轮次汇总与交接](docs/operations.md#研究总汇总与轮次交接)

## 贡献与许可

欢迎提交可复现的缺陷报告、独立测试和文档改进。 [贡献指南](CONTRIBUTING.md) · [变更日志](CHANGELOG.md) · [Apache License 2.0](LICENSE) · [版权声明](NOTICE)
