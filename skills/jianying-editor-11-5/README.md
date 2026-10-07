# Jianying Editor 11.5 · 自包含 Skill

本 skill 适配 Windows 剪映专业版 **11.5.0.14471**，使用本机原生 DraftIO 保存可编辑草稿。

- AI 入口：[SKILL.md](SKILL.md)
- 安装依赖：`python -m pip install -r requirements.txt`
- 在 `scripts` 目录运行：`python -X utf8 -m jyai.cli --help`
- 仓库中的完整说明：[使用文档](../../docs/使用文档.md)
- 上游历史说明：[README.upstream.md](README.upstream.md)

新版本兼容边界与已验证事实以 `SKILL.md` 为准。保留的上游资料可能描述 5.9、macOS 或尚未适配的导出功能，不代表当前原生适配版全部支持。

当前是文件式草稿生成/修改，不是实时 GUI 控制。目标草稿正在剪映编辑时，先返回首页，再让 AI 修改；想保留人工修改时必须重新读取最新草稿并增量处理。

保留上游作者 LICENSE 和第三方许可。本目录复制到别处仍能运行核心命令，外部仓库文档链接可能不再有效。
