# Todo

只记录有现有证据支持的后续工作。本文件不授权修改代码、调用收费 API、删除数据、上传或发布。

## P0：建立可验证基线

- [x] 2026-07-27 完整审查：本机 Python 3.13.14 + `.venv` 已安装声明开发依赖；`ruff check clean_auto tests` 通过；`pytest` 收集并通过 **345** 项。
- [x] 2026-07-27 完整审查：在临时目录构建包并通过 `twine check`。
- [x] 2026-07-27：`origin/main` @ `474a48f`（1.8.5）GitHub Actions 为 **success**。
- [ ] 1.8.6 候选提交 Push 后，确认对应 SHA 的完整 CI 矩阵通过，再创建稳定 Tag。

## P0：调查已观察到的处理失败

- [ ] 在并发和停止/继续条件下复现或排除 `Cannot send a request, as the client has been closed.`。
- [ ] 确认客户端生命周期、未完成 future、停止处理和 context manager 退出的回归测试覆盖。
  - 现状：调度器在 stop 后会取消未开始任务并等待已运行 future；正常路径下 `ApiClient` 在调度结束后才关闭。仍缺少“关闭中的 client + 未完成请求”专项回归。
- [ ] 复核日志中的严重保留率失败，区分接口截断、提示词行为、token 限制和正常质量拦截。
- [ ] 在确认测试副本和 API 费用前，不使用真实资料进行故障复现。

## P1：运行数据治理

- [ ] 确认日志、复核文件、partial 和 manifest 的备份及保留策略。

## P1：同步文档与代码

- [x] 已确认版本事实：v1.8.0 的 workers 范围为 1-5。
- [x] 已确认版本事实：v1.8.1 将 workers 范围扩大为 1-10，并已同步 `CHANGELOG.md` 和 README。
- [x] 2026-07-27：准备发布 **1.8.6**；版本号同步到 `__init__.py`、`pyproject.toml`、README、CHANGELOG 与版本测试。
- [x] 2026-07-27：`requirements-dev.txt` 已与 `pyproject.toml` 的 dev 依赖对齐。
- [ ] 将历史开发记录（如 `development_log.md`、CHANGELOG 旧条目上下文）与当前权威状态分离，或清楚标记已过时段落。

## P2：平台验证

- [ ] 完成 PowerShell 7 语法和人工冒烟测试。
- [ ] 重新检查 PowerShell 5.1 重定向输出编码（本机 `--help` 在部分控制台出现中文乱码，需区分控制台编码与程序输出）。
- [x] 远程 CI 矩阵已对 Windows 3.10/3.12/3.14 与 Ubuntu 3.10/3.12 在 `474a48f` 跑通；本地完整审查在 Windows Python 3.13 复验。
- [ ] 确认 1.8.6 候选 SHA 的完整 CI 矩阵。

## P2：可维护性

- [ ] 评估 `api_client.py`、`chunking.py`、`pipeline.py`、`config.py` 是否可在不改变行为的前提下拆分。
- [ ] 明确复制工作目录中 `build/`、`*.egg-info`、缓存和运行产物的清理策略；任何删除需单独确认。
- [ ] 确认是否需要接入精确 tokenizer；当前实现为近似估算。
- [ ] 确认严格 Front Matter 校验是否继续保持可选。
- [ ] 降低 basedpyright 警告量，或明确“仅错误阻断、警告不阻断”的类型检查策略，并决定是否纳入 CI。

## 待确认信息

- 目标 API 提供方及模型兼容限制。
- 真实或 mock 集成测试允许的 API 费用。
- YAML 严格校验的默认策略。
- 正式使用的质量阈值。
- 运行数据备份和保留策略。
