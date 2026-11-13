# 学术知识库与探索工作流（0.5）

本功能将方法论接到实际任务契约。框架执行检索、资料冻结、候选登记、预测区分、有限评估与路线展示；外部 agent 负责提出数学内容。没有增加内置模型 API、训练器或自动代理启动器，数学检查器仍独立于生产程序。

## 现成入口

可在 `data/academic/` 创建与数学工作区分开的本机资料库。随包种子包含资料入口和机制摘要卡，涵盖五项探索方法、失败综合、策略批评、邻近问题、复用审查、目标裁剪以及数学论文的方法案例。条目链接到公开来源，使用具体定理时仍需核对原文和适用条件。

```powershell
# 首次使用时创建资料库，已有库可跳过此命令。
python -m ai4math knowledge --root data/academic init --max-remote-requests 100

# 查询使用词项匹配。
python -m ai4math knowledge --root data/academic search --query "Hall 匹配 边界" --capability representation
python -m ai4math knowledge --root data/academic search --query "桥梁 引理" --capability bridge_lemma
python -m ai4math knowledge --root data/academic search --query "program evolution 构造" --capability construction
python -m ai4math knowledge --root data/academic audit

# 另一台机器/独立信息边界使用新目录；已有目录不能 init 覆盖。
python -m ai4math knowledge --root data/my-academic init --max-remote-requests 100

# 显式在线检索，每次恰好一次请求，不自动重试，不调用模型。
python -m ai4math knowledge --root data/academic discover --provider crossref --query "Small gaps between primes James Maynard" --limit 3
python -m ai4math knowledge --root data/academic discover --provider arxiv --query 'all:"auxiliary lemmas"' --limit 3

# 用返回的 ID 阅读记录；网络结果只是 provider_metadata。
python -m ai4math knowledge --root data/academic show --kind source --id <SOURCE_ID>
python -m ai4math knowledge --root data/academic add-source --file templates/academic_source.json
python -m ai4math knowledge --root data/academic add-card --file templates/mechanism_card.json

# 保存明确选择的本地原文：UTF-8 文本/HTML 建片段索引，PDF 只保留字节。
python -m ai4math knowledge --root data/academic add-document --source <SOURCE_ID> --file <LOCAL_FILE> --format text
python -m ai4math knowledge --root data/academic search --kind passage --query "tight constraint"
```

在线适配使用 Crossref REST JSON 和 arXiv Atom API。请求条数 1–20、响应不超过 2 MiB、超时参数 1–30 秒，禁止跨主机重定向；arXiv 请求间隔至少三秒。库的远程请求总额在创建时固定；失败消耗一次额度，未完成请求在重开后继续阻塞，须显式 `knowledge recover --id ... --reason ...`，恢复不返还额度。网络超时为客户端 I/O 限制及读取间隔的经过时间检查，不是 OS 级强制截止；它不代替模型/工具资源账本。没有批量全网抓取、订阅、付费服务或自动全文下载。

`add-document` 按 SHA-256 流式归档完整原件。小型文本和 HTML 可自动生成带定位的片段，HTML 索引排除 script/style。PDF、超过 8 MiB 的文本或 HTML，以及提取文字超过 200,000 字符的原件仍完整归档，返回 `text_extracted=false`。用 `add-passage` 为相关原文补登记页码、定理号或章节。导入不执行文档内容，数学引用仍需核对原文和适用条件。

来源和机制记录以规范 JSON 的 SHA-256 为身份，修改内容生成新 ID，老字节、事件及来源不覆盖。库使用独立的 `research.sqlite3`、objects 和追加事件，与课题库不共用数据库。哈希链检查一致性，不能抵抗具有宿主写权限者重写所有文件与锚点。

## 实际探索的最小流程

1. 读锁定目标、参数域和开放义务；先搜索资料，再 `show` 阅读完整机制卡与来源。检索到相关词不等于定理适用。
2. `knowledge.attach` 明确导入所选 `card_ids`；原文另以 `document_ids` 明确选取。不自动注入整个库。启用 theory_first 时库根必须在 `allowed_roots` 中；封存题、历史课题不能借知识库绕过信息边界。
3. `exploration.open` 为目标建固定容量 pool，声明 `max_candidates`、`max_tasks`、`max_probes`。需要排名时，在建池时冻结 `evaluation_protocol`，包括 `validator_artifact`、`benchmark_artifacts`、各指标的 `definition`、`unit` 和 `metric_directions`（max/min）。计划变化后，原池不能派新任务。
4. `exploration.propose` 提交候选：能力类型、机制键、假设、表示、桥梁、反驳条件、计划、最小探针和冻结设计材料。修改/组合父候选时列 `parents` 与 `parent_reason`；候选不覆盖原版本。
5. `task.add` 通过 `exploration_candidate` 绑定候选。已有 research policy 的方法设计和审查要求照常生效。目标开池后，创建、导出和启动都会拦截未绑定候选的待执行 explore/produce 任务。原任务记录继续保留，继续研究需登记新任务。
6. `export-task` 写出 `input.json` 和 `PROMPT.md`，带锁定目标、预算、操作建议、来源、机制、候选及交付约定。可交给 Codex/其他 agent 或人工。新建 explore/produce 任务即使未使用 pool，也带基础探索指导；既有任务契约不追改。
7. agent 返回具体论证/构造及材料；有可执行程序时由原运行器 `run` 执行。框架不从流畅文本中自动判断“思路新颖”。
8. 独立 validate 任务按冻结协议核验，输入恰为候选设计、基准集与生产结果的并集。`exploration.evaluate` 还核对验证任务 ID、协议摘要、候选摘要和完整指标集合。有效评估在同协议内进入 Pareto 前沿。没有协议的池可用于不评分探索，旧评估显示在 `unranked_evaluations` 中。
9. `exploration.probe` 记录至少两个有不同预测的候选；按预测分区熵/预计工具调用数排序。它只是假设下的调度指标，不是真实信息增益或正确性概率。
10. `exploration.feedback` 记录终态任务的障碍与下一改变；`explore --pool ...` 查看组合、评估、探针和反馈。接纳数学主张仍须走原 `claim/validation/review/accept` 流程。

