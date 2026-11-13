# 公开数学研究方法的工程吸收

这次重写把前期调研中可落地的方法转为对象和约束：研究围绕精确目标与尚未解决的证明义务推进；实验、审稿和证明使用不同完成标准。以下是框架设计选择，工程检验属于 `finite_support`，对开放数学命题的成功效果为 `unknown`。

## 方法对应关系

| 参考 | 借鉴的方法 | 本框架的实现 | 适用边界 |
| --- | --- | --- | --- |
| OpenAI First Proof | 多种种子路线、完整论证审查、引用检查、有界修订 | route board、独立 review、coverage 和 stop condition | 没有复现模型训练或论文中的完整实验系统 |
| AlphaProof 与 DeepSeek Prover | 以未闭合子目标组织搜索 | statement/proof dependencies、目标可达锥和过期证明计划检查 | 未实现 AND–OR 树搜索、策略学习或自动 Lean 证明 |
| Aristotle | 非形式化引理与形式化反馈衔接 | 通用命题节点、独立检查器任务和显式 formal_kernel 信任记录 | 未捆绑 Aristotle 服务，也未默认信任包装器退出码 |
| AlphaEvolve 与 OpenEvolve | 将候选生成和固定评估器分开，保存可重放材料 | produce/validate 任务、冻结程序与输入、内容寻址对象 | 未实现进化种群、评分函数或模型调用；有限评分不证明全称结论 |
| Pharos | 路线记录、下一问题、阻碍与停机规则 | route rationale、next_question、stop_condition、checkpoint | 不按新增文件数或重复有限表格判定证明进展 |
| OSVerify | 错误提出、验证和最终反馈审计分开 | review issues 的 confirmed/rejected/unresolved、blocking、coverage | `ready_for_human` 不等于 proved；本版本是结构化审稿协议 |
| SHEAF | 锁定目标，区分语义依赖和证明依赖，只处理目标相关子图 | 不可改命题摘要、两类依赖、无环检查、goal cone | 不自动保证自然语言或 Lean 定义语义等价 |
| Danus | 事实依赖与撤销传播，区分信任来源 | claim dependencies、级联 revoke、validation trust_basis | 不把 LLM truth gate 视为数学证明 |
| Archon 与 Horizon | 明确验收、持久任务与资源约束 | task 状态、超时、输出上限、路线次数、恢复、delivery-check | 没有分布式调度、常驻服务、容器隔离或自动集群部署 |

## 与旧探索方式的具体差别

旧 demo 围绕 Phase 文件检查和 claim 登记。它回答“这一轮文件是否齐全”，没有直接表示“这一轮解决哪个证明义务”“为何值得继续”“哪个前提变化会让结论失效”。

新流程先写精确目标和可接受证据，再建立证明义务。每条路线明确下一问题与停止条件。有限实验可作为 `finite_support` 被接纳并存入台账，但不能消除全称证明义务。只有新接纳的 proved/disproved claim 才能获得路线进展记账；同一 claim 重复引用不会再次获得进展。达到执行次数或无进展上限后，路线停放，由新的具体理由支持后续研究选择。

## 原始资料与代码使用

下列资料用于说明表中的方法来源。框架接口和证据要求见[架构说明](architecture.md)。

- [OpenAI First Proof 论文](https://cdn.openai.com/pdf/26177a73-3b75-4828-8c91-e8f1cf27aaa0/oai_first_proof.pdf)
- [AlphaProof 论文](https://www.nature.com/articles/s41586-025-09833-y)
- [Aristotle 报告](https://arxiv.org/abs/2510.01346)；[DeepSeek Prover V2](https://arxiv.org/abs/2504.21801)
- [Pharos](https://github.com/frenzymath/Pharos)；[OSVerify](https://github.com/OSVerify/OSVerify)；[SHEAF](https://github.com/frenzymath/SHEAF)
- [Danus](https://github.com/frenzymath/Danus)；[Archon](https://github.com/frenzymath/Archon)；[Horizon](https://github.com/frenzymath/Archon-Horizon)
- [OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve)
