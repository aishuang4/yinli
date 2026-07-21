# 高级工作流

## 依赖选择

基础脚本只使用 Python 3 标准库。复杂 Word 功能可优先使用以下成熟组件：

- `python-docx`：段落、样式、表格、页眉页脚和图片。
- `lxml`：批注、修订、域、目录等 OOXML 精确修改。
- LibreOffice：DOCX 转 PDF，用于视觉验证。
- Poppler `pdftoppm`：PDF 转逐页 PNG。

处理中文文档时，headless LibreOffice 环境还必须提供可发现的 CJK 字体，例如 Noto Sans CJK、思源黑体或系统授权字体。宿主机安装了字体并不等于容器或隔离运行时能够读取；若渲染出现方框，应先修复运行时字体配置，不能把它判断为文档内容丢失。

使用前先通过通用 Shell 检查依赖，不要假设命令或包已安装：

```bash
python3 -c "import docx; print(docx.__version__)"
command -v soffice
command -v pdftoppm
```

缺少 Python 包时应向用户说明依赖，不要静默联网安装。桌面应用应在受控 Artifact Runtime 中预装这些依赖。

## 模板继承

需要沿用模板时，以原 DOCX 为基础修改，不要从零重建。保留以下部件：

- `word/styles.xml`
- `word/numbering.xml`
- `word/theme/`
- 页眉、页脚及其关系文件
- 页面尺寸、页边距和分节设置

先用 `scripts/inspect_docx.py` 记录结构，再完成修改和渲染对比。

## 批注与修订

批注和修订需要同时更新多个 OOXML 部件及 relationship/content type。只有在具备结构测试和渲染验证时才直接修改；否则使用成熟库或 Word 自动化接口。

## Windows Office 自动化

若任务要求控制已打开的 Word 桌面窗口、刷新目录、运行域或操作受保护文档，应使用独立的 Windows Office 自动化工具层，例如受权限控制的 PowerShell/COM 工具。文件型 Skill 不应自行获得无边界的桌面控制权限。
