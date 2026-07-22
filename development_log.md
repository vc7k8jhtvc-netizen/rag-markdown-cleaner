# Development Log

## Current State

Current branch: `feature/chinese-menu`

## v1.7.0 Phase 2: Chinese PowerShell Menu (2026-07-23)

- [x] `一键菜单.bat` 已收缩为 ASCII、无 BOM、CRLF 的 PowerShell 启动器；不再承载菜单或回退到系统 Python。
- [x] 新增 UTF-8 with BOM、CRLF 的 `scripts/menu.ps1`，提供中文主菜单、选择与恢复子菜单、会话 workers 设置和低频功能入口。
- [x] 菜单只使用项目 `.venv\\Scripts\\python.exe`；缺失或无法导入项目时安全退出，不启动处理任务。
- [x] 复用现有 CLI 参数和 `scripts/select_input_files.ps1` 的 selection JSON 契约；未修改 pipeline、manifest、cache 或 API 语义。
- [x] Windows PowerShell 5.1 已验证中文菜单、空输入、EOF、缺失 `.venv` 安全提示和 dry-run（不发送 API 请求）。
- [ ] 当前验证主机未安装 PowerShell 7，尚未完成 PowerShell 7 手工冒烟。

## v1.7.0 Phase 1: File Progress Events (2026-07-23)

- [x] 新增内部 `ProgressEvent`、线程安全事件队列和主线程中文行式输出。
- [x] 并发 worker 不再直接写 stdout；分片开始、完成、缓存跳过、失败和中断均通过事件报告。
- [x] 删除 SSE `\r` 单行刷新，最终批次汇总直接使用 finalize 后的 manifest counts。
- [x] 已验证进度事件、scheduler、pipeline、processor、API、assembly 以及完整 pytest 和 Ruff 检查。

## v1.6.1 Release (2026-07-23)

- [x] v1.6.1 于 2026-07-23 正式发布；标签：`v1.6.1`。
- main 发布提交：`18785143656d7b27d8ed96df8365661d2f4ebce6`。
- GitHub Release 已创建并设为 latest；未上传 PyPI。
- GitHub Source ZIP 的 Windows launcher CRLF 热修复已发布。
- 下一阶段：中文菜单、一键安装环境、菜单精简。

Phase 1:

- [x] CRLF 保真处理

Phase 2:

- [x] 编码与换行保真阶段完成
- [x] 第一批编码契约测试完成
- [x] 第二批编码与 CRLF 端到端测试完成
- 本阶段未修改生产代码。

Next stage:

- v1.6.1 正式发布文档已完成，尚未发布，等待 main 合并、标签和 GitHub Release。根因是 Git blob 与 GitHub Source ZIP 中的 `.bat`
  使用 LF，而本地 `core.autocrlf=true` 掩盖了该问题；热修复增加 `.gitattributes`、菜单
  空输入/EOF 控制流保护和启动失败保护。
- develop 热修复合并提交为 `9d0e599a22f7ded316ac5180cf8ef856b8716cce`；launcher
  针对性测试为 18 passed，热修复完整基线为 277 passed，develop CI #22 六项全绿。
- release/v1.6.1 候选 CI 六项已全部通过；中文菜单、一键安装和菜单精简保留为后续独立工作。
- 下一步：将 release/v1.6.1 合并到 main，验证后创建 v1.6.1 标签和 GitHub Release。
- [x] v1.6.0 已于 2026-07-23 正式发布；标签 `v1.6.0` 指向 main 发布提交
  `ffcc8d39b53d4164dec74f4e160d67315c1421c0`，GitHub Release 已创建并设为 latest。
- 五个 v1.6.0 开发阶段已完成：`--selection-file`、串行 manifest/resume、workers 并发与
  API 协调、retry-failed 子批次、Windows 菜单接入。
- 发布验证为 273 passed，Windows/Linux 多 Python 版本和构建 CI 全部通过；未上传 PyPI。
- 下一阶段：收集真实批量处理反馈；后续评估 workers 设置持久化、批次历史浏览和更完善的
  跨平台交互。这些后续功能尚未实现。

