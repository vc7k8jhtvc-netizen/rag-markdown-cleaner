# RAG Markdown Cleaner

一个面向「中级注册安全工程师 AI 学习知识库」的 Markdown 批量清洗工具。

网上的教材、法规、标准、讲义和真题,经过 PDF/OCR 转换后往往夹杂着广告、引流、重复页眉页脚、页码和各种版式噪声。这个工具帮你把这些垃圾清掉,同时尽量守住正文、数字、单位、法条、题目、答案和解析不被误删。

它的定位不是"随手写的清洗脚本",而是一个考虑了安全、数据完整性、并发和断点恢复的批处理工具。下面会把这些特性一条条讲清楚。

## 它能做什么

- 扫描 `input/` 下的 Markdown,支持子目录
- 调用 OpenAI 兼容接口清洗内容,去掉广告、引流、重复页眉页脚和 OCR 噪声
- 修复标题、段落、列表和表格结构
- 按字符上限自动分片,遇到超长代码块或表格宁可报错也不静默切坏
- 支持暂停、安全停止和断点续跑
- 用文件指纹避免重复调用 API,省 token
- 自动做质量检查:正文过度删除、异常扩写、数字丢失、新增网址等
- 生成分片结果和合并后的完整文档,完整文件丢了也能重新合并而不用重跑

## 核心特性详解

这几点是这个工具区别于普通清洗脚本的地方,值得单独说说。

### 安全优先

处理教材和调用付费 API,安全上没敢马虎:

- 密钥脱敏 —— 日志和错误信息里出现的 `Bearer xxx`、`api_key=xxx`、`sk-xxx` 会被自动打码,不会明文落盘。
- 路径穿越防护 —— 所有输入输出路径都会解析后校验是否越界,`input/` 里的符号链接和目录联接指不到项目外面去。
- 拒绝危险 URL —— `OPENAI_BASE_URL` 必须是 http/https,不允许带用户名密码、查询参数或片段,从源头杜绝把密钥写进 URL、进而漏进日志。
- 跳过符号链接 —— 扫描和读取阶段都主动跳过符号链接,避免被诱导读取任意文件。

### 数据不会写坏

所有输出文件都用原子写入:先写临时文件、`fsync` 落盘、再 `replace` 到目标名。这意味着即使程序在写一半时崩溃或断电,你也不会得到一个损坏的半截文件——要么是完整的旧版本,要么是完整的新版本。写文件和写 metadata 任何一步失败,都会把这次的半成品清理掉。

### 断点续跑,不重复烧 token

每个分片是否算"已完成",由一组 SHA-256 指纹共同决定:源文件、分片内容、提示词、模型名、Base URL、分片编号、输出文件哈希。任何一项变了,只有对应分片重跑,其余全部跳过。

这带来两个实际好处:

- 中途停了、断网了、程序挂了,再跑一次自动从断点接着来。
- 完整合并文件不小心删了或改坏了,程序会跳过所有有效分片、只重新合并,一次 API 都不用调。

### 只此一个实例在跑

内置文件锁,防止你手滑开了两个窗口同时清洗同一批文件把结果搅乱:

- 用 `O_CREAT|O_EXCL` 原子创建锁,天然防并发竞态。
- 锁里记了 PID,启动时检测持锁进程是否还活着;活着就拒绝启动,死了就自动清理失效锁。
- 内容损坏的锁默认不删,需要你确认没有其他实例后用 `--force-unlock` 才清理。
- 释放锁时校验 PID 归属,绝不误删别的进程的锁。

### 聪明的重试

网络和 API 都不可靠,重试逻辑做了区分:

- 该重试的才重试 —— 网络错误、超时、429、5xx、可识别的限流/容量错误、SSE 中途断流。
- 不该重试的绝不硬刚 —— 400 请求错误、401 密钥错误、403 权限、404 地址错误、模型正常返回但质检不合格。
- 指数退避 + 随机抖动,避免多任务同时重试打爆接口;接口返回 `Retry-After` 时优先听它的。
- 流式请求中途断了,已经收到的部分内容会存成 `*.partial.md`,方便你排查。