所有 pool 绑定任务在创建时占用一个任务槽，包括验证任务。准备好但未运行、失败或中断都不返还槽；原路线额度仍在启动时消耗。两个限制同时生效，模型与资源账本另行准入。探索反馈、排名和信息收益从不重置额度或路线停机计数，任务导出也不会自动启动新阶段。

结构去重使用规范化的能力、机制键和假设；组合展示按机制家族保留代表。它不是数学语义等价判定：agent 仍必须识别“同方法换说法”。评分前沿只表示声明有限评价下的不支配关系，不能自动用于全参数证明或对外声称研究效率领先。

## 从文献机制到义务关闭

1. `exploration.transfer` 登记 `id`、`pool`、`card`、`document`、`obligation`、`excerpt`、`locator`、`conditions`、`mapping`、`gap`。系统检查冻结文档归属及正文是否包含摘录，定位描述由提交者填写，数学适用性仍是 `unknown`。义务必须是目标计划中的开放直接依赖。
2. `exploration.propose` 用 `transfer` 和 `hypothesis` 锁定迁移来源与候选假设。验证器发现反例并在声明的有限顺序内核验最小性时，在评估回执中提交 `counterexample`，包括 `hypothesis`、`case_id`、`witness`、`search_domain`、`ordering`、`smaller_cases_checked` 和 `minimality_checked=true`。`exploration.counterexample` 接收 `id` 和失败的 `evaluation`，从冻结回执读取见证。最小性受验证器与声明域约束。
3. 修订候选引用同一 `transfer`、含失败候选的 `parents`、`counterexample` 和 `revision_change`，并给出改变后的假设。通过同一协议重新评估，独立回执的 `rechecked_cases` 记录原失败用例已经重新核验。
4. 对准确的义务走 `claim.add → validation.add → review.add → claim.accept`。`exploration.resolve` 用 `id`、修订的 `evaluation` 和已接纳的 `claim` 串联全链，并校验义务、失败用例复核、修订产物及基准绑定。`explore` 和报告显示当前主张状态，后续撤销仍可追溯。

公开开发题 [CSES School Dance](https://cses.fi/problemset/task/1696/) 使用教材增广路机制，覆盖 36 张非空标号小图与 1 个公开样例。可用以下命令重放，需提供本地已保存的[教材原文 HTML](https://web.cs.dal.ca/~nzeh/Teaching/4113/book/matching/bipartite_maximum_matching/augmenting_paths.html)。

```powershell
python -m examples.run_school_dance_chain workspaces/school-dance-demo path/to/augmenting_paths.original.html
```

回放通过独立的边子集穷举检查器关闭有限义务，公开题的全输入实现仍为 `unknown`。运行目录保存原文库、工作区事件、独立回执、验收 JSON 和报告。

## 五项能力的任务契约

| capability | agent 必须交付 | 框架实际提供 |
| --- | --- | --- |
| representation | 新对象定义、映射、额外假设、转换探针 | 机制检索、源材料绑定、候选版本与 recipe |
| bridge_lemma | F / D / G、引理 L、两段逻辑义务、反例 | 引理设计契约、原目标约束与独立任务入口 |
| construction | 模板、程序、参数域、父候选、修复理由 | 候选谱系、有界执行、独立评分与 Pareto 前沿 |
| discriminating_probe | 候选预测表、最小实例、两种结果的动作 | 预测差异检查、成本归一的启发式排序 |
| branch_portfolio | 承重机制差异、首个探针、跨路线发现 | 家族代表、未评价候选、失败综合与固定预算 |

这些 recipe 是从公开方法独立编写的操作建议，不复制运行外部项目的技能。Pharos/Rethlas 提供多计划与失败综合的启发，OpenEvolve/FunSearch/AlphaEvolve 对应构造群体，AlphaGeometry 对应辅助对象，DSP/LEGO 对应引理与草图；LEGO 复用质疑提醒实际测量复用和成本。研究者案例用于提取机制，不代表它们必然可迁移。

## 工程演示与证据

```powershell
python -m examples.run_exploration_demo workspaces/my-new-exploration-demo data/academic
python -m ai4math --workspace workspaces/my-new-exploration-demo explore --pool portfolio
python -m ai4math --workspace workspaces/my-new-exploration-demo report --output exports/exploration.md
```

演示使用独立新目录，导出五类任务契约；实际运行两个合成候选及各自独立检查器，共四个子进程；保存父子关系、两条有限评估、区分探针与反馈。它检查框架接线，不运行真实模型，不发现新数学，不接纳任何数学命题；合成主目标维持 unknown。

演示产生的命令、版本与回执可随本机工作区保存。评估 Agent 的研究能力时，应使用同题、同模型、同信息边界和同总预算的对照。 [评测操作](benchmark-operations.md)

参考接口文档：[Crossref REST](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)、[arXiv API](https://info.arxiv.org/help/api/user-manual.html)。数学方法来源的具体版本保存在每条 source 的 locator 字段。
