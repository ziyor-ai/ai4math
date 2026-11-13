# VERIFY_LEDGER

本 ledger 记录论文单元与证据 claim 的对应关系。它不是数学事实来源；
交付前必须由人工或独立审查确认所有状态。

| unit_id | label | source_claim | evidence_level | parameter_domain | status | last_checked | delivery |
|---|---|---|---|---|---|---|---|
|  |  |  |  |  | pending |  | blocked |

允许的 `status`：`pending`、`correct`、`wrong`、`uncovered`、`overridden`。  
允许的 `evidence_level`：`proved`、`disproved`、`finite_support`、`unknown`。
