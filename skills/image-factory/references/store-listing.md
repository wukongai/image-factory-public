# 与 store-listing-kit 交接

由 Agent 编排两个 Skill。小店 Skill 给出产品事实、后台图片槽位、确认文案、品牌风格及产品内输出路径。图片工厂输出真实 PNG 和 image-delivery/v1 receipt，不自行更改小店价格、权益、库存或商品状态。

每张 receipt 包含 id、image_path、dimensions、sha256、status。status=ready 必须通过像素检查与人工/Agent 视觉检查；awaiting_visual_review 不能列为 ready。小店 Skill 将 image_path 转成当前上架素材内相对路径，加入 upload-checklist.json 的 steps[].files，再运行其 render_upload_checklist.py；不要只在清单中写预计文件名。

例：04-商品展示与介绍/01-商品主图.png 与 04-商品展示与介绍/02-详情图.png。文件放在调用方已确认的产品目录；不得把 Skill 安装目录当成商品素材包。失败只记录缺项，不伪造图片或 ready 状态。
