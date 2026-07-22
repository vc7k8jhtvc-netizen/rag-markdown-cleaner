# Development Log

## Current State

Phase 1:

- [x] CRLF 保真处理

## Git History

- 2026-07-22: `feature/crlf-preserve` 使用显式 `newline=""` 读取文本，保留 CRLF、LF
  和混合换行，并补充跨平台回归测试。

## Known Issues

1. [已解决] 文本读取曾通过通用换行模式把 CRLF 转换为 LF；`read_text()` 现使用
   `newline=""` 保留原始换行符。

## Test Result

- `pytest -q`: 113 passed
- `ruff check clean_auto tests`: All checks passed
