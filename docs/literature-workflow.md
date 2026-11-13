# 文献检索、学习与复用

## 两个会话如何协作

**研究会话**按证明义务查本地库，核对原文和假设，决定首检。遇到库内缺口就提交请求并记下 ID，同时推进手头可独立完成的证明、反例和实验。主会话按复盘决定逐路线登记 `literature.search`，汇总冻结 `literature.plan`。子会话读冻结输入、独立查本地库、提交适用性回执，经 `task.release_exploration` 放行后开始首检。两次关口评估手头证据，均可在文献请求尚未处理时完成。

**独立文献会话**用同一个学术库接单。它负责补查学术来源、获取与阅读原文、登记结构化速览，再发布命中的 profile ID 或有限检索的结果。它可以在研究会话继续探索、暂停或复盘时工作。研究主会话在检查点或下一轮读已发布结果，必要时通知正在运行的子会话。只有依赖新文献的具体义务保持开放。已归档材料仍按实际假设核对后才能迁移到证明。

新外部研究合同在 `literature.acquisition_mode` 中标记 `async`。托管 worker 可在文献或探索阶段提交请求和读取结果，可在自然检查点重新扫描本地库。归档段落的使用记入工具记录。历史合同沿用各自的获取规则和已冻结回执。

## 启动和收发请求

在两个 Codex 会话各自开工，使用同一学术库路径。文献会话的 actor 与研究主会话及 worker 分开。

```text
/goal 使用 $ai4math-coordinate 在 <工作区> 推进 <锁定目标>。
在 <学术库> 查现有资料。新文献缺口发到共享队列，推进可以独立完成的部分。
每轮检查已发布的结果，核对原文后决定是否回接。
```

```text
/goal 使用 $ai4math-literature 持续处理 <学术库> 的文献请求，actor 为 literature-curator。
先复用库中材料，必要时补查并归档原文、定位和速览。以同一请求 ID 发布结果。
在我停止或授权的检索预算耗尽时结束。
```

研究侧提交 `gap.json`，包含 `question`、`assumptions`、已查的 `queries` 和说明结果会改变哪项决策的 `purpose`。同一问题和同组假设共用请求 ID，各路线的订阅、检索提示和用途仍分别保存。

```powershell
python -m ai4math --workspace <WORKSPACE> --actor coordinator knowledge --root <LIBRARY> request --route <ROUTE> --file gap.json
python -m ai4math --workspace <WORKSPACE> knowledge --root <LIBRARY> inbox --status published --after 0
```

研究主会话记下已读结果的最后一个 `delivery_seq`，下次用 `--after` 增量读取。后来订阅同一已发布结果的路线也会收到一条新投递。托管子会话可通过 `request_literature` 提交同样的字段，工具自动绑定工作区、路线、任务和合同。`literature_updates` 立即返回该任务的请求状态，传 `request_id` 可查看该条结果绑定的速览和段落 ID。`search` 和 `reuse_passage` 用于核对原文。学术库的写入会使子会话的旧检索缓存失效。选中的材料可在下轮作为冻结输入。

```powershell
python -m ai4math knowledge --root <LIBRARY> inbox --status queued --wait 15
python -m ai4math --actor literature-curator knowledge --root <LIBRARY> request-claim
python -m ai4math --actor literature-curator knowledge --root <LIBRARY> request-finish --id <REQUEST_ID> --attempt <ATTEMPT_ID> --file result.json
```

`result.json` 包含 `outcome`、`summary`、实际 `queries`。`found` 还需 `profile_ids`，每个 ID 绑定库里已登记的原文和段落。`no_match` 说明已查的范围，`blocked` 记录访问或范围缺口。文献会话中断后可用原 actor 查询 `claimed` 状态并继续，或以 `interrupted` 结束该次尝试，让请求回到队列。尝试和发布记录均保留，结果标记为 `unknown`。

一次本地 `scan` 同时覆盖来源、原文段落、速览和方法卡。同一归档文档选出最相关段落，DOI 或 arXiv 别名用于归并来源，原文版本与内容哈希各自保留。文献会话可按替代表示、边界条件和引用链扩展查找。重复的成功 Crossref/arXiv 请求按服务、查询和上限复用，明确需要刷新时用 `--refresh` 并计入原配额。

## 给归档原文补速览

