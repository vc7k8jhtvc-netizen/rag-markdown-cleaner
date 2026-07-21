# RAG Markdown Cleaner



面向**中级注册安全工程师 AI 学习知识库**的 Markdown 批量清洗工具。



用于处理网上 PDF/OCR 转换后的教材、法律法规、标准规范、讲义和真题 Markdown，清理其中的广告、引流内容、重复页眉页脚、页码和版式噪声，并尽量保护教材正文、数字、单位、法条、题目、答案和解析。



## 主要功能



- 扫描 `input/` 下的 Markdown 文件，支持子目录

- 调用 OpenAI Chat Completions 兼容接口清洗内容

- 清理明确的广告、销售和引流信息

- 清理重复页眉、页脚、页码及 OCR 版式噪声

- 修复 Markdown 标题、段落、列表和表格结构

- 按字符上限自动分片

- 保护超长代码块和 Markdown 表格，避免静默切坏

- 支持暂停、安全停止和断点续跑

- 使用文件指纹避免重复调用 API

- 校验 YAML Front Matter

- 检测正文过度删除、异常扩写、数字丢失和新增网址

- 生成分片结果和完整合并文件

- 完整文件丢失或损坏后可重新合并，无需重复调用 API

- 保存 JSON metadata 和 JSONL 运行日志



## 适用范围



项目主要面向以下内容：



- 中级注册安全工程师教材

- 安全生产法律法规

- 安全生产管理

- 安全生产技术基础

- 安全生产专业实务

- 法律、行政法规、部门规章

- 国家标准和行业标准

- 历年真题、答案及解析

- PDF/OCR 转换后的 Markdown 文档



## 环境要求



- Windows 10/11

- Python 3.10 或更高版本

- OpenAI Chat Completions 兼容接口

- 接口需要支持：



```text

POST /chat/completions

stream=true

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



激活虚拟环境：



```powershell

.\\.venv\\Scripts\\Activate.ps1

```



如果 PowerShell 阻止脚本运行：



```powershell

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

.\\.venv\\Scripts\\Activate.ps1

```



安装项目：



```powershell

python -m pip install --upgrade pip

python -m pip install -e .

```



验证：



```powershell

python -m clean\_auto --help

```



## 配置



复制配置模板：



```powershell

Copy-Item .env.example .env

```



打开 `.env` 并填写自己的配置：



```dotenv

OPENAI\_API\_KEY=你的API密钥

OPENAI\_BASE\_URL=https://你的接口地址/v1

OPENAI\_MODEL=你的模型名称

```



注意：



- 不要把真实 API Key 写入源代码

- 不要提交或公开 `.env`

- 不要把 API Key 放在 URL 查询参数中

- 已经公开过的 API Key 必须立即撤销并更换



## 使用方法



### Windows 菜单



双击：



```text

一键菜单.bat

```



菜单提供：



```text

\[1] Dry-run 预览

\[2] 试跑 1 个文件

\[3] 处理全部文件

\[4] 打开 input

\[5] 打开 output

\[6] 打开日志

\[7] 重置暂停和停止标记

\[0] 退出

```



### 命令行



将待处理的 `.md` 文件放入：



```text

input/

```



预览任务，不调用 API：



```powershell

python -m clean\_auto --dry-run

```



试跑一个文件：



```powershell

python -m clean\_auto --yes --max-files 1

```



严格校验第一个分片的 YAML Front Matter：



```powershell

python -m clean\_auto --yes --max-files 1 --strict

```



处理所有待处理文件：



```powershell

python -m clean\_auto --yes --strict

```



指定单片最大字符数：



```powershell

python -m clean\_auto --yes --max-chars 50000

```



正式导入知识库前，建议使用 `--strict`。



## 推荐工作流程



1\. 将 Markdown 放入 `input/`

2\. 执行 dry-run

3\. 试跑一个文件

4\. 检查完整清洗文件和 metadata

5\. 检查广告是否删除、正文是否完整

6\. 小批量处理

7\. 最后执行全量清洗

8\. 仅将通过人工检查的结果导入知识库



示例：



```powershell

python -m clean\_auto --dry-run

