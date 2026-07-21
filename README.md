# RAG Markdown Cleaner

面向**中级注册安全工程师 AI 学习知识库**的 PDF/OCR Markdown 教材清洗工具。

它用于清理网上教材经过 PDF/OCR 转换后产生的：

- 明确的广告、销售和引流内容；
- 重复页眉、页脚和页码；
- PDF 转换产生的版式噪声；
- 明显的 OCR 格式错误；
- 损坏的 Markdown 标题、段落、列表和表格结构。

程序会尽量保护：

- 教材正文；
- 安全生产法律法规；
- 标准规范和标准编号；
- 数字、年份、单位和公式；
- 真题、题目、选项、答案和解析；
- 作者、出版社、发布机关和来源信息。

> 完整的安装、配置、运行、暂停、停止和故障排查说明，请查看 [使用说明.md](使用说明.md)。

## 主要功能

- 扫描 `input/` 下的 Markdown 文件，支持子目录；
- 调用 OpenAI Chat Completions 兼容接口；
- 清理明确的广告、销售和引流信息；
- 清理重复页眉、页脚、页码和 OCR 版式噪声；
- 按字符上限自动分片；
- 保护超长代码块和 Markdown 表格；
- 支持 dry-run、单文件试跑和批量处理；
- 支持暂停、安全停止和断点续跑；
- 支持流式 SSE 响应、超时和临时错误重试；
- 支持模型上下文预算检查；
- 支持 YAML Front Matter 校验；
- 支持输出长度、标题、题目、数字、表格和 URL 检查；
- 高风险结果自动复制到 `review/`；
- 生成分片文件和完整合并文件；
- 完整文件损坏或丢失后可重新合并；
- 支持分片、完整文件和复核报告 metadata schema；
- 提供 GitHub Actions 自动测试。

## 适用内容

项目主要面向：

- 中级注册安全工程师教材；
- 安全生产法律法规；
- 安全生产管理；
- 安全生产技术基础；
- 安全生产专业实务；
- 法律、行政法规和部门规章；
- 国家标准和行业标准；
- 历年真题、答案和解析；
- PDF/OCR 转换后的 Markdown。

## 环境要求

- Windows 10/11；
- Python 3.10 或更高版本；
- OpenAI Chat Completions 兼容接口；
- 接口支持 `POST /chat/completions`；
- 接口支持流式响应。

项目 CI 已验证：

```text
Python 3.10
Python 3.12
Python 3.14
```

## 安装

克隆仓库：

```powershell
git clone https://github.com/vc7k8jhtvc-netizen/rag-markdown-cleaner.git
cd rag-markdown-cleaner
```

创建虚拟环境：

```powershell
python -m venv .venv
```

安装项目：

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

开发环境安装测试和 Ruff：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

验证安装：

```powershell
.\.venv\Scripts\python.exe -m clean_auto --help
```

查看版本：

```powershell
.\.venv\Scripts\python.exe -c "import clean_auto; print(clean_auto.__version__)"
```

## 配置

复制配置模板：

```powershell
Copy-Item .env.example .env
```

编辑：

```powershell
notepad .env
```

填写自己的 API 配置：

```dotenv
OPENAI_API_KEY=你的API密钥
OPENAI_BASE_URL=https://你的接口地址/v1
OPENAI_MODEL=你的模型名称
```

不要：

- 将 `.env` 上传到 GitHub；
- 将 API Key 写入 Python 代码；
- 将 API Key 放入 Base URL 查询参数；
- 使用已经泄露的旧 API Key。

## 可选模型预算

如果知道模型的上下文容量，可以配置：

```dotenv
OPENAI_CONTEXT_WINDOW=32768
OPENAI_MAX_OUTPUT_TOKENS=12000
OPENAI_TOKEN_PARAMETER=max_tokens
OPENAI_SAFETY_MARGIN_TOKENS=1024
```

如果接口使用 `max_completion_tokens`：

```dotenv
OPENAI_TOKEN_PARAMETER=max_completion_tokens
```

如果不确定模型能力，先使用：

```dotenv
OPENAI_CONTEXT_WINDOW=0
OPENAI_MAX_OUTPUT_TOKENS=0
OPENAI_TOKEN_PARAMETER=max_tokens
OPENAI_SAFETY_MARGIN_TOKENS=1024
```

## 可选质量阈值

默认配置：

