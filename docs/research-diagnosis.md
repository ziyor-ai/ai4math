# 反例、猜想修订和定理适用性

`diagnose --node NODE` 从当前工作区快照读取已登记的数学证据，输出 JSON。用 `python -m ai4math --workspace WORKSPACE --actor ACTOR apply OPERATION --file INPUT.json` 登记下列输入。报告与诊断记录均保留原命题、任务与证据等级，接纳数学结论仍走独立验证和审稿。

## 反例影响

对被反驳的节点运行 `diagnose --node NODE`。报告从已接纳、由 `counterexample` 方法验证的 claim 出发，列出反例产物及信任依据，并沿定义依赖、证明依赖和 claim 依赖列出需复查的节点、路线、主张和文稿条目。`dependency_edges` 显示影响路径，`other_accepted_proofs` 列出报告中没有命中依赖的已接纳证明。影响范围以已登记的边为准，需复查的主张由研究者决定是否撤销。报告中的原目标状态来自注册表。

已有的 `exploration.counterexample` 作为候选机制的失败证据显示在 `candidate_counterexamples`，作用域沿用其独立评估。要判定某个命题被反驳，需将见证绑定到该节点并接纳反驳 claim。

## 猜想修订与重放

1. 用 `node.add` 登记新版本，保持原节点的陈述、域和量词。若改变工作区主目标，另建工作区。工作区内可以并列保存辅助命题的各版本。
2. 用 `diagnosis.revision` 登记 `id`、`original`、`revised`、`reason`。报告逐项显示变更，并列出还没有复查记录的旧反例产物。
3. 为修订后的节点执行一个独立 `validate` 任务，唯一输入为旧反例的冻结见证。用 `diagnosis.replay` 登记 `id`、`revision`、`counterexample`（已接纳的反例 claim ID）、`witness`（SHA-256）、`task` 和 `trust_basis`。
4. 检查器在原 `result_contract` 之外写入 `diagnosis`，其中 `kind` 为 `revision_replay`，同时给出 `reason` 和以下一种 `outcome`。`still_refutes` 表示新版前提满足且结论失败，`excluded_by_condition` 需填写具体 `condition`，`conclusion_holds` 表示新版前提和结论在该实例均成立，`inconclusive` 表示本次未判定。

登记人和执行检查器的身份分别记录。旧反例撤销后，报告仍显示历史重放，并标明原反例是否仍有效。重放的证据范围是这一见证。若要接纳对新版的反驳，另走 claim、验证和审稿流程。

## 不可区分的见证

用独立 `validate` 任务核查两个冻结见证。任务节点是所要分析的证明义务。以 `diagnosis.indistinguishability` 登记 `id`、`node`、`question`、`witnesses`（两个 SHA-256）、`task`、`trust_basis`、`next_step`。

检查器返回的 `diagnosis.kind` 为 `indistinguishability`，`diagnosis.witnesses` 以两个哈希为键，每个值包含 `invariants` 字典、`conclusion` 和 `realization`。两份不变量按规范 JSON 比较，必须完全一致，结论必须不同。`realization` 取 `actual` 或 `formal`。若有形式见证，检查器还需填写 `diagnosis.realizability_gap`。使用明确的精确量或规范表示，检查器负责数学合法性和不变量计算。

以上两种检查器均复用 `validate` 的 `result_contract`，需要保留任务 ID、命题摘要、精确输入、`passed: true`、`scope: finite`、`checks` 和 `limitations`。需要请求身份的合同还要返回对应绑定字段。诊断结果只针对所检查的范围。

## 原文定理适用性

用 `knowledge.attach` 显式附加所选的机制卡、原文和段落。已有的旧段落可用同一资料再附加一次以登记段落身份。用 `diagnosis.source_check` 登记 `id`、`node`、`passage`、`theorem`、`use_point`、`limitations` 和 `conditions`。每条原文假设对应一个带 `status` 和 `reason` 的对象：

- `satisfied` 附 `claim`，指向已经接纳、独立证明的依据。
- `open` 附 `obligation`，指向单独登记的未解节点。
- `mismatch` 说明原文条件与当前使用点的差异。

框架核查原文版本、段落和字节，数学假设的对应由研究者复核。若需要校订原文或假设，用新 ID 和 `supersedes` 指向同一节点和使用点的旧记录。报告保留旧版，显示前提撤销与原文校订可能涉及的依赖和文稿。后续若证成一条开放条件，通过新记录更新映射。

## 离线演示

```console
python -m examples.run_hyperfield_diagnosis workspaces/hyperfield-diagnosis
python -m ai4math --workspace workspaces/hyperfield-diagnosis/workspace diagnose --node common
python -m ai4math --workspace workspaces/hyperfield-diagnosis/workspace diagnose --node cubic
python -m ai4math --workspace workspaces/hyperfield-diagnosis/workspace diagnose --node singleton-route
```

五元超域的乘法单位群为 Klein 四群，单位自负。独立检查器穷尽验证表的公理，检验给定三元组的二次见证集合和六项和。另一个三元组在同样的表、相同的群、零和前提和两两相交条件下拥有共同见证。修订版本加上单元素集合的条件，旧见证被该条件排除。定理适用性用一段随演示生成的初等集合论推导示范。审稿身份是演示角色，计算证据由独立检查器提供。原题在此演示工作区保留为开放目标。 [公开题面](https://github.com/1stproof/batch-3/blob/5908aab1a1067d0e858294950537d0c471d7bb7f/batch-3-problems/problem-05/problem.tex)
