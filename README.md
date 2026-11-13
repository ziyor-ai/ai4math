# AI4Math

AI4Math is a Python workbench for mathematical research. It tracks proof obligations, research routes, task execution, and independently reviewed evidence across sessions.

AI4Math 是面向长周期数学研究的本地工作台，适合需要跨会话探索、核验和交接部分结果的课题。研究者可以从一个精确命题出发，逐条跟踪证明义务、试过的路线和下一步要解决的问题。

## 反例出现后，证明还能怎么走？

First Proof 第 5 题问的是，在超域中，若三个元素的和可以取到零，由它们构成的六项三次和是否也能取到零。一条证明路线是为三个二次和找到同一个元素，再分别乘以原来的三个元素，把原来的零和关系带到三次和里。只要存在这个共同见证，路线就能走通。 [公开题面](https://github.com/1stproof/batch-3/blob/5908aab1a1067d0e858294950537d0c471d7bb7f/batch-3-problems/problem-05/problem.tex)

演示中的独立检查器核验了一个五元超域。在其中一组满足原题前提的三元组上，三个二次和分别给出集合 $\lbrace a,d\rbrace,\quad \lbrace b,d\rbrace,\quad \lbrace a,b\rbrace$。它们两两相交，三者交集为空。这个三元组的六项三次和仍能取到零。

1. **追踪反例的影响。** 报告标出被击穿的桥梁「共同见证总是存在」，并列出依赖它的三次和证明路线和文稿条目。独立检查器也核验了仍可使用的结论，即这张五元超域表上满足前提的三元组都给出两两相交的二次和。
2. **用旧见证重放修订。** 给桥梁加上「至少一个二次和只有一个元素」的条件。独立检查器重放原例，定位到三个二次和各有两个元素，因此原例被新增条件排除。
3. **比较不可区分的见证。** 在同一个五元超域上，另一组满足零和前提的三元组也给出两两相交的二次和，却有共同见证。这两个例子说明，超域大小、单位群、零和前提和两两相交这几项信息合起来，仍不足以判断共同见证是否存在。
4. **把定理条件变成下一步义务。** 演示将所用的集合论引理绑定到原文段落。两两相交已有依据，至少一个集合只有一个元素则是这条路线待核的条件。一旦该条件成立，其唯一元素就同时属于另两个集合。

**下一步研究问题**是从三个二次和中分别选择一个元素，检查乘上各自对应的原元素后，三项加权和能否取到零。报告把这一步连回原题，供下一轮接续。

在源码目录按[安装步骤](#安装)完成安装，再用一个全新的工作目录运行离线演示。三个诊断命令分别查看反例影响、见证对照和定理条件。 [四项工作流与输入格式](docs/research-diagnosis.md)

```console
python -m examples.run_hyperfield_diagnosis workspaces/hyperfield-diagnosis
python -m ai4math --workspace workspaces/hyperfield-diagnosis/workspace diagnose --node common
python -m ai4math --workspace workspaces/hyperfield-diagnosis/workspace diagnose --node cubic
python -m ai4math --workspace workspaces/hyperfield-diagnosis/workspace diagnose --node singleton-route
```

## 项目特点

- **用证明义务组织研究。** 锁定命题、参数域和量词，分别记录定义依赖与证明依赖。路线保留证伪条件、运行额度和未解义务，下一轮可以沿着具体卡点接续。
- **让独立检查器核验可追溯的结果。** 按 SHA-256 冻结输入、检查器和产物。数学主张、验证依据和审稿意见分别登记，前提撤销会沿依赖传递到受影响的结论。
- **在推导时取得所需原文。** 独立文献库和异步请求队列提供选定段落，研究任务可在补料期间继续处理已有依据的部分。交回时可以将承重定理绑定到原文定位、使用点和假设对应。
- **在本机复查和续接。** 工作区保存检查点、追加事件、封存清单和停机回执。评测可导出匿名稿件及 Inspect AI 日志，先审查数学推进，再核对分组和用量。

## 与相关框架的侧重点

| 项目 | 公开实现的侧重点 | AI4Math 的对应设计 |
| --- | --- | --- |
| Pharos | 主 Agent 管理路线，研究 Agent 推进证明，独立验证者审查并将结果写入共享事实图。[项目文档](https://github.com/frenzymath/Pharos) | 用任务合同将首检、续查、停机和交回产物绑定到一项锁定的证明义务。 |
| Danus | 以共享事实图协调并行证明搜索，保存已核验的事实、证明与逻辑依赖。[作者论文](https://arxiv.org/abs/2607.06447) | 在逻辑依赖之外，登记数学证据等级、检查器的信任依据和撤销后的影响范围。 |
| Archon Horizon | 编排跨项目的长周期 Lean 4 形式化任务。[项目文档](https://github.com/frenzymath/Archon-Horizon) | 在同一证据流程中记录独立程序检查、外部形式化核验和人工证明审查的结果。 |
| OpenEvolve | 从种子程序出发迭代生成、评估和选择候选代码。[项目文档](https://github.com/algorithmicsuperintelligence/openevolve) | 在同一冻结评估协议下比较研究候选，记录可复算的有限反例及后续修订。 |
| OSVerify | 分离数学错误发现、验证与反馈审计，为专家复核整理问题。[项目文档](https://github.com/OSVerify/OSVerify) | 将反馈和覆盖范围回接到工作区中的具体主张及其接纳流程。 |

## 已做的优化

- **首检后优先补推导。** 可选的 `target_progress_v2` 在首检只得到有限结果时，要求先记录实际推出的结论或遇到的障碍。再做计算时写明不同结果会如何改变下一步数学判断。
- **减少证据整理中的重复操作。** 托管计算可以直接归档原始输出，交回时用已知哈希确认未变化的文件。缺失原文的承重依赖保留为条件性结论，便于审查时定位还需核对的步骤。

## 安装

需要 Python 3.10 或以上。从源码目录安装。核心运行时仅依赖 Python 标准库和随 Python 提供的 SQLite。

```console
python -m pip install -e .
python -m ai4math --version
python -m ai4math --help
```

## 在 Codex 中安装和使用

### 1. 打开源码目录并安装 Skills

在 Codex 桌面应用中打开克隆或解压后的 AI4Math 源码目录，也可以从该目录启动 Codex CLI。在仓库根目录完成上面的 Python 安装，再执行以下命令。

```console
python -m ai4math codex-skills install
python -m ai4math codex-skills check
```

安装器将三个 Skill 复制到 `~/.agents/skills/`。`check` 成功时会输出文件哈希清单。升级已有安装时使用 `python -m ai4math codex-skills install --upgrade`，然后重新检查。如果 Codex 里还看不到 Skill，重启 Codex 后再选择。 [OpenAI 官方 Skill 文档](https://learn.chatgpt.com/docs/build-skills)

### 2. 创建本机研究工作区和文献库

```console
python -m ai4math --workspace workspaces/my-research init --project-id my-research --title "我的数学研究"
python -m ai4math knowledge --root data/academic init
```

已有工作区或文献库时，直接沿用其路径并跳过对应的初始化命令。

### 3. 在新会话中选择协调 Skill

在 Codex 会话中选择 `ai4math-coordinate`，或在支持 `$` 提及的界面输入 `$ai4math-coordinate`。把下面的提示词发给它，将占位内容替换成实际命题。

```text
使用 $ai4math-coordinate。工作区为 workspaces/my-research，学术库为 data/academic。
我的研究命题是 <命题、参数域和已知假设>。
请先登记命题和主目标，列出关键证明义务，查阅学术库，并提出第一轮研究路线和首检。
把首轮任务合同准备好供我审阅。
```

运行已准备好的托管任务时，协调会话通过本机 Codex App Server 启动使用 `ai4math-research` 的子会话。 [托管研究与派工](docs/codex-research.md)

### 4. 按需启动独立文献会话

在另一个 Codex 会话中使用同一个学术库。可以直接发送下面的提示词。

```text
使用 $ai4math-literature。学术库为 data/academic，actor 为 literature-curator。
请接续已认领的文献请求，并处理队列里的新请求。先查库内资料，按需要和授权额度补查。
找到原文后归档版本、定理或页码定位及适用条件，按请求 ID 发布结果。
找不到时记录已查范围和受影响的推导，供研究会话继续推进。
```

[独立文献会话](docs/literature-workflow.md)

## 运行离线示例

在仓库根目录执行。示例目录必须尚未存在，重跑时换一个目录名。

```console
python -m examples.run_finite_demo workspaces/finite-demo
python -m ai4math --workspace workspaces/finite-demo status
python -m ai4math --workspace workspaces/finite-demo audit
python -m ai4math --workspace workspaces/finite-demo delivery-check
```

示例用独立的生产与验证程序核对两顶点图 $P_2^4$ 的最小互素标号数。示例审稿人为合成角色，数学结论由可检查的有限证书支持。整个流程可离线运行。

## 研究工作流

1. **锁定目标。** 明确命题、参数域和量词，将证明义务登记为有向无环依赖图。
2. **组织探索。** 为当前义务登记候选路线、关键假设、证伪条件和首个探针，冻结所选资料与预算。
3. **执行并核验。** 交给本机程序、外部 Agent 或研究者执行。独立验证器检查结果，工作区保留输入、回执与 SHA-256。
4. **接纳与续接。** 按适用范围登记证据和未解义务，使用检查点、工作区审计和文稿台账接续。

从工作区的 `reports/research-summary.md` 查看当前进展、适用范围、未解缺口和下一步。每轮验收或暂停交接可追加一条汇总，链接推导、验收回执及选用的论文段落，并保留历史轮次。 [轮次汇总操作](docs/operations.md#研究总汇总与轮次交接)

数学结论使用四种证据等级 `proved`、`disproved`、`finite_support` 和 `unknown`。有限实验按实际范围登记。任务完成和封存成功只确认工作流状态，数学正确性依赖独立验证。

[架构与信任边界](docs/architecture.md) · [命题、路线与任务操作](docs/operations.md) · [知识库和探索](docs/exploration.md)

## Inspect AI 评测导出

在需要导出评测日志的 Python 环境中安装 `inspect-ai`。`study inspect-export` 可将已登记的评测结果导入 Inspect 日志，用于复查用量、停机和盲审。 [评测与导出](docs/benchmark-operations.md)

## 发布验证

- **默认无界。** 新建本地或外部任务时，`timeout_seconds` 和 `output_limit_bytes` 默认是 `null`。需要固定预算时可显式设置。运行输出完整留存，工具预览继续分页。 [任务合同](docs/operations.md)
- **大文件归档。** 干净安装后，用 `knowledge add-document` 完整归档了一份 9,437,797 字节的 PDF 测试件，归档前后字节数和 SHA-256 一致，并登记了带页码的摘录。 [文献归档](docs/exploration.md)
- **五元超域实跑。** 在干净解压环境中，协调和文献会话通过本地测试接口调用真实 Codex，研究会话通过随包的 `codex-run` 托管。研究任务约 347 秒完成封存并确认停机，四项独立验证均通过。共同二次见证的普遍存在被证伪，旧见证被新增的单元素条件排除，两组见证对照和原文条件核查均已登记。六项三次和的原命题保持开放。验收和暂停各追加一条历史，共核对 29 个材料链接。 [离线复现步骤](docs/research-diagnosis.md#离线演示)

## 仓库布局

| 目录 | 用途 |
| --- | --- |
| `ai4math/` | 核心引擎、命令行和学术资料种子 |
| `docs/` | 架构与操作手册 |
| `examples/` | 示例、生产程序与独立验证器 |
| `templates/` | 任务、证据和研究配置模板 |
| `skills/` | Codex Skills |
| `tests/` | 离线回归测试 |

本地研究状态可放在 `workspaces/`，本机学术资料、验收回执和运行记录可放在 `data/`。

## 贡献与许可

欢迎提交可复现的缺陷报告、独立测试和文档改进。参阅[贡献指南](CONTRIBUTING.md)和[版本记录](CHANGELOG.md)。项目采用 [Apache License 2.0](LICENSE)。
