# Development Log

## Current State

Current branch: `feature/encoding-preserve`

Phase 1:

- [x] CRLF 保真处理

Phase 2:

- [x] 编码与换行保真阶段完成
- [x] 第一批编码契约测试完成
- [x] 第二批编码与 CRLF 端到端测试完成
- 本阶段未修改生产代码。

Next stage:

- v0.3.0 发布准备

## Git History

- 2026-07-22: `feature/crlf-preserve` 使用显式 `newline=""` 读取文本，保留 CRLF、LF
  和混合换行，并补充跨平台回归测试。
- 2026-07-22: `feature/encoding-preserve` 完成 Phase 2 第一批编码契约测试，覆盖编码、
  换行、原始字节哈希、API 消息、assembly 质量检查和 recovery 行为；未修改生产代码。
- 2026-07-22: `feature/encoding-preserve` 完成 Phase 2 第二批编码与 CRLF 端到端测试，
  覆盖规划、分片、处理、组装、缓存恢复和失败重试流程；未修改生产代码。
- 2026-07-22: Phase 2 编码与换行保真范围审查通过，已达到可合并标准；下一阶段为
  v0.3.0 发布准备。

## Known Issues

1. [已解决] 文本读取曾通过通用换行模式把 CRLF 转换为 LF；`read_text()` 现使用
   `newline=""` 保留原始换行符。

## Test Result

- `pytest -q`: 135 passed
- `ruff check clean_auto tests`: All checks passed

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
