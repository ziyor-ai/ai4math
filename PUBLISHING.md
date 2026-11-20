# 构建源码发布包

从源码检出运行下列命令。输出路径应尚未存在，命令会打印 ZIP 的 SHA-256 和文件数。

```console
python scripts/package_public.py output/release/ai4math-0.7.1-source.zip
```

包内包含框架、活跃项目 Skill、工作流文档、通用示例、离线测试和公开学术种子。实际研究工作区、本机学术库、运行日志和验收回执保存在本地。发布前按[打包清单](scripts/package_public.py)核对选入 Git 的路径，保留 [LICENSE](LICENSE) 和 [NOTICE](NOTICE)。

在新目录解包后运行离线检查。

```console
python -m pip install -e . "pytest>=8,<10"
python -m pytest -q -p no:cacheprovider
python -m ai4math codex-skills check
python -m examples.run_finite_demo workspaces/release-finite-demo
python -m ai4math --workspace workspaces/release-finite-demo audit
```

GitHub Actions 的 Windows 作业分别在 Python 3.10 和 3.12 上运行测试与有限算例。
