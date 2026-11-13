# AI4Math 评测执行与审计

本页介绍如何冻结数学研究对照、登记运行结果、组织盲审和导出 Inspect AI 日志。研究运行按各自冻结的协议、模型和权限执行。

## 模型预算与回执

新模型计划使用 `schema_version: 2`，延用 v1 的题目摘要、调用和 token 上限、方法快照及预算分摊规则。可选择 `["B","C"]` 或 `["A","B","C"]`。用 `models` 代替 `model`，按角色名登记 `provider`、`model`、`revision`、`reasoning_effort` 和 `settings_sha256`。资源账本若使用 B/C，则选 `tool-resource-plan-v2`，并绑定同一模型计划。

把每项启用的 readiness 所依据的文件 SHA-256 放进 `readiness_evidence`，并用 JSON 对象将这些名称映射到本地证据文件路径。初始化复制证据字节，预留前复查摘要。证据的真实性及验证范围由出具者和评测保管人核对。

```powershell
python -m ai4math.evaluation init --root model-ledger --input model-plan.json --readiness-files readiness-paths.json
python -m ai4math.evaluation reserve --root model-ledger --input reservation.json
python -m ai4math.evaluation settle --root model-ledger --input settlement.json
python -m ai4math.evaluation audit --root model-ledger
```

预留包含 `role`、匹配的模型锁、完整请求摘要、精确输入用量和强制输出上限。结算包含 `id`、`usage`、`status` 和 `receipt`。回执形状如下。提供商响应需有原生 ID、实际模型版本、终态，以及存在时的原生用量。若提供商用 `model` 返回固定版本，可以省去 `model_revision`。推理档可从 `reasoning_effort` 或 `reasoning.effort` 读取。

```json
{
  "request_sha256": "<frozen wire SHA-256>",
  "model": {
    "provider": "<provider>",
    "model": "<requested model>",
    "revision": "<observed immutable revision>",
    "reasoning_effort": "high",
    "settings_sha256": "<frozen configuration SHA-256>"
  },
  "raw_response": {
    "id": "<native response ID>",
    "model": "<observed immutable revision>",
    "status": "completed",
    "reasoning_effort": "high",
    "usage": {"inputTokens": 15, "outputTokens": 5, "totalTokens": 20}
  }
}
```