### 分片宁缺毋滥

按字符上限自动分片时,会尽量保持 Markdown 结构完整:优先在句末、空行、结构块边界切分,代码围栏内的空行不会误伤。碰到单个超过上限的代码块或表格,程序直接报错而不是硬切——因为切坏一个表格头或代码围栏,损失比停下来大得多。

### 全流程质量把关

每个分片和最终合并文件都会过一遍质量检查(详见下方「质量检查」小节),严重异常直接判失败、不落盘;轻微问题标记 `review_required` 提示你人工复核。质检结果全部写进 metadata,可追溯。

## 适合处理哪些内容

中级注册安全工程师相关的教材、法规、标准和真题,包括:

- 安全生产法律法规 / 管理 / 技术基础 / 专业实务教材
- 法律、行政法规、部门规章
- 国家标准和行业标准
- 历年真题、答案及解析
- 各类 PDF/OCR 转换后的 Markdown

## 开始之前

你需要:

- Windows 10/11
- Python 3.10 或更高
- 一个 OpenAI Chat Completions 兼容接口,支持 `POST /chat/completions` 和 `stream=true`

## 安装

克隆并进入项目:

```powershell
git clone https://github.com/vc7k8jhtvc-netizen/rag-markdown-cleaner.git
cd rag-markdown-cleaner
```

建一个虚拟环境并激活:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

如果 PowerShell 拦住了脚本,先放行再激活:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

安装项目:

```powershell
python -m pip install --upgrade pip
python -m pip install -e .
```

验证一下:

```powershell
python -m clean_auto --help
```

## 配置

复制配置模板:

```powershell
Copy-Item .env.example .env
```

打开 `.env`,填上你自己的信息:

```dotenv
OPENAI_API_KEY=你的API密钥
OPENAI_BASE_URL=https://你的接口地址/v1
OPENAI_MODEL=你的模型名称
```

关于密钥,记住几条:

- 别把真实 Key 写进源代码
- 别提交或公开 `.env`
- 别把 Key 放进 URL 查询参数
- 一旦泄露,立刻撤销并更换

## 怎么用

### 方式一:Windows 菜单

双击 `一键菜单.bat`,里面有:

```text
[1] Dry-run 预览
[2] 试跑 1 个文件
[3] 处理全部文件
[4] 打开 input
[5] 打开 output
[6] 打开日志
[7] 重置暂停和停止标记
[0] 退出
```

### 方式二:命令行

先把待处理的 `.md` 放进 `input/`,然后:

预览任务,不花钱不调 API:

```powershell
python -m clean_auto --dry-run
```

先拿一个文件试水:

```powershell
python -m clean_auto --yes --max-files 1
```

想顺便严格校验第一片的 YAML Front Matter:

```powershell
python -m clean_auto --yes --max-files 1 --strict
```

确认没问题后,处理全部:

```powershell
python -m clean_auto --yes --strict
```

需要的话可以指定单片最大字符数:

```powershell
python -m clean_auto --yes --max-chars 50000
```

正式导入知识库前,建议加上 `--strict`。

## 命令行参数

常用的几个:

- `--dry-run` —— 只生成处理计划,不调 API,用来预览会切成几片、花多少
- `--yes` / `--no-confirm` —— 跳过启动确认
- `--strict` / `--no-strict` —— 是否强制第一片包含完整 YAML Front Matter
- `--max-files N` —— 本次最多处理几个文件,0 表示不限
- `--max-chars N` —— 单片最大字符数
- `--max-file-size N` —— 单个输入文件最大字节数
- `--pause-between-files` / `--pause-between-chunks` —— 文件间 / 分片间暂停秒数
- `--pause-after-files N` —— 每处理 N 个文件后等你按 Enter
- `--force-unlock` —— 确认没有其他实例时,清理失效锁
- `--base-dir` —— 指定项目根目录,默认当前目录或环境变量 `RAG_CLEANER_HOME`

