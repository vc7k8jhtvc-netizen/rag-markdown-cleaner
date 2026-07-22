# Changelog

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