- [x] v1.5.0 已于 2026-07-22 正式发布
- 软件版本以 `pyproject.toml` 为权威来源，已从此前基线 `1.4.1` 向前递增至
  `1.5.0`；原 v0.3.0 发布规划已纠正。Phase 1、Phase 2 是开发阶段编号，不是软件版本号。
- 正式标签 `v1.5.0` 指向提交 `22644c6a00c494325b066c7105afb8b5b512f399`；
  GitHub Release 已创建并设为 latest。
- 发布验证为 139 passed，Windows、Ubuntu 和多 Python 版本 CI 全部通过；未上传 PyPI。
- v1.6.0 第一阶段已完成：新增受验证的 `--selection-file` JSON 清单，支持按清单首次
  出现顺序串行处理 `input/` 内的 Markdown 文件；未提供清单时保持原有递归扫描、排序和
  `--max-files` 行为不变。
- v1.6.0 第二阶段已完成：非 dry-run 串行任务使用
  `logs/batches/<batch_id>.json` 保存独立 batch manifest，并通过
  `logs/batches/latest.json` 引用最近创建或恢复的批次。`--resume-batch` 只处理
  `pending` 和 `interrupted`，暂不自动重试 `failed`。
- v1.6.0 第三阶段已完成：新增 `--workers 1-5`（默认 1）；`workers=1` 继续使用原有
  串行路径并保留暂停和连续 5 次失败停止规则，`workers>1` 使用有界文件级
  `ThreadPoolExecutor`，单文件内 chunks 仍顺序处理且不启用跨文件失败熔断。
- 并发处理由主线程维护 manifest 状态和进度；stop 后不再提交新文件，并尝试取消尚未
  运行的 future。共享 `ApiClient` 限制活跃请求数，协调 429 冷却；JSONL 追加受进程内锁
  保护，并在 worker 开始和 assembly 正式发布前检查源文件 SHA-256。
- v1.6.0 第四阶段已完成：新增 `--retry-failed [BATCH_ID]`，可重试 latest 或指定父批次
  中的 `failed` 文件，并创建独立子批次保留父批次历史；支持当前 `workers` 和
  `--max-files`，未调度项可继续通过 `--resume-batch` 处理。
- v1.6.0 第五阶段已完成：Windows 一键菜单已接入处理全部文件、`input/` 内多文件和
  子目录选择、会话级 workers 1-5、resume latest、retry failed latest、只读 batch
  status，以及 input/output/logs 工作目录入口。选择范围仍仅限 `input/` 及其子目录，
  PowerShell 只生成 selection JSON，Python 层继续执行最终路径安全校验。
- v1.6.0 正式发布历史已同步到 `main` 和 `develop`；`release/v1.6.0` 与
  `feature/batch-concurrency` 均保留。

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
- 2026-07-22: v1.5.0 正式发布；标签 `v1.5.0` 指向正式提交
  `22644c6a00c494325b066c7105afb8b5b512f399`，GitHub Release 已创建并设为 latest；
  139 项测试与 Windows/Ubuntu 多 Python 版本 CI 全部通过，未上传 PyPI。
- 2026-07-22: v1.6.0 第一阶段完成受验证的 selection-file 串行选择处理；清单仅允许
  `input/` 内的 Markdown 相对路径，并保留首次出现顺序和既有单文件失败隔离。文件级并发、
  批次 manifest、resume/retry-failed 和一键菜单尚未实现。
- 2026-07-22: v1.6.0 第二阶段完成串行 batch manifest、latest 指针和
  `--resume-batch`；恢复仅调度 `pending`/`interrupted`，保留 failed 历史和
  `workers=1` 的既有连续失败停止规则。文件并发、`--retry-failed` 和菜单尚未实现。
- 2026-07-22: v1.6.0 第三阶段完成 `--workers 1-5` 和有界文件级线程池调度；保留
  `workers=1` 串行兼容行为和单文件 chunk 顺序，新增 stop/cancel 状态协调、共享 API
  请求上限与 429 冷却、并发 JSONL 写入保护和源文件双重 hash 发布保护。
