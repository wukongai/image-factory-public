# Image Factory Public · 商品海报图片工厂

从产品资料到可上传的商品图片：方形主图、竖版详情图、营销海报、参考图改版，支持成套编译与统一风格。

**这是商品海报发行版，不包含私人 IP 角色、API Key、个人存储服务或作者的完整内部工作台。** 代码负责规划与编译；真正出图由你的 Agent 内置生图工具，或你已配置的生图 API/MCP 执行。

## 安装

```bash
npx skills add wukongai/image-factory-public --skill image-factory
```

这条命令下载本仓库 Skill，按提示选择 Agent 和安装范围。需要 Node.js/npx。Agent 运行脚本还需要 Python 3.10+ 与 Pillow，可让 Agent 按手册在虚拟环境安装 requirements.txt。

若已经有同名 `image-factory` 私有安装，请选择另一个测试项目，不要直接覆盖。仓库根 SKILL.md 与 src、scripts、templates 是一个完整安装单元，不能只复制 SKILL.md。

## 使用

```text
使用 image-factory，给这个产品制作一张方形主图和两张详情海报。
产品资料：[资料文件]
图片要求：[后台截图或尺寸要求]
风格：暖白底、深蓝文字、橙色点缀，整套一致。
输出目录：[产品目录下的素材文件夹]
请实际生成图片，逐张检查；没有生图工具时先明确告诉我。
```

先确认文字与样式，生成首图并检查，再用同一套设计规则做后续图片。收到真实文件后，执行 accept_image.py，生成图片路径、尺寸、哈希和审核状态回执。

- [完整使用手册](docs/使用手册.md)
- [与小店 Skill 连接](references/store-listing.md)
- [小店上架 Skill](https://github.com/wukongai/store-listing-kit)
- [验证说明](docs/VERIFICATION.md)
- [隐私与安全](SECURITY.md)

## 新增营销风格

四套营销风格各配方形主图和竖版海报：**黑底赛博大字、黑白大字冲击、橙色活力转化、奶油质感高级**。每套分别定义标题层级、主视觉和转化区域，共 8 个新增模板，加上原有模板合计 12 个。

[查看风格和模板选择表](docs/营销模板选择.md)。直接告诉 Agent “用黑底赛博大字风做一套商品图”即可。

## 包含什么

| 模板 | 默认画幅 | 用途 |
|---|---|---|
| product-main-v1 | 1024×1024 | 方形商品主图 |
| product-detail-v1 | 1024×1536 | 竖版详情页单张海报 |
| marketing-poster-conversion-v1 | 1024×1536 | 营销转化海报 |
| poster-reference-remix-v1 | 1024×1536 | 根据授权参考图提炼布局后改版 |

实际后台要求优先，默认尺寸不是平台官方要求。模板是可修改 JSON；批量示例在 examples/product-set.json。每张图只承载清楚的核心信息，长详情通过多张图接续。

## 本地验证

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/check_public_release.py
```

测试不调用付费 API。编译成功不代表图片已经生成，ready 回执只在真实文件检查与视觉审核后产生。程序不登录电商后台，也不会发布商品。

MIT License。通用编译模块继承自 Image Factory，商品海报发行版独立维护。

## 真实生成示例

以下为通用演示素材，由宿主生图工具生成，并已交给小店 HTML 清单验证。

![方形商品主图](examples/rendered/product-main.png)

![竖版详情图](examples/rendered/product-detail.png)

### 黑底赛博大字示例

![赛博营销海报](examples/rendered/cyber-bold-poster.png)
