# 贡献指南

在源码目录使用 Python 3.10 或以上。提交改动前运行测试，并在新目录完成离线示例。

```console
python -m pip install -e .
python -m pip install "pytest>=8,<10"
python -m pytest -q -p no:cacheprovider
python -m examples.run_finite_demo workspaces/contribution-demo
python -m ai4math --workspace workspaces/contribution-demo audit
python -m ai4math --workspace workspaces/contribution-demo delivery-check
```

报告缺陷时说明 Python 与操作系统版本、最短复现命令、预期行为和实际结果。请使用合成或公开可再分发的最小输入，隐去凭据和私人研究材料。

改动研究流程时，请同时说明证据的适用范围和信任依据。生产与验证应当独立。添加测试时，优先覆盖实际入口、失败路径和已有工作区的兼容性。数学研究记录放在本机工作区，通用说明放在 [操作手册](docs/operations.md)。
