---
name: customs-report
description: Use for generating a professional customs (海关) big-data report (海关大数据报告) from the HandaaS customs MCP — covering 出口趋势、出口产品趋势(HS码)、外贸招聘画像、出口订单地区分布、海外认证. Trigger when users ask for “海关大数据报告”, “外贸大数据报告”, “出口分析”, “海关数据查询”, “外贸招聘画像”, “海外认证查询”, “出口订单地区分布”, or “企业外贸画像”. Infer the canonical enterprise name (auto-fuzzy-completing a keyword), pick the right MCP tools, and produce HTML + Markdown + JSON reports automatically.
---

# 海关大数据报告

## 用户契约

把“海关大数据报告”作为面向用户的调用短语。`customs-report` 仅为内部包名。

当本 skill 处于激活状态：

1. 不要向用户索要 product_id、MCP 工具名、API 字段、内部参数或凭证信息；只接受企业名称、统一社会信用代码、注册号或企业 ID。
2. 接受自然目标，例如“查一下某某公司的海关数据”“分析这家企业的外贸情况”“给我一份某某公司的出口报告”“看看这家企业出口到哪些国家”。
3. 当用户只给关键词时，自动调用关键词模糊查询补全企业全称，再查海关/外贸详情。
4. 优先使用 MCP 连接（`CUSTOMS_MCP_URL` Remote MCP 或本地 `handaas-mcp-server/customs-mcp-server`）；不要让用户处理签名或凭证。
5. 同时产出 HTML（可分享交付）、Markdown（知识库 / wiki）、JSON（系统集成）三类产物。
6. 报告正文必须是专业研究报告风格：只见海关/外贸事实与结构化数据，绝不出现工具名、入参、product_id、内部字段或空表。
7. 绝不打印 `secret_id`、`secret_key`、签名、token 或原始签名请求。
8. 默认 dry-run；真实付费 / 凭证调用需用户明确要求且 MCP 连接配置完整。
9. 数据为空时明确说明数据范围 / 口径，不渲染空表、不臆造事实。


- MCP 返回的嵌套 JSON 字符串（如金额 `{"coinType":"人民币","value":430000000.0}`、地址 `{"city":"杭州市",...}`）必须解析为可读文本（如"4.30 亿 人民币"、"浙江省杭州市"），绝不在报告正文、表格或指标中输出原始 JSON 字符串。
- 报告所有章节标题、指标卡标签必须用中文；`core_analysis.sections` 的 `title` 字段必须中文，不可显示英文 key（如 `holders`、`investments`）。
- 指标值必须可读化：金额格式为"X 亿/万 + 币种"，地址拼接省市区，比率显示百分号。详见 `references/report-output.md` 的「数据格式约束」。

## MCP 服务入口

- 上游 MCP 项目：`handaas-mcp-server/customs-mcp-server`（“海关大数据”）。
- Remote MCP：设置环境变量 `CUSTOMS_MCP_URL`（streamable-http），可选 `CUSTOMS_MCP_TOKEN`。
- 本地 MCP：设置 `HANDAAS_MCP_SERVER_ROOT` 指向 `handaas-mcp-server` 仓库根目录；该 server 自己的 `.env` 提供 `INTEGRATOR_ID` / `SECRET_ID` / `SECRET_KEY`。
- 首次真实查询前，运行 `scripts/mcp_client.py ping` 与 `scripts/mcp_client.py list-tools` 验证连通。

## 按需加载 references

- 不清楚该 MCP 有哪些工具、参数、返回字段、何时调用：`references/mcp-tools-reference.md`。
- 报告结构、章节、质量底线、渲染工作流：`references/report-output.md`。

## 意图路由

