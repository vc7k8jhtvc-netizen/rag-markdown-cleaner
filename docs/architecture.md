# Architecture

## 文档范围

本文只描述可以由源码确认的稳定架构，供后续 AI Agent 快速建立上下文。

本文不记录当前批次、输入输出内容、Git 状态、CI 结果或本机环境状态；这些易变化信息应记录在 `todo.md` 或专项检查报告中。

## 系统定位

项目是本地 Markdown 批量清洗工具。它读取 PDF/OCR 转换后的 Markdown，将文档安全分片后发送到 OpenAI Chat Completions 兼容接口，校验模型响应，保存分片和完整结果，并维护可恢复的批次状态。

当前代码不包含 Web 服务、数据库、用户登录、向量数据库或 RAG 索引写入功能。

## 入口

- `python -m clean_auto` → `clean_auto.__main__.run()`
- `rag-cleaner` 命令 → `clean_auto.pipeline.run()`
- `clean_auto_run.py` → `clean_auto.pipeline.run()`
- 根目录 `.bat` 和 `scripts/` 下 PowerShell 脚本提供 Windows 安装、菜单、文件选择、暂停和停止入口。

## 核心组件

| 模块 | 职责 |
|---|---|
| `pipeline.py` | CLI 总流程、输入选择、批次生命周期、调度、汇总和退出码 |
| `config.py` | 参数、环境变量、路径、运行配置、脱敏、原子写入和通用数据结构 |
| `chunking.py` | 输入扫描、编码读取、路径保护、Markdown 无损分片、文件计划和缓存检查 |
| `api_client.py` | HTTPX 客户端、SSE 解析、超时、重试、共享限流冷却和响应大小限制 |
| `processor.py` | 单文件/单分片处理、调用模型、校验、质量检查、保存和触发合并 |
| `assembly.py` | 验证分片、按顺序合并、最终质量检查、发布结果和同步复核副本 |
| `validation.py` | 响应格式、错误页和 YAML Front Matter 校验 |
| `quality.py` | 保留率、扩写率、标题、题目、数字、表格、URL 和广告信号检查 |
| `quality_settings.py` | 从环境变量加载质量阈值 |
| `batch_manifest.py` | 批次 manifest、latest 指针、继续批次和重试失败文件 |
| `scheduler.py` | 有界文件级并发调度 |
| `selection.py` | 选择清单 schema 和 input 相对路径安全校验 |
| `locking.py` | 工作目录单实例锁 |
| `control.py` | 暂停、继续、安全停止和受控等待 |
| `progress.py` | 线程安全的进度事件和终端显示 |
| `model_budget.py` | 近似 token 预算和可选输出 token 限制 |
| `metadata_schema.py` | 生成 metadata 的 schema 名称和版本 |

## 主数据流

1. 解析 CLI 参数，按 `--base-dir`、`RAG_CLEANER_HOME`、当前目录的优先级确定工作目录。
2. 从工作目录加载 `.env` 和必需的 `prompt.md`。
3. 通过扫描 `input/`、选择清单、继续批次或重试失败批次确定源文件。
4. 正式处理时获取工作目录锁，并创建或加载批次 manifest。
5. 按 `utf-8-sig`、`utf-8`、`gb18030`、`gbk` 尝试解码；检查大小、路径、符号链接和 SHA-256。
6. 按 Markdown 安全结构边界分片，所有分片连接后必须等于解码后的源文本。
7. 使用有界线程调度文件或分片；网络请求共享同一个并发上限。
8. 为每个分片构造“不可信文档”消息，流式调用 `{base_url}/chat/completions`。
9. 校验响应格式并执行内容质量检查；被拒绝但已完整接收的候选另存为失败诊断文件。
10. 原子保存分片正文和 metadata；partial 响应不视为完成分片，人工确认且指纹仍匹配的失败候选可转为完成分片。
11. 所有分片有效后，按分片编号合并，执行完整文件质量检查并发布最终结果。
12. 需要复核时，将完整文件和复核报告同步到 `review/`。
13. 全程更新 JSONL 日志和批次 manifest。

## 工作目录数据

| 路径 | 内容 |
|---|---|
| `input/` | 待处理 Markdown，本地用户数据 |
| `output/` | 分片、完整结果及 metadata |
| `review/` | 需要人工复核的完整结果和报告 |
| `logs/batch.jsonl` | 追加式运行事件 |
| `logs/batches/` | 批次 manifest 和 `latest.json` |
| `prompt.md` | 必需的外部系统提示词 |
| `.env` | 本地 API 和质量配置，不得提交 |
| `.clean_auto.lock` | 正式任务锁 |
| `pause.flag`、`stop.flag` | 运行控制文件 |

## 缓存与恢复

分片只有在以下身份全部匹配时才可复用：源文件哈希、分片哈希、提示词哈希、模型、Base URL、分片编号和总数、严格校验模式、规范策略、输出哈希。

质量阈值使用独立策略指纹。策略变化时，已有模型响应继续作为内容缓存复用，但必须按当前阈值重新校验并重新组装完整文件，不重复调用 API；缺少策略指纹的旧 metadata 会在重新组装时兼容读取并升级。

提示词身份使用 `PROMPT_IDENTITY_VERSION=2`。读取 `prompt.md` 时，CRLF、LF 和单独 CR
统一规范化为 LF 后生成主指纹；仅换行格式差异不会使缓存失效。系统同时兼容旧版本生成的
LF/CRLF 提示词指纹，只有提示词文本实际变化时才使对应缓存失效。

完整文件丢失但所有分片仍有效时，可以只重新合并，不重复调用 API。`*.partial.md` 仅用于诊断；对应分片下次会重新请求。

需要人工复核时，完整文件和复核报告必须成功同步到 `review/` 才能把文件视为处理成功。复核副本缺失、损坏或不同步时，缓存检查会要求重新组装并重试同步。

## 安全约束

- 输入、输出、选择清单和控制文件路径被限制在预期目录内。
- 拒绝符号链接输入、路径穿越和不安全相对路径。
- 远程接口必须使用 HTTPS；HTTP 只允许 loopback。
- 日志写入前会执行部分密钥脱敏，并尽量使用相对路径。
- 处理前和最终发布前检查源文件 SHA-256。
- 严重质量错误默认阻止保存或发布；被拒绝候选可供人工检查，只有明确确认且指纹仍匹配时才可转为完成分片；警告触发人工复核。
