# Changelog

## [1.6.1] - 2026-07-23

### Fixed

- 修复 GitHub Source ZIP 中 Windows `.bat` 文件以 LF 行尾保存，导致 `cmd.exe` 错误解析一键菜单的问题。
- 修复空输入、无效输入和 stdin EOF 可能穿透到 Dry-run 或形成异常循环的问题。
- 修复菜单启动失败时可能误触发其他菜单动作的问题。
- 确保 Source archive 中 `.bat` 和 `.cmd` 文件保留 CRLF 且无 BOM。

### Compatibility

- 不改变批次、并发、manifest、selection、缓存、chunking、metadata 或输出契约。
- `.venv` 可用时优先使用；否则仅在系统 Python 可以运行当前项目时安全回退。
- 中文菜单、一键安装和菜单精简不包含在 v1.6.1 中。

## [1.6.0] - 2026-07-23

### Added

- `--selection-file`：使用经过验证的 JSON 清单处理指定 Markdown 文件
- 批次 manifest 历史和 latest 批次引用
- `--resume-batch [BATCH_ID]`：继续 pending/interrupted 文件
- `--retry-failed [BATCH_ID]`：为失败文件创建独立重试子批次
- `--workers 1-5`：有界文件级并发，默认 1
- `--batch-status`：只读查看最近批次状态
- Windows 一键菜单：多选 Markdown、选择 input 子目录、设置并发、继续、重试和查看状态
- Windows PowerShell 文件/目录选择辅助脚本及文本降级路径

### Changed

- 批处理现在支持批次状态、恢复、失败隔离和受限并发
- workers=1 保持原串行处理行为；workers>1 按文件级并发处理，单文件内 chunks 保持顺序
- 并发模式共享 API 冷却、限制网络请求并保护 JSONL 日志写入

### Safety / Compatibility

- 选择文件仍仅允许 input/ 及其子目录；Python 层执行路径、符号链接、Markdown 和 cleaned 文件校验
- 并发范围限制为 1-5；单文件 metadata、缓存、输出布局和 chunking 契约不变
- 并发模式不支持 dry-run 或文件间暂停；workers=1 保留原有串行连续失败停止行为

### Known limitations

- PowerShell GUI 选择器仅适用于 Windows；不可用时使用文本降级或 CLI selection-file
- workers 设置仅在当前一键菜单会话有效
- 不支持 input/ 外文件选择、复杂桌面 GUI、拖放或 chunk 级并发
- cleaned 输出仍采用 UTF-8 和现有组装规则；prompt.md 仍是用户工作目录中的外部必需配置

## [1.5.0] - 2026-07-22

### Changed

- Markdown 分块现在保留源文本空白、缩进和换行结构
- 文本读取保留 CRLF、LF 和混合换行
- 加强 pipeline、assembly、recovery 和缓存行为验证

### Fixed

- 修复通用换行处理将 CRLF 转换为 LF 的问题
- 修复分块过程可能由 `strip()` 或文本重建造成的内容与 Markdown 结构损失
- 保证所有 chunks 拼接后可恢复传给 chunking 的完整 `source_text`

### Testing

- 覆盖 UTF-8、UTF-8 BOM、GB18030 和 GBK 兼容输入
- 覆盖 Markdown 列表、引用、代码块和表格的 CRLF 保真
- 覆盖 API message、assembly、recovery、缓存复用、失败重试和坏文件隔离

### Known limitations

- cleaned 输出继续使用现有 UTF-8 和组装契约
- 不承诺继承源文件的原始编码
- 不支持 UTF-16 或自动编码探测
- `prompt.md` 是用户工作目录中的必需外部配置，不包含在 wheel 中
- 本版本不包含多文件并发和一键开始菜单增强