python -m clean\_auto --yes --max-files 1 --strict

python -m clean\_auto --yes --strict

```



## 暂停和停止



请求暂停：



```text

暂停.bat

```



解除暂停：



```text

继续.bat

```



安全停止：



```text

停止.bat

```



程序完全停止后，在“一键菜单”中选择：



```text

\[7] Reset pause and stop flags

```



不要在程序尚未接收到停止请求时手动删除 `stop.flag`。



## 输出结构



每个输入文件会创建独立输出目录：



```text

output/

└── 教材名称\_路径哈希/

&#x20;   ├── 教材名称\_part\_001\_cleaned.md

&#x20;   ├── 教材名称\_part\_001\_cleaned.md.meta.json

&#x20;   ├── 教材名称\_part\_002\_cleaned.md

&#x20;   ├── 教材名称\_part\_002\_cleaned.md.meta.json

&#x20;   ├── 教材名称\_cleaned.md

&#x20;   └── 教材名称\_cleaned.md.meta.json

```



其中：



- `*\_part\_XXX\_cleaned.md`：模型清洗分片

- `*.meta.json`：分片指纹、质量检查和处理状态

- `*\_cleaned.md`：全部成功分片合并后的完整文档

- `*.partial.md`：流式请求异常中断时保存的部分响应



完整文件丢失或 metadata 损坏后，再次运行程序会跳过有效分片，只重新合并，不重复调用 API。



## 质量检查



程序会检查：



- 输出是否为空

- 是否发生严重截断

- 是否异常扩写

- 标题是否大量减少

- 题目是否大量减少

- 数字是否大量减少

- 表格是否明显丢失

- 是否新增原文不存在的网址

- 是否仍有疑似广告或引流文字

- YAML Front Matter 是否完整



metadata 中：



```json

"review\_required": false

```



表示程序没有发现明显风险。



```json

"review\_required": true

```



表示结果已经保存，但建议人工复核。



自动检查不能证明模型输出完全正确。正式导入知识库前仍应抽查：



- 法律法规名称和条款

- 标准编号

- 年份、数字和单位

- 题目、选项、答案和解析

- 表格

- 出版社、作者和发布机关

- 广告是否漏删

- 教材正文是否误删



## 超长代码块和表格



当单个围栏代码块或 Markdown 表格超过 `--max-chars` 时，程序会停止切分并报错，避免静默破坏结构。



可以适当增大限制：



```powershell

python -m clean\_auto --dry-run --max-chars 50000

```



如果仍然超限，建议人工拆分源文件中的超大代码块或表格。



## 断点续跑



以下内容共同决定分片是否已经完成：



- 源文件 SHA-256

- 分片 SHA-256

- 提示词 SHA-256

- 模型名称

- API Base URL

- 分片编号和总数

- 输出文件 SHA-256



以下内容变化时，对应分片会重新处理：



- 源文件内容

- `prompt.md`

- 模型名称

- API Base URL

- 分片算法或分片数量



## 测试



安装开发依赖：



```powershell

python -m pip install -r requirements-dev.txt

```



运行测试：



```powershell

python -m pytest -q

```



当前测试覆盖：



- 普通文本分片

- 内容保留

- 最大字符限制

- 超长代码块保护

- 超长表格保护

- 完整文件缺失后的恢复

- 完整文件被修改后的恢复

- metadata 缺失后的恢复



## 数据与隐私



以下内容默认被 `.gitignore` 排除：



```text

.env

.venv/

input/

output/

logs/

review/

.clean\_auto.lock

pause.flag

stop.flag

*.partial.md

```



公开或提交代码前仍应执行人工检查，确认没有：



- API Key

- Token

- 真实教材

- 清洗结果

- 运行日志

- 本机绝对路径

- 受版权保护的内容



本仓库不包含教材、PDF、清洗结果或 API 凭据。



## 使用限制



大模型可能误删、漏删、修改或生成内容。本工具提供质量检测和断点恢复，但不能代替人工复核。



请确保你对输入文档拥有合法的使用、处理和存储权限，并遵守教材版权、API 服务条款及适用法律。



