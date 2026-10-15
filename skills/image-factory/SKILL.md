---
name: image-factory
description: 为商品制作方形主图、竖版详情图、营销海报及成套一致配图。支持参考图改版，接收 store-listing-kit 的图像任务并交回真实图片；不用于自动上架、私人 IP 配图、账号配置或视频制作。
---

# 图片工厂公开版

## 输入与边界
读取 [使用手册](docs/使用手册.md) 与 [小店交接合同](references/store-listing.md)。输入为用户确认的商品资料、图片任务、尺寸、统一风格和可用的授权参考图。图片文字只使用用户确认的产品事实；价格、资质、评价、权益不明时追问，不编造。

安装目录是本 SKILL.md 所在目录，所有 scripts、templates、src 都在其下。通过定位本文件取得绝对路径，不依赖作者电脑或私有仓库。运行需要 Python 3.10+ 和 Pillow；缺依赖时在用户确认的位置建立虚拟环境，安装 requirements.txt，不修改系统 Python。

## 执行流程
1. 按后台要求建立图片任务：每张有 id、用途、标题/副标题/卖点、尺寸和输出文件位置；同一套共用配色、字形气质、背景与产品外观。无明确要求时先用方主图＋竖版详情图，不能称为平台强制规范。
2. 选择模板：`product-main-v1`（1:1）、`product-detail-v1`（2:3）、`marketing-poster-conversion-v1`（营销）、`poster-reference-remix-v1`（参考改版）。参考图由当前 Agent 看图，提炼布局规则到 reference-analysis.json；品牌、人物、商品包装只有用户授权才可使用。
3. 用 `python3 <安装目录>/scripts/generate_image_asset.py --brief <brief.json> --template <模板ID> --output-dir <产品内制作记录目录>` 编译。成套任务使用 `generate_image_batch.py --batch <batch.json> --output-dir <制作记录目录>`；示例见 examples/product-set.json。编译只产生提示词和 render_handoff，不等于图片完成。
4. 当前 Agent 使用宿主生图工具执行 manifest 中的 render_handoff：先读取 prompt_file，按 assets 加载授权参考图。首图通过用户检查后，可作为同套后续图的风格参考；保留每张真实商品事实。用户已有 API/MCP 生图工具时也可接入，不要求安装作者私有 Router。API Key 在用户自己的工具配置中，不写进 brief、图片、仓库或对话；未授权付费调用时不得使用收费通道。没有可用生图工具则交付编译产物并明确 blocked，不能假报已出图。
5. 工具生成后用 `scripts/accept_image.py --manifest <manifest> --image <生成图片> --destination <产品输出目录/图片.png>` 接收。它检查文件完整性、画幅与最低分辨率，同画幅更高分辨率原图可保留；后台要求精确像素时加 `--exact-size`。不拉伸或裁切图片；尺寸不符则重生。命令复制最终文件并写回图片路径与哈希。逐张展示 PNG，检查中文字、内容、边缘、外观及成套一致性。只有视觉检查通过才执行同一命令加 `--visual-approved`（已有同哈希文件允许复核；不同文件不覆盖）。
6. 将 receipt 中的 image_path、dimensions、sha256、status 交回小店 Skill。完成条件为真实 PNG 存在、图像检查通过、视觉审核通过，并被小店 HTML 清单引用。失败保留可用产物与具体失败项；同一图片不得在未读取已有状态时重复付费生成。

## 模板与扩展
模板是 JSON，用户可复制后调整配色、构图、文字预算与尺寸，再以 `--template <模板文件>` 调用。支持 template 参数覆盖与批量公共 defaults。公开版采用图文一次生成，不启用自适应混排/无字后合成分支。画幅预设只调整尺寸。只改比例不等于完成排版适配；请同时调整 layout 与 constraints。通用版不包含任何作者人物、照片、默认品牌、云存储或私人服务。

## 验证与副作用
脚本只写用户指定的本地文件，不上传、不改商品、不发送消息。编译输出目录必须为空，重做时建立新版本目录；已有不同图片文件拒绝覆盖。小店交接由调用方维护清单，不让图片工厂越权修改交易信息。
自测：`python3 -m unittest discover -s tests -p 'test_*.py'`。发布扫描：`python3 scripts/check_public_release.py`。完整交接示例见 docs/使用手册.md；测试的合成图不代表真实生图成功。