- 2026-07-22: v1.6.0 第四阶段完成 `--retry-failed [BATCH_ID]`；仅按父批次原始顺序
  选择 `failed` 文件，创建带 `parent_batch_id` 的独立子批次，支持 latest、显式 batch
  ID、`workers`、`--max-files`、stop 和后续 resume，且不修改父批次历史。
- 2026-07-23: v1.6.0 第五阶段完成 Windows 一键菜单批次控制；保留原菜单 1-7 编号，
  新增多文件/子目录选择、workers、resume、retry、batch status 和 logs 目录入口。
  selection JSON 原子写入 `logs/selections/` 并保留审计，Python 安全边界保持不变。

## Known Issues

1. [已解决] 文本读取曾通过通用换行模式把 CRLF 转换为 LF；`read_text()` 现使用
   `newline=""` 保留原始换行符。

## Test Result

- `pytest -q`: 273 passed
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

v1.6.0 第一阶段新增覆盖：

- UTF-8 与 UTF-8 BOM selection JSON、schema 校验和相对 `--base-dir` 解析
- 清单顺序、规范化绝对路径去重、子目录同名文件和既有目录扫描排序
- input 越界、绝对/UNC/驱动器路径、符号链接、非 Markdown、cleaned 文件和损坏清单拒绝
- 缺失选中文件的单文件规划失败隔离、空清单 no-op、`--max-files` 与 dry-run 契约

v1.6.0 第二阶段新增覆盖：

- batch manifest/latest 的 schema、原子写入、路径约束、历史保护和损坏输入错误
- pending/running/succeeded/failed/skipped/interrupted 状态机及 counts 重算
- 串行成功、缓存跳过、单文件失败隔离、停止和异常退出后的 interrupted 恢复
- `--max-files` 未处理项保留、latest/显式 batch 恢复、当前源文件重新规划和 hash 检查
- dry-run/空 selection 不创建批次状态，以及错误字段的限长、换行压平和敏感信息脱敏

v1.6.0 第三阶段新增覆盖：

- `--workers` 默认值、1-5 边界，以及 dry-run/文件间暂停冲突校验
- `workers=1` 的顺序、暂停、退出码和连续 5 文件失败停止兼容行为
- 文件级实际并发、有界 future、失败隔离、无并发文件熔断及输入顺序 manifest
- final 缓存跳过、stop 后停止提交、future 取消回 pending 和 interrupted/counts 状态
- 共享 `ApiClient` 请求并发上限、429 冷却、既有请求重试和客户端关闭时机
- 多线程 JSONL 完整性，以及 worker 开始和 assembly 发布前的源文件 SHA-256 检查

v1.6.0 第四阶段新增覆盖：

- `--retry-failed` 的 latest/显式 batch ID 解析、互斥规则及 workers/max-files 组合
- failed-only 原序选择、重复路径去重、独立子批次初始状态和父 manifest 字节保护
- 空重试 no-op、重新规划和 source hash、缓存跳过、失败隔离、并发上限、stop 与 resume
- 缺失源文件、损坏 latest/manifest，以及越界、绝对、UNC、驱动器、符号链接和非 Markdown
  历史路径的拒绝

v1.6.0 第五阶段新增覆盖：

- `--batch-status` 模式互斥、无 latest 成功返回、只读摘要和损坏 latest/manifest 错误
- 状态输出字段完整且不泄露错误正文、prompt、源文件或敏感配置
- Unicode、空格和 POSIX selection 路径消费，以及菜单/PowerShell 脚本的静态安全契约
- Windows 菜单实际启动冒烟与 PowerShell 语法解析；Linux CI 不运行图形对话框

## Non-blocking Follow-ups

- 增加 Linux CI 实际运行
- 在稳定环境中补充 `PermissionError` 测试
- UTF-16 或自动编码探测支持需要单独设计
- cleaned 输出继续遵循 UTF-8 和现有组装契约
