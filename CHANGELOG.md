# Changelog

## [1.8.3] - 2026-07-26

### Fixed

- 格式或质量校验失败的分片现在会保留 `*_failed.md` 诊断候选和 metadata，方便人工判断失败内容；默认不会参与缓存或完整文件合并，用户明确确认后可转为完成分片。

## [1.8.2] - 2026-07-26

### Fixed

- 质量阈值现在进入缓存身份；阈值变化会重新校验和组装已有结果，但不会重复调用 API。
- 旧分片 metadata 会在重新校验时兼容读取并升级到当前质量策略。
- `review/` 副本同步失败不再被吞掉；缺失或不同步的复核副本会触发后续重试。

## [1.8.1] - 2026-07-26

### Fixed

- 修复同一份 `prompt.md` 因 CRLF/LF 读取方式变化而被误判为提示词已修改、导致完整缓存重复调用 API 的问题。
- 提示词身份统一使用 LF 指纹，并兼容旧版 LF/CRLF 指纹；真正的提示词文字变化仍会使缓存失效。

### Changed

- workers 的可配置范围扩大为 1 到 10；文件和分片仍共用同一个并发上限。

## [1.8.0] - 2026-07-26

### Added

- 增加文件内分片并发；即使只选择一个大文件，也可以按照 workers 设置同时清洗多个分片。
- 文件和分片共用同一个 API 并发上限，避免多文件与多分片并发相乘。
- 流式进度显示已接收 B/KB，每个并发任务固定占用自己的进度行，并显示文件总体完成百分比。
- Windows 一键菜单开始处理时会优先继续最近一次尚未完成的批次。

### Changed

- workers 的含义调整为“同时处理的清洗任务数”，范围仍为 1 到 5；workers=1 保持原有串行行为。
- 并发分片完成后仍按分片编号组装，已验证完成的分片继续支持缓存复用和中断恢复。
- 恢复固定空行分隔的分片组装规则，避免边界空白处理不一致导致完整性测试失败。

### Validation

- 使用临时文件和模拟 API 验证单文件分片并发、全局请求上限、进度行隔离、失败停止、缓存复用和有序组装。
- 未调用真实 API，未处理真实输入资料。

## [1.7.3] - 2026-07-24

### Fixed

- 修复已保存分片在包含合法首尾空白时无法通过自身 `output_sha256` 完整性校验的问题；哈希现始终基于完整原始文本。
- 修复该不一致可能导致的当次 assembly 失败、失败分片误计数和后续运行重复 API 调用风险；不匹配的旧 metadata 仍保守失效并重新处理。
- 强化 fenced Markdown 与首尾空白的数据保真，完整保留 fenced `text`、`markdown`、无语言围栏、前导换行、尾随空格、尾随换行和多个末尾空行。

## [1.7.2] - 2026-07-24

### Fixed

- 修复 source snapshot、chunks 与 source SHA-256 可能来自不同文件版本的竞态，拒绝使用不一致计划。
- 将 strict validation 和 Markdown 规范策略纳入 chunk 与最终输出缓存身份，避免严格模式复用宽松缓存。
- 修复 CP936 无法表示的路径导致进度输出崩溃；不可表示字符以稳定转义保留。
- 新增带文件序号、相对路径、分片号和脱敏错误摘要的失败分片进度事件；partial/review 进度不再显示绝对路径。
- 保留合法 fenced Markdown 与有意义首尾空白，不再自动剥离整篇 outer code fence。

### Known limitations

- Windows PowerShell 5.1 重定向输出可能因宿主表现为 CP936 或 UTF-16LE，需要按实际编码读取。
- 本机未安装 PowerShell 7，本版本尚未进行 PowerShell 7 手工验证。

## [1.7.1] - 2026-07-24

### Fixed

- 修复并发处理时进度可见性和上下文缺失；进度事件现包含文件序号、相对路径和适用的分片上下文。
- 恢复并统一等待/重试、暂停/恢复、partial/review/质量提示、分片完成/缓存跳过事件，并修正基于 manifest 的批次进度计数。

### Known limitations

- Windows PowerShell 5.1 的重定向输出可能因宿主表现为 CP936 或 UTF-16LE，需要按实际编码读取。
- 本机未安装 PowerShell 7，本版本尚未进行 PowerShell 7 手工验证。

## [1.7.0] - 2026-07-23

### Added

- workers > 1 时由 scheduler 主线程统一输出完整中文文件级进度事件，覆盖开始、分片进度、缓存跳过、成功、失败和中断，消除 stdout/SSE 覆盖与交错。
- 新增简洁中文 PowerShell 菜单，`一键菜单.bat` 改为最小启动器；保留处理、选择、恢复、重试、状态和更多功能入口。
- 新增 `一键安装.bat` 与项目内 `.venv` 安装/修复流程，支持 Python 3.10+ 检测、健康检查和明确确认后的损坏环境重建。

### Changed

- Windows 环境配置仅使用项目 `.venv`，不静默回退系统 Python，不修改 PATH、注册表或全局 Python。
- Windows `.bat/.cmd` 启动器和 PowerShell 脚本建立 ASCII/无 BOM/CRLF 与 UTF-8 BOM/CRLF 编码契约，兼容 Source ZIP 和 Windows PowerShell 5.1。

### Compatibility / Release

- 不改变 manifest、metadata、JSONL、cache、chunking、stop/resume/retry 或 API 请求语义。
- 发布物仍为 GitHub tag、GitHub Release 和 Source code archives；本项目不上传 PyPI。
- 本阶段本机未安装 PowerShell 7，PowerShell 7 的验证依赖 GitHub Actions Windows runner。

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
