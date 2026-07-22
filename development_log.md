# Development Log

## Current State

Current branch: `feature/encoding-preserve`

Phase 1:

- [x] CRLF 保真处理

Phase 2:

- [x] 第一批编码契约测试完成
- [ ] Phase 2 后续工作待完成
- 本批次未修改生产代码。

## Git History

- 2026-07-22: `feature/crlf-preserve` 使用显式 `newline=""` 读取文本，保留 CRLF、LF
  和混合换行，并补充跨平台回归测试。
- 2026-07-22: `feature/encoding-preserve` 完成 Phase 2 第一批编码契约测试，覆盖编码、
  换行、原始字节哈希、API 消息、assembly 质量检查和 recovery 行为；未修改生产代码。

## Known Issues

1. [已解决] 文本读取曾通过通用换行模式把 CRLF 转换为 LF；`read_text()` 现使用
   `newline=""` 保留原始换行符。

## Test Result

- `pytest -q`: 128 passed
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