```powershell
python -m ai4math knowledge --root data/academic search --kind scan --query "metric compatibility 度量相容 curvature commutator"
python -m ai4math knowledge --root data/academic review-queue --limit 20
python -m ai4math knowledge --root data/academic add-document --source <SOURCE_ID> --file <ORIGINAL_FILE> --format html
python -m ai4math knowledge --root data/academic show --kind passage --id <PASSAGE_ID>
python -m ai4math knowledge --root data/academic add-profile --file <PROFILE_JSON>
python -m ai4math knowledge --root data/academic show --kind paper_profile --id <PROFILE_ID>
```

1. 文献会话阅读相关原文后，根据返回的 `document` 和 `passages` ID 登记速览。`needs_profile` 表示已有可读段落，`needs_passage` 表示应先补定位。对 PDF 可执行 `knowledge add-passage --document <DOCUMENT_ID> --locator "§2, p. 4, Lemma 1" --file <TEXT_FILE>`，登记人工或外部工具提取的短段落。使用该段落前与同一 PDF 核对。
2. `summary` 写研究问题及结果。`contributions` 写原文实际给出的创新或方法，`fields` 写涉及领域，`keywords` 可同时放中英文术语和异名。`methods`、`assumptions`、`limitations` 写方法、必要条件和可迁移边界，`anchors` 引用实际阅读的段落 ID。所有速览标记为 `selected_passages` 和 `unknown`。若原文给出的定理要用于当前证明，另做假设映射和独立验收。
3. 同一原文的补订填写 `supersedes` 为当前速览 ID。新版本原文先登记新 `document`，再建自己的速览。主会话按需通过 `knowledge.attach` 的 `document_ids`、`passage_ids`、`profile_ids` 附加已选记录。托管子会话的冻结输入使用可直接读取的文本段落和速览，PDF 原字节留作复核依据。档案补录可按 `review-queue` 逐份推进，优先完成当前义务引用的原文。

```json
{
  "document": "<DOCUMENT_ID>",
  "summary": "作者研究什么问题，主要建立了什么结果。",
  "contributions": ["可定位的主要贡献"],
  "fields": ["离散数学", "图论"],
  "keywords": ["二分匹配", "bipartite matching", "deficient subset"],
  "methods": ["交替路", "缺陷证书"],
  "assumptions": ["有限二分图"],
  "limitations": "转入当前命题前需要核对的额外条件。",
  "anchors": ["<PASSAGE_ID>"]
}
```

## 自动化科研与 RSI 相关做法

| 论文与已读位置 | 文献如何进入下一轮 | 可移植到 ai4math 的做法 |
| --- | --- | --- |
| [ResearchAgent §3.2](https://arxiv.org/html/2404.07738v2#S3.SS2) | 从核心论文沿引用关系扩展，并从多个研究领域检索共享概念。实体抽取覆盖受其标题、摘要语料和实体链接限制。 | 首检设计从最贴近的原文出发，沿引用和方法关键词扩展。阶段一使用可检查的领域、别名和方法字段。 |
| [AI-Supervisor §3.3–3.5](https://arxiv.org/html/2603.24402v1#S3.SS3) | 按平台并行搜索并合并去重，摘要初筛后再读原文。分段提取方法模块、评测结果和限制。遇到失败先找失效机制，再把它翻译成邻域术语。 | 将已知缺口写成对象和假设的关系，例如由“延拓证明”细化为“给定局部等距，如何证明同一拉回路径度量下的提升端点存在”。用改写后的机制查覆盖空间、度量几何和 ODE 的相关定理。 |
| [EvoScientist §3.2–3.5](https://arxiv.org/html/2603.08127v1#S3.SS2) | 跨任务检索两种经验。一种保存有前景与失败的想法，另一种保存有效执行策略，任务结束后分别提炼。 | 论文速览保存原文贡献和假设。路线复盘保留可迁移的失败条件与首检结果，之后由新任务对照同类假设。 |
| [The AI Scientist-v2 §3.1](https://arxiv.org/html/2504.08066v1#S3.SS1) | 在构想阶段查询 Semantic Scholar，用相关工作评估想法的新颖性和可行性。 | 在冻结探索计划前补查真正会改变路线选择的缺口，并把已知结论和自己的额外义务逐条列出。 |

当前实现使用本地双语词法排序。引用扩展由文献会话按请求显式选择，发现请求继续使用有预算的 Crossref/arXiv。后续可用固定的一组跨领域问题比较查询时间、同哈希下载次数、速览转入原文的比例和首检被文献纠正的次数，再判断是否需要自动引用扩展或向量检索。