```dotenv
QUALITY_SEVERE_MIN_RETAINED_RATIO=0.30
QUALITY_WARNING_MIN_RETAINED_RATIO=0.50
QUALITY_REVIEW_MIN_RETAINED_RATIO=0.70

QUALITY_SEVERE_MAX_EXPANSION_RATIO=2.00
QUALITY_WARNING_MAX_EXPANSION_RATIO=1.50

QUALITY_HEADING_RETAINED_RATIO=0.50
QUALITY_QUESTION_RETAINED_RATIO=0.70
QUALITY_NUMBER_RETAINED_RATIO=0.70
QUALITY_TABLE_RETAINED_RATIO=0.50
```

质量阈值在程序启动时加载一次。修改 `.env` 后需要重新启动程序。

## 推荐使用流程

将待清洗的 `.md` 文件放入：

```text
input/
```

先预览，不调用 API：

```powershell
.\.venv\Scripts\python.exe -m clean_auto --dry-run
```

试跑一个文件：

```powershell
.\.venv\Scripts\python.exe -m clean_auto --yes --max-files 1 --strict
```

确认结果后再处理全部文件：

```powershell
.\.venv\Scripts\python.exe -m clean_auto --yes --strict
```

也可以双击：

```text
一键菜单.bat
```

推荐流程：

```text
dry-run
  -> 试跑一个文件
  -> 检查 output/ 和 review/
  -> 小批量处理
  -> 全量处理
```

## 输出结构

```text
output/
└── 教材名称_路径哈希/
    ├── 教材名称_part_001_cleaned.md
    ├── 教材名称_part_001_cleaned.md.meta.json
    ├── 教材名称_part_002_cleaned.md
    ├── 教材名称_part_002_cleaned.md.meta.json
    ├── 教材名称_cleaned.md
    └── 教材名称_cleaned.md.meta.json
```

文件说明：

- `*_part_XXX_cleaned.md`：模型清洗后的分片；
- `*.meta.json`：分片指纹和质量检查结果；
- `*_cleaned.md`：所有成功分片合并后的完整文档；
- `*.partial.md`：流式请求中断时保存的部分响应。

## 人工复核

当完整文件 metadata 中出现：

```json
"review_required": true
```

程序会复制到：

```text
review/
```

示例：

```text
review/
└── 教材名称_路径哈希/
    ├── 教材名称_cleaned.md
    └── 教材名称_review.json
```

需要重点检查：

- 法律法规名称和条款；
- 标准编号；
- 年份、数字和单位；
- 题目、选项、答案和解析；
- 表格；
- 广告是否漏删；
- 教材正文是否误删；
- 模型是否新增原文没有的内容。

`review_required=false` 只表示程序没有发现明显风险，不能替代人工抽查。

## 暂停和停止

暂停：

```text
暂停.bat
```

继续：

```text
继续.bat
```

安全停止：

```text
停止.bat
```

也可以使用标记文件：

```powershell
New-Item .\pause.flag -ItemType File -Force
Remove-Item .\pause.flag -Force

New-Item .\stop.flag -ItemType File -Force
```

程序完全停止后，使用一键菜单中的：

```text
[7] Reset pause and stop flags
```

不要在任务尚未完全停止时手动删除 `stop.flag`。

## 断点续跑

程序根据以下指纹判断分片是否完成：

- 源文件 SHA-256；
- 分片 SHA-256；
- 提示词 SHA-256；
- 模型名称；
- API Base URL；
- 分片编号和总数；
- 输出文件 SHA-256。

以下内容发生变化时，对应分片可能重新处理：

- 源文件；
- `prompt.md`；
- 模型名称；
- API Base URL；
- `--max-chars`；
- 分片算法或分片数量。

如果完整文件丢失，但分片仍然有效，程序会跳过已完成分片，只重新合并完整文件。

## 测试和代码检查

运行测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

运行 Ruff：

```powershell
.\.venv\Scripts\python.exe -m ruff check clean_auto tests
```

GitHub Actions 会在推送和 Pull Request 时自动验证：

```text
Python 3.10
Python 3.12
Python 3.14
```

## 版本

当前版本：

```text
1.4.1
```

版本发布：

- `v1.2.0`：初始公开版本；
- `v1.3.0`：可靠性、质量检查和测试增强；
- `v1.4.0`：上下文预算、复核目录、metadata schema 和 CI；
- `v1.4.1`：使用说明和文档结构修复。

## 安全、版权和费用

不要公开：

- `.env`；
- API Key；
- 原始教材；
- 清洗结果；
- 运行日志；
- partial 响应。

API 调用可能产生费用。正式导入知识库前，请人工抽查清洗结果。

请确保你拥有输入文档的合法使用、处理和存储权限，并遵守教材版权、API 服务条款以及适用法律法规。

## 详细说明

完整操作手册请查看：

[使用说明.md](使用说明.md)
