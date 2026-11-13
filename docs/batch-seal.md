# 原子批量封存

`task.submit_seal_batch` 解决同库多个 worker 各提交数百个产物时，逐件 `artifact.import` 在写锁内重复审计完整历史造成的锁饥饿。旧 `artifact.import`、`task.submit_seal` 和既有回执保持兼容；使用旧 helper 不会自动获得批量行为。下一批 helper 必须使用新的版本和以下接口，不覆盖历史 helper 或重启失败任务。

## 工作流与输入

worker 先完成原有文献 ready、协调审核 release 和首检。可以在 exploration 阶段运行预检，及时发现所有旧字节、重复路径或容量问题；只有进入 seal 后才能提交。原来的服务器时钟、阶段截止和独立本机进程树 watchdog 保持有效。

`batch.json` 是一个对象，示例如下；路径只相对于本任务 `tasks/TASK/return`，不是 shell 路径：

```json
{
  "task": "my-new-task",
  "contract_sha256": "实际冻结input.json的64位SHA256",
  "artifacts": [
    {"path": "argument.md", "sha256": "该文件当前完整字节的64位SHA256"},
    {"path": "sources/NewLemma.lean", "sha256": "该文件当前完整字节的64位SHA256"}
  ]
}
```

```powershell
# 默认或显式 --check 都是只读：不暂存对象、不导入、不改阶段，过期也不登记超时。
python -m ai4math --workspace $ws --actor $worker seal-external my-new-task --file batch.json --check

# 原有 begin_seal 入口，须仍在 first_check 截止以内。
python -m ai4math --workspace $ws --actor $worker apply task.begin_seal --json '{"task":"my-new-task"}'

# 只有此命令执行批量提交。
python -m ai4math --workspace $ws --actor $worker seal-external my-new-task --file batch.json --submit
```

稳定 JSON API 是 `w.apply('task.preflight_seal_batch', payload, actor)` 和 `w.apply('task.submit_seal_batch', payload, actor)`；CLI 的 `apply` 也支持这两项。actor 必须是本任务登记的 worker 或协调者，本地 actor 标签不是身份认证。CLI 预检使用 SQLite `mode=ro`，不初始化或修改数据库 schema。

预检失败返回 `passed=false` 和所有可检查条目的 `errors`，CLI 返回2；提交错误中的 `preflight` 字段保留结构化报告。列表限制为1至1000项，每个相对路径最多1024字符；单文件超限会跳过其内容读取。绝对路径、跨任务路径、根目录 junction/symlink 别名、重复路径、重复SHA、expected SHA不符、原始输入/合同/提示和历史首次导入均拒绝。遇到旧字节不会只返回第一项，方便一次修正完整输出选择。

已有同SHA对象只有在**首次登记来源位于本任务return内、首次actor是本worker或协调者、首次导入在release后**时才能作为本任务输出；另一任务在release后先导入相同字节也不能冒充本任务新输出。之后补一条import记录不能改变首次来源。未改源码或相同版本/命令诊断字节应在新的来源清单中引用旧artifact与SHA，不能为了过关增加无意义注释。完整原始日志与数学产物新鲜度是两个不同问题。

合同的 `output_limit_bytes` 为正整数时，输出文件加框架生成的seal manifest总字节必须不超过该值；manifest开销也会在预检中报告。新托管 Codex 合同可显式设为 `null`，此时封存文件、manifest及原生监督器捕获流没有任务输出字节截止。写入文件的工具输出仍须从首条工具命令起在完整命令台账中累计计量，不能把未知、截断、漏记范围声明为完整。单次预览可缩短，但完整原始记录必须保留。新API不拦截任意文件IO，也不替代这些日志审查；旧合同保持原有限额。

## 原子性、审计与截止

提交先在锁外核对完整选择和冻结handoff，再复制并校验内容寻址字节。暂存只发布不可覆盖的objects，不登记artifact/import/外部seal。随后在一个SQLite WAL读事务中完整重放事件链并比较全部projection；不是缓存或只比较tail hash。`UPDATE records SET body=body WHERE 0` 将**同一个已审核快照**升级为writer而不更改记录。若另一writer已经提交，SQLite拒绝旧快照升级，整个读事务回滚，然后完整重审；最多32次，每次锁等待最多50ms，backoff最多50ms。只重试BUSY/LOCKED类获取失败，不重执行提交主体。

取得写权后重新核对真实task、contract/candidate/input绑定、全部源和暂存字节、来源及新鲜度，随后在同一事件中登记所有artifact/import、manifest及external_seal。任何失败均回滚全部记录，不会留下部分封存。事件序列化完成后、紧邻COMMIT之前，再按原未封存task的可信截止检查；不能因pending投影已经写成sealed而跳过检查。失败/超时保留unknown，不能复活completed。

完整审计指事件哈希链和数据库投影，不是每次重读全库所有对象字节。本次涉及的handoff、输入和输出对象会校验实际字节；`ai4math audit` 仍负责全库所有已登记对象的字节审计。前后不降低原有接口的审计要求。

提交失败可能留下**未登记**的不可变objects，属于未接受的暂存字节，不构成artifact或seal；不删除它们，避免误删被另一任务引用的相同内容。对象先发布与数据库原子提交是不同边界。对文件同时恶意改写不提供OS安全沙箱；在最后核对前可观察到的source/object变化均拒绝，内容寻址对象后续仍受audit检测。

返回值沿用 `external_stage=sealed, acceptance=pending, receipt`，manifest在内容寻址对象中；不要求worker另外写 `native-seal-manifest.json`。receipt仍可由旧 `task.confirm_stop` 和 `task.finish_external` 消费。数学接纳和停止确认仍独立，`sealed`绝不是证明完成。

`batch_metrics` 记录预检、暂存、完整审计累计时间、升级锁等待、重试退避、尝试次数、写锁持有、事件准备和COMMIT耗时，以及审计的event数/tail。精确提交后耗时只在返回值中，不追改已提交事件；调用方必须保留实际CLI输出和退出码。

## 新合同预算与验收

对于下一批多产物任务，可在获得明确授权后登记 `timeout_seconds=4200`、`phase_budgets={literature:600, coordinator_review:300, first_check:2400, seal:900}`、`output_limit_bytes=2000000`，独立validator另登记1800秒。总额度必须与四阶段严格相加。这些数值是显式新合同示例，不改变默认行为，不给旧300秒seal任务补时，也不能退回旧失败的消耗。

并发验收应覆盖实际规模的事件字节和projection行/字节数，不只看event数量；同时测试三批150–200产物与begin_seal/release/expire控制调用。还应验证快照失效重审、writer持有、全量冲突、跨任务同SHA竞争、source/object TOCTOU、回滚、末端截止与独立Windows watchdog。批量性能改进不能代替数学范围和证书验收。
