# 外部任务阶段预算与停止协议

普通外部任务省略 `phase_budgets` 时使用无阶段截止的合同。总时长与输出字节上限默认均为 `null`，可按评测协议显式设定。下文描述明确提供四阶段预算的合同。

```json
{
  "timeout_seconds": 1800,
  "phase_budgets": {
    "literature": 300,
    "coordinator_review": 180,
    "first_check": 1140,
    "seal": 180
  },
  "external_supervision": "process"
}
```

四阶段必须各自是正整数秒，总和严格等于 `timeout_seconds`。`literature` 在框架启动事务内、worker 派发前使用服务器 UTC 开始计时；`task.literature_ready` 转入独立有界的协调等待；`task.release_exploration` 启动首检；worker 在首检到期前调用 `task.begin_seal`。封存有独立截止，总截止始终截断每个阶段。阶段不能重入，未用完的预算不转让，worker 回填时间无效。这里的“服务器”是运行框架的可信本机 Python 进程，不是远程认证服务；actor 是归属标签，不是访问控制。

## 受管 CLI 子会话

把实际 worker 的可执行文件和参数写入新的 UTF-8 JSON 数组文件。例如 `["C:/path/codex.exe", "exec", "...实际参数...", "读取指定绝对路径的 PROMPT.md 和 input.json，并严格按其中协议执行"]`。框架不猜测模型参数，不自动付费调用。该命令本身必须由用户授权。

```powershell
python -m ai4math --workspace workspaces/new-workspace --actor coordinator supervise-external new-task --worker-argv-file research-inputs/new-worker-argv.json
```

监督器先冻结契约和启动时间，然后创建隐藏 Windows bootstrap，把 bootstrap 纳入具有 `KILL_ON_JOB_CLOSE` 的 Job Object，最后才放行 worker。参数始终是数组，无 shell 展开。所有本机后代纳入同一个 Job；超时、输出超限、意外退出、封存完成都终止整个 Job，并核对活跃进程数为零。监督器自身异常退出也会关闭 Job。POSIX 使用独立进程组；这不是抵御恶意程序主动逃逸的沙箱。受管 worker 是可信本地程序。

独立单调时钟 watchdog 不访问 SQLite，因此 worker 持有数据库写锁不会拖延本机进程停止。阶段更新只有从框架已提交状态读取；服务器时间预算和本轮单调时钟共同约束，保守超时会失败，不会补发预算。数据库仍被其他程序锁住时失败回执可能延后，但停止不等待数据库。

worker 提交独立文献回执后可以在同一 CLI 子会话中有界轮询：

```powershell
python -m ai4math --workspace workspaces/new-workspace check-external new-task
```

只在 `external_stage=exploration` 后开展首检；等待状态为 `awaiting_coordinator_review` 时不可提前研究。协调方另行调用 `task.release_exploration`。不需要退出后重启阶段或重新分配预算。worker 过早退出视为失败；worker 若需要分次 CLI 运行，应由一个受管本机 wrapper 持续运行并保留同一任务和时钟。

## 限时封存与后续验收

首检结束时，worker 调用 `task.begin_seal`，冻结全部输出，再冻结下列 JSON 清单：

```json
{
  "task_id": "new-task",
  "contract_sha256": "input.json 的冻结 artifact id",
  "statement_digest": "锁定节点摘要",
  "input_artifact_ids": ["完整、排序的原始输入 artifact id"],
  "candidate_digest": "精确候选摘要；无候选时为 null",
  "artifacts": [{"artifact_id": "输出 artifact id", "sha256": "同一 SHA-256"}]
}
```

用 `artifact.import` 导入清单后，在 seal 截止前提交：

```json
{
  "task": "new-task",
  "artifact_ids": ["全部输出 artifact id"],
  "seal_manifest_artifact": "清单 artifact id"
}
```

该 payload 用于 `apply task.submit_seal`。框架重新核验冻结 handoff、原输入和所有输出字节；清单必须精确绑定任务、合同、命题、原输入集合、候选、输出集合与哈希。输出不得是原始输入/合同/提示词，且须在放行后首次导入；清单须在 seal 窗口首次导入。所有计算与验证成本均处于 seal 预算中，校验结束时再次检查期限。

提交成功产生不可变 `external_seal` 回执；任务保持 `status=running` 占用路线，阶段变为 `sealed`，含义为“已封存，待验收”。监督器立即停止本机 worker 树，登记 `task.confirm_stop`。此时 `supervise-external` 返回 0 仅表示及时封存和本机停止已确认。

协调方之后独立验收，使用 `task.finish_external`，传入 `status`、`summary`、同一 `artifact_ids` 和 `seal_manifest_artifact`。完成必须已经确认停止且精确对应已封存输出集合。后续验收不消耗 worker seal 时间，不能添加新的研究输出。成功终态仍为 `evidence_level=unknown`；数学接纳走独立验证流程。

任何阶段到期都会独立提交 `unknown_timeout` 和停止请求；触发拒绝的原操作回滚不会撤销超时。超时、失败和中断不能变 completed，既有路线次数/池名额不会退回。迟到产物只能 `artifact.import` 存档，不能进入成功封存或改写旧结果。

## Hosted collaboration 的边界

`external_supervision="host"` 允许 `start-external`，但框架没有 hosted model 的 PID，也无法直接调用宿主的 interrupt 工具。协调方必须持续有界检查阶段，在停止请求/封存/到期时调用真实宿主中断工具，观察确认，再用 `task.confirm_stop` 提交 `{"task":"...","confirmed":true,"evidence":"实际工具返回和目标标识"}`。只发停止文字、只拒收产物或者只登记请求均不等于物理停止；未经确认一直保留 `stop_state=requested`。

CLI process 监督确认的范围也是本机 worker 与其后代。它不能证明远端供应商的模型生成或计费已停止。需要该保证时必须另有供应商取消接口及确认回执。旧无阶段预算合同可读取和按原语义处理，但不能宣称获得新的硬停止保证。

