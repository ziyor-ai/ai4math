# GitHub 首次发布交接

本包用于建立新的源码仓库。解压到一个空目录，确认根目录包含 README、LICENSE 和隐藏的 GitHub 配置。

## 交接验收

- 新任务的时长和输出字节上限默认为 `null`。工具预览按页读取，原始输出完整留存。 [任务合同](docs/operations.md)
- 9,437,797 字节的 PDF 测试件已完整归档，字节数和 SHA-256 一致，带页码的摘录已登记。 [文献归档](docs/exploration.md)
- 五元超域实跑从干净解压目录启动。托管 Codex 研究任务约 347 秒完成封存和停机，四个独立验证任务通过，验收和暂停各追加一条历史，29 个材料链接已核对。协调和文献会话通过本地测试接口调用真实 Codex。 [README 验证结果](README.md#发布验证)
- ZIP 根目录可直接作为新仓库。源码、测试、模板、示例和学术资料种子随包交付。G0 研究材料、运行日志、本机学术库和验收回执留在本机。解压前可用随包提供的 `.sha256` 校验文件核对 ZIP。

## 首次公开步骤

1. 由权利人确认发布授权，在首次公开前补齐准确的版权年份和署名。Apache-2.0 全文已附在 [LICENSE](LICENSE)。适用的第三方署名与通知随相应材料保留。
2. 在 GitHub 建立空仓库，使用团队确定的仓库名称和组织。用下面的命令建立首个提交，并将占位的远程地址替换成新仓库地址。
3. 在 GitHub 的 Actions 页面检查 Windows 测试。后续发布源码包可从仓库根目录执行 `python scripts/package_public.py output/release/ai4math-0.7.0-source.zip`。

```console
git init -b main
git add .
git diff --cached --stat
git commit -m "Initial open source release"
git remote add origin https://github.com/OWNER/REPOSITORY.git
git push -u origin main
```