## 推荐的工作流程

别一上来就全量跑。稳妥的顺序是:

1. 把 Markdown 放进 `input/`
2. 先 dry-run 看看计划
3. 试跑一个文件
4. 检查完整清洗结果和 metadata
5. 确认广告删干净了、正文没缺
6. 小批量跑
7. 最后再全量
8. 只把人工检查过的结果导入知识库

对应的命令:

```powershell
python -m clean_auto --dry-run
python -m clean_auto --yes --max-files 1 --strict
python -m clean_auto --yes --strict
```

## 暂停和停止

跑到一半想歇一下:双击 `暂停.bat`,想继续就 `继续.bat`。

想安全停下来:双击 `停止.bat`。等程序完全停住后,在「一键菜单」里选 `[7]` 重置标记。

一个提醒:程序还没接收到停止请求时,别手动去删 `stop.flag`。

## 输出长什么样

每个输入文件会有独立的输出目录:

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

各是什么:

- `*_part_XXX_cleaned.md` —— 模型清洗的单个分片
- `*.meta.json` —— 分片指纹、质量检查和处理状态
- `*_cleaned.md` —— 全部分片成功后合并的完整文档
- `*.partial.md` —— 流式请求中途断了时保存的半成品

完整文件丢了或者 metadata 坏了,再跑一次程序会跳过有效分片、只重新合并,不会重复烧 token。

## 质量检查

程序会自动盯着这些点:

- 输出是不是空的
- 有没有严重截断
- 有没有异常扩写
- 标题、题目、数字是不是大量减少
- 表格有没有明显丢失
- 有没有新增原文里没有的网址
- 有没有残留的广告或引流文字
- YAML Front Matter 完不完整

metadata 里:

```json
"review_required": false
```

表示程序没发现明显风险;

```json
"review_required": true
```

表示结果已经存好了,但建议你再人工看一眼。

需要强调的是:自动检查过不代表模型输出百分百正确。正式入库前,这些还是得抽查:

- 法规名称和条款
- 标准编号
- 年份、数字和单位
- 题目、选项、答案和解析
- 表格
- 出版社、作者和发布机关
- 广告有没有漏删,正文有没有误删

## 遇到超长代码块或表格

当单个代码块或表格超过 `--max-chars` 时,程序不会硬切,而是直接报错,避免把结构切坏。

可以适当调大上限:

```powershell
python -m clean_auto --dry-run --max-chars 50000
```

如果还是超,建议手动把源文件里那个超大的代码块或表格拆一下。

## 断点续跑的原理

下面这些一起决定一个分片算不算「已完成」:

- 源文件 SHA-256
- 分片 SHA-256
- 提示词 SHA-256
- 模型名称
- API Base URL
- 分片编号和总数
- 输出文件 SHA-256

所以只要下面任何一项变了,对应分片就会重跑:

- 源文件内容
- `prompt.md`
- 模型名称
- API Base URL
- 分片算法或分片数量

## 跑测试

装开发依赖:

```powershell
python -m pip install -r requirements-dev.txt
```

跑:

```powershell
python -m pytest -q
```

目前覆盖:普通文本分片、内容保留、字符上限、超长代码块/表格保护,以及完整文件缺失、被改、metadata 丢失后的恢复。

## 数据与隐私

这些默认已经被 `.gitignore` 排除:

```text
.env
.venv/
input/
output/
logs/
review/
.clean_auto.lock
pause.flag
stop.flag
*.partial.md
```

即便如此,公开或提交前还是手动确认一遍,别混进去:

- API Key 或 Token
- 真实教材、清洗结果、运行日志
- 本机绝对路径
- 受版权保护的内容

本仓库不含任何教材、PDF、清洗结果或 API 凭据。

## 使用限制

大模型可能误删、漏删、改写或凭空生成内容。这个工具提供了质量检测和断点恢复,但它代替不了人工复核。

另外,请确保你对输入文档有合法的使用、处理和存储权限,遵守教材版权、API 服务条款和适用法律。
