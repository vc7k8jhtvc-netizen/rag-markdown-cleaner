# Development Log

## Current State

Current branch: `release/v1.5.0`

Phase 1:

- [x] CRLF 保真处理

Phase 2:

- [x] 编码与换行保真阶段完成
- [x] 第一批编码契约测试完成
- [x] 第二批编码与 CRLF 端到端测试完成
- 本阶段未修改生产代码。

Next stage:

- [x] v1.5.0 发布候选准备完成（尚未发布）
- 软件版本以 `pyproject.toml` 为权威来源，已从此前基线 `1.4.1` 向前递增至
  `1.5.0`；原 v0.3.0 发布规划已纠正。Phase 1、Phase 2 是开发阶段编号，不是软件版本号。
- 下一步为发布候选审核和 v1.5.0 正式发布。

## Git History

- 2026-07-22: `feature/crlf-preserve` 使用显式 `newline=""` 读取文本，保留 CRLF、LF
  和混合换行，并补充跨平台回归测试。
- 2026-07-22: `feature/encoding-preserve` 完成 Phase 2 第一批编码契约测试，覆盖编码、
  换行、原始字节哈希、API 消息、assembly 质量检查和 recovery 行为；未修改生产代码。
- 2026-07-22: `feature/encoding-preserve` 完成 Phase 2 第二批编码与 CRLF 端到端测试，
  覆盖规划、分片、处理、组装、缓存恢复和失败重试流程；未修改生产代码。
- 2026-07-22: Phase 2 编码与换行保真范围审查通过，已达到可合并标准；下一阶段为
  v1.5.0 发布候选准备。
- 2026-07-22: 发布规划按权威包版本 `1.4.1` 向前递增，原 v0.3.0 计划纠正为
  v1.5.0；开发阶段编号与软件版本号分开管理。
- 2026-07-22: v1.5.0 发布候选准备完成；同步项目、运行时和 README 版本，新增
  CHANGELOG、版本一致性测试、外部 `prompt.md` 配置测试，以及 Windows/Linux CI
  和 wheel/sdist 构建检查。`prompt.md` 保持为用户工作目录中的外部必需配置。

## Known Issues

1. [已解决] 文本读取曾通过通用换行模式把 CRLF 转换为 LF；`read_text()` 现使用
   `newline=""` 保留原始换行符。

## Test Result

- `pytest -q`: 139 passed
- `ruff check clean_auto tests`: All checks passed
- `python -m build`: wheel 和 sdist 构建成功
- `python -m twine check dist/*`: wheel 和 sdist 均通过

新增覆盖：

- UTF-8 与 UTF-8 BOM
- GB18030 与 GBK 兼容输入
- CRLF、LF 和混合换行
- 解码失败和文件系统异常
- `build_file_plan()` 的字符数、chunks 和原始字节哈希
- API user message 的 CRLF 保真
- assembly 质量检查的 CRLF source
- prompt/source hash 与 recovery 行为
- `atomic_write_text()` 的 UTF-8、BOM 和换行契约

第二批新增覆盖：

- 结构化 CRLF Markdown 在规划、分片和原始字节哈希之间保持一致
- UTF-8、UTF-8 BOM 和 GB18030 输入进入正常规划流程
- 无法解码文件按批处理契约隔离，不影响有效文件
- pipeline 规划、处理、API mock、assembly 质量检查和 recovery/cache 完整流程
- prompt BOM/LF/CRLF 及 source CRLF/LF 变化对应的缓存复用和失效行为
- 失败分片重试时复用已完成且哈希有效的分片

## Non-blocking Follow-ups

- 增加 Linux CI 实际运行
- 在稳定环境中补充 `PermissionError` 测试
- UTF-16 或自动编码探测支持需要单独设计
- cleaned 输出继续遵循 UTF-8 和现有组装契约