原生响应按规范 JSON 保存并以 SHA-256 绑定。重开账本仍拒绝重复使用相同提供商和响应 ID。若请求没有可验收的用量和终态证据，可保留预留，或以失败、中断、超时和未知用量记账。用量未知时保留上限。Responses 文本请求可复用 `prepare_text_wire`、`observe_input_count` 和 `settle_text_response`，使用同一请求内容核对精确输入计数、生成上限和实际响应。[Responses 回执实现](../ai4math/responses_evidence.py) · [OpenAI Docs 输入计数](https://developers.openai.com/api/docs/guides/token-counting)

托管 Codex 任务可在 `codex_workflow` 中增加 `model_settings_artifact`。该冻结输入使用上述模型锁的五个字段，推理档必须明确。运行器向 App Server 显式传入模型、提供商和推理档，并在开轮前检查回报配置。App Server 捕获回执标识 `usage_scope=app_server_thread`。严格的逐请求对照需要另行接入可以出具提供商原生回执的传输层。[OpenAI Docs App Server](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server) · [托管合同](codex-monitored-contracts.md)

## 本机评测与执行边界

公开校准和可信代码可沿用 `trusted_local`。托管 Codex 按冻结工具链执行，监督器记录进程停止和输出。Windows 工程探针可单独检查进程树停机和资源上限。需要读写和网络隔离的密封题与历史截点评测，仍需两组各自通过整条求解链的隔离验收。[托管合同](codex-monitored-contracts.md) · [Windows 工程探针](../ai4math/windows_isolation.py)

## 截点导出和继续执行

保管人明确选择原工作区的事件编号。导出重放到该编号，校验事件链，复制当时登记的内容寻址对象。截点须处于静止状态，所有在途任务和待确认停止须先收尾。导出包含该前缀的历史元数据和证据对象，按保管边界存放。普通 Agent 的交接包应使用从该截点明确挑选的同一批材料。

```powershell
python -m ai4math --workspace research-ws checkpoint export --at 123 --out cutoff --model-ledger model-ledger
python -m ai4math --workspace restored-ws checkpoint restore --from cutoff --sha256 <manifest-SHA-256> --model-ledger model-ledger
python -m ai4math --workspace restored-ws audit
```

如果导出时绑定了模型或工具账本，恢复须连接原账本目录。账本锚点取导出时读到的账本尾，跨分支继续以同一追加账本计量。工作区路线与探索池记录按所选事件前缀恢复，保管人应在正式对照中为每个组只派发一个获准的分支。恢复保留原事件、任务 ID、状态和已用路线次数，再追加一条父截点登记。旧任务仅用于审查。续跑时以新任务 ID 执行 `task.add`，可用 `resume_from` 绑定继承的旧任务。目标、路线、角色、方法、程序、输入及候选绑定须一致。若需新文献目录，通过 `--allowed-root` 明确选择。

## 统一清单和盲审

试验计划 `ai4math-study-v1` 冻结数据集版本、活跃组、各组运行配置摘要、题目、计划观察点、来源和评分版本，关联模型和可选资源计划摘要。模型计划的 `tasks` 值是各题对象的规范 JSON 摘要。各题对象固定 `statement`、`domain`、`sources`、`input_manifest_sha256`、`checker_sha256` 和 `checkpoint_sha256`，后两个按需要为 SHA-256 或 null。

来源以 `url`、`version`、`original_sha256` 和 `passages` 登记。每个入选段落记录 `locator`、`page`、`text_sha256` 和 `assumptions` 字典。可以复用学术库的 [选定原文段落](exploration.md)，把人工核对的文本与原始 PDF 同时冻结。`--artifacts` 指向一份 SHA-256 到路径的映射，包含数据集清单、各组配置、题目输入清单、已登记的检查器和截点清单，以及选定的原文和段落。初始化逐个校验和保留字节。

```powershell
python -m ai4math --actor coordinator study --root trial init --file study-plan.json --artifacts artifact-paths.json --model-ledger model-ledger
python -m ai4math --actor coordinator study --root trial record --file outcome.json --model-ledger model-ledger
python -m ai4math --actor custodian study --root trial blind-export --out blind.zip
python -m ai4math --actor reviewer study --root trial review --file review.json
python -m ai4math study --root trial audit
python -m ai4math study --root trial inspect-export --at 7200 --out inspect-7200.json
```

`record` 每次对应一个预登记的 `case`、`arm`、`checkpoint_seconds`。记录 `status`、`elapsed_seconds`、`stop_confirmed`、草稿路径或 null、`reason` 和 `citations`。终态可记录完成、失败、中断、超时、输出限额或未运行，期间也可登记 `checkpoint`。当 `stop_confirmed` 为 true 时，`stop_receipt` 提供监督器或保管人的 JSON 停机回执路径，私有注册表封存其字节。可使用含 `stop_confirmed: true` 的工具回执，或带 `confirmed_at`、`evidence` 与 `trust_basis` 的协调方停机登记。保管人核对回执对应的进程、任务与停机范围。每条引用给出 `id`、`source`、`passage`、`claim`、`use_point`，并用 `assumptions` 把原文假设 ID 映射到论证里的满足理由。无法绑定的引用和漏填的条件保留为审查问题。用量从关联账本取得，未知保留为 null。

盲审 ZIP 包含每个预登记结果，包括失败和未运行。包内包含匿名 ID、稿件摘要、锁定陈述、选定段落及原文。组别映射、用量、耗时与内部诊断留在私有注册表。发布前保管人应检查稿件正文是否自带分组线索。复核记录绑定 `review_id` 和 `packet_sha256`，填写 `reviewer_kind`、`correctness`、`scope`、`attribution`、`progress`、`review_seconds`、`notes`、`citation_checks`。引用复核分别判断 `passage`、`assumptions` 和 `use_point`，并给出备注。通过的盲审记录可用于对照，数学主张仍走独立验证和接纳流程。

`inspect-export` 需安装提供日志 API 的 Inspect AI。每次只导出一个观察点，保留该点所有组和失败样本。它从封存回执生成可读的 Inspect JSON，标明 `executed_by_inspect=false`，不会重新派发模型。汇总多个观察点时应以最后一个累计观察点为用量终值。

## 选择首检后的研究策略

全目标研究对照可在新 AI4Math 任务的 `codex_workflow` 中将 `research_stop_policy` 设为 `target_progress_v1`。首检后记录对原题的覆盖范围。有限结果需要对剩余关键义务做一次有产物的推进，再按实际结果交回。固定本地语料的试验可同时将 `literature_acquisition` 设为 `local_only`。在试验清单中冻结各组资料与权限。

任务也可选 `target_progress_v2`。首检后的主评价项是相对于锁定目标完成的推导、对证明路线有决定意义的反例，以及承重结论的可核查性。复算记录它支撑的具体论证，用量和耗时仍从账本报告。交回时绑定承重引用的原文段落、使用点及假设对应。任何要供模型复用的原文先纳入试验资料清单，再按试验协议对齐各组资料权限。 [托管任务合同](codex-monitored-contracts.md)