| 用户意图 | 内部工作流 |
| --- | --- |
| 查一家公司的全维度海关/外贸报告 | 调出口趋势 + 产品趋势 + 招聘画像 + 订单地区 + 海外认证组装全量报告；`compose_report.py --enterprise ...` |
| 只要出口趋势 / 产品趋势 | 仅调对应工具，按统一骨架组装 |
| 只要出口订单地区分布 | `customs_bigdata_export_order_regions`，可调分页 |
| 只要外贸招聘画像 / 海外认证 | 用对应工具，按 KV/表组装 |
| 只给关键词（不是全称） | 先 `customs_bigdata_fuzzy_search` 补全全称，再查详情 |
| 只要 JSON / 只要 HTML / 只要 Markdown | 用 `--output`（JSON）或 `--report-output`（HTML+MD），或 `render_report.py` 重渲染 |
| 连接 / 工具不存在 / 传参错误 | `mcp_client.py ping` / `list-tools` 排查；报脱敏后的缺失项 |

## Golden path for 海关大数据报告

1. **解析企业全称**：若输入含“公司/集团/有限/院/厂/中心/事务所/合作社/合伙”等后缀视为全称；否则调 `customs_bigdata_fuzzy_search` 取首个命中。
2. **调海关工具**：`customs_bigdata_export_trends`（出口趋势：年份/金额/订单）、`customs_bigdata_export_product_trends`（HS 码订单分布）、`customs_bigdata_export_recruitment_profile`（外贸招聘画像）、`customs_bigdata_export_order_regions`（订单地区分布）、`customs_bigdata_overseas_certifications`（海外认证）。入参为 `matchKeyword`（企业全称）+ `keywordType`。
3. **组装统一报告**：核心分析含出口趋势（表）、出口产品趋势（表）、外贸招聘画像（KV）、出口订单地区分布（表）、海外认证（表）。
4. **渲染三件套**：`compose_report.py --enterprise ... --output ... --report-output ...` 直接产出 JSON + HTML + Markdown；或 `render_report.py --input ... --output ...` 重渲染。
5. **返回路径**：返回 JSON、HTML、Markdown 文件路径，以及企业全称映射与数据口径。

## 脚本速查

```bash
# 校验连接配置（脱敏）
python scripts/validate_config.py --allow-placeholders

# 连通性自测
python scripts/mcp_client.py ping
python scripts/mcp_client.py list-tools

# 干跑（不调真实 API，用样例数据组装报告骨架）
python scripts/compose_report.py \
  --enterprise "示例科技有限公司" \
  --dry-run \
  --output output/customs.json \
  --report-output output/customs.html

# 真实查询 + 渲染（需 MCP 连接就绪）
python scripts/compose_report.py \
  --enterprise "示例科技有限公司" \
  --output output/customs.json \
  --report-output output/customs.html

# 手动调单个工具
python scripts/mcp_client.py call-tool \
  --tool customs_bigdata_export_trends \
  --arguments-json '{"matchKeyword": "示例科技有限公司", "keywordType": "name"}'

# 重渲染已有 JSON
python scripts/render_report.py --input output/customs.json --output output/customs.html
python scripts/render_report.py --input output/customs.json --output output/customs.md
```

## 输出字段

- `subject`：企业全称、匹配关键词、主体类型、是否自动补全。
- `abstract` / `summary`：封面摘要与详细摘要。
- `metrics`：出口年份覆盖、近三月外贸招聘、出口地区数、海外认证数。
- `caliber`：匹配对象、匹配方式、数据范围、产品、局限。
- `core_analysis`：出口趋势（表）、出口产品趋势（表）、外贸招聘画像（KV）、出口订单地区分布（表）、海外认证（表）。
- `representative_records`：代表性出口订单地区（国家/地区 / 订单数 / 金额）。
- `insights`：结构化解读（出口活跃度 / 外贸团队扩张 / 市场多元化 / 国际合规资质）。
- `data_source`：MCP server、数据产品、生成时间、是否 dry-run。

若 API 调用失败，明确报出缺失的配置 / 缺失的工具 / MCP 错误 / 参数校验错误 / 上游网络错误，给出 dry-run 命令或配置步骤，绝不暴露密钥。
