# Public Poster Implementation Plan

Goal: 独立可安装海报公开版与小店素材交接。
Architecture: 复用编译模块与模板，以宿主执行真实生图，accept_image 验收交付。
Tech Stack: Python 3.10+, Pillow, Agent Skills。

- [x] 白名单导出通用模块，移除默认身份与私有发布；不复制 Git 历史。
- [x] 实现图像接收与版本冲突保护，新增方主图、详情模板。
- [x] tests/test_public.py 覆盖模板、批量、参考、尺寸和状态；scripts/check_public_release.py 检查私有标记与敏感文件。
- [x] 隔离安装副本验证，原版相同输入对比，不丢海报规则。
- [x] 真实生成方图与详情，视觉检查后交给小店 HTML 清单。
- [ ] 发布新 GitHub 仓库，更新双方安装链接与手册，远端安装复验。
