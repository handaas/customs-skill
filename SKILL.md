---
name: customs-report
description: Use for generating a professional customs (海关) big-data report (海关大数据报告) from the HandaaS customs MCP — covering 出口趋势、出口产品趋势(HS码)、外贸招聘画像、出口订单地区分布、海外认证. Trigger when users ask for “海关大数据报告”, “外贸大数据报告”, “出口分析”, “海关数据查询”, “外贸招聘画像”, “海外认证查询”, “出口订单地区分布”, or “企业外贸画像”. Infer the canonical enterprise name (auto-fuzzy-completing a keyword), pick the right MCP tools, and produce HTML + Markdown + JSON reports automatically. MCP 服务名称触发词：海关贸易分析。
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

- MCP 服务当前名称：海关贸易分析（连接仍使用原服务目录标识）。

- 上游 MCP 项目：`handaas-mcp-server/customs-mcp-server`（“海关大数据”）。
- Remote MCP：设置环境变量 `CUSTOMS_MCP_URL`（streamable-http），可选 `CUSTOMS_MCP_TOKEN`。
- 本地 MCP：设置 `HANDAAS_MCP_SERVER_ROOT` 指向 `handaas-mcp-server` 仓库根目录；该 server 自己的 `.env` 提供 `INTEGRATOR_ID` / `SECRET_ID` / `SECRET_KEY`。
- 首次真实查询前，运行 `scripts/mcp_client.py ping` 与 `scripts/mcp_client.py list-tools` 验证连通。

## 按需加载 references

- 不清楚该 MCP 有哪些工具、参数、返回字段、何时调用：`references/mcp-tools-reference.md`。
- 报告结构、章节、质量底线、渲染工作流：`references/report-output.md`。

## 付费调用与预算约束（必读）

1. 每次业务工具调用与每次明细翻页都可能计费；想要 total/页数只是为了精确规划，不构成默认全量拉取的授权。
2. 调用前先推演所需结论与数据维度，确定工具/参数计划、必要页数与最大调用预算。total 未知时，第一个必要明细页既是数据又是 total 探测，绝不为此重复付费。
3. 优先用统计/画像/聚合接口回答问题；只在结论、图表、代表记录或明确全量/计数需求确实需要时取明细。只有确实要求综合报告时才跑完整流水线，绝不因为工具存在就全部调用。
4. 用 schema 允许的最大合理 pageSize 取所需行数，证据/样本目标满足即停，且不超过明确预算。更多调用必须有具体缺失结论支撑，不因为"补齐所有章节"而追加；不得用样本分布冒充全量结论。预算不足以支撑关键结论时，先说明证据缺口与预计额外调用数，等明确授权后再扩大——不得静默降级结论，也不得静默扩大计费范围。
5. 当前任务/报告内复用相同请求（含第一页规划数据）：同一 tool+相同参数只调用一次；不要为渲染重跑 compose/MCP——用已保存 JSON 本地重渲染；不得自动重试、重复取统计/明细或并行获取相同数据；补充查询先列明缺口并复用已返回记录。
6. `total_pages`/`remaining_pages` 是按请求 pageSize 的估算；短但非空的页仍有剩余记录时在预算内继续。只有稳定有效 total 与实际扫描数一致才算完整采集；报错、提前空页、重复页、total 变更/缺失、数量溢出均为不完整。显式预算或样本目标停止即部分采集，绝不宣称全量。不调用无关或不必要维度的工具。

本 skill 分页明细接口：`customs_bigdata_export_order_regions`（订单地区分布，pageSize≤50），预算经 `--max-pages`（默认 1）传递；出口趋势/产品趋势/招聘画像/海外认证与模糊补全为单次调用，不进入分页预算。

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

- **渲染前置检查**：执行会产出含图表 HTML / PDF 的报告命令前，先验证 `node --version` 与本 skill 自带 `assets/vendor/echarts.min.js` 存在；内嵌静态 SVG 的生成需要 Node.js（推荐当前 LTS，无需 npm 包），缺失时报告生成明确报错。查看已生成的 HTML 与 Markdown 输出不需要 Node。

## 数据整理与交付前 AI 审校（强制）

正常生成（`build_payload` / CLI 非 `--dry-run`）在组装阶段已自动执行 `apply_content_review`；`--dry-run` 与正常生成共用同一条路径且只执行一次。`apply_content_review` 只是确定性规则整理，不等于实际 AI 语义审校。离线入口仅用于复核已有报告，重复调用保留此前 `content_review.corrections`。以下语义核对仍须由当前 AI 逐项完成。

出口金额、订单数、地区与 HS 编码是不同维度；以下核对必须由当前 AI 完成，并在审校记录中保留原值、规范值与证据位置。

1. **“其他”不是品类**：HS 编码为“其他/未分类/空”时，标为“其他（未分类，非具体HS编码）”；若它占订单最多，结论写成“分类缺口”，不得称“核心出口品类”。具体 HS 编码数不含“其他”。
2. **年度可比性**：先检查年度是否连续；缺年不插值、不补 0。当前年度为 YTD 时，不把它与完整年度直接同比，也不把 YTD 峰值写成“近年峰值”。同比只对相邻完整年度计算，并写出比较年份。
3. **金额与样本**：“已返回年度出口额合计”只合计返回年度金额，可能含 YTD，不等于海关全量；金额单位保持美元，不把 0 当缺失、不把缺失当 0。
4. **地区口径**：`出口地区总数` 是上游 total，地区和 CR3 只按当前返回样本计算；两者不得混为“覆盖全部市场”。地区图与表按订单数排序，排序不改变事实。
5. **招聘信号**：近三月/累计招聘数、城市与岗位只描述招聘样本；`其他` 城市群占比高时说明归类不足，不据此推断人才布局或跨境电商团队。
6. **主体确认**：`美的集团` 这类非完整法定名称即使候选名称一致，也需在口径中保留原输入与匹配原因；未唯一确认时不调用主体数据。

离线修订已有报告（不调用 MCP，不覆盖 `examples/`）：

```python
import json
from compose_report import apply_content_review

report = json.load(open("examples/01-ev-export/report.json", encoding="utf-8"))
revised = apply_content_review(report)
json.dump(revised, open("output/content-quality-review/01-ev-export/report.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
```

`apply_content_review` 标注未分类 HS、去除重复年度指标、重命名累计出口额/产品数、按订单量排序，并重写年度比较与品类缺口文案；不新增国家、产品、金额或订单事实。

### 草稿 → AI 审校 → 最终交付

1. **采集一次，只出草稿**：本 Skill 的采集脚本（`compose_report.py` 或 `compose_fusion_report.py`，以脚本速查为准）在正常生成和 `--dry-run` 时都于写盘前标记 `data_source.semantic_review.status=pending`，产出 `output/draft.json` 与 `output/draft.html`/`draft.md`。后两者只是预览，不能交付；`apply_content_review`、`organize_report` 等确定性整理不等于实际 AI 审校。
2. **实际阅读再判断**：当前 AI 智能体打开 draft，逐项阅读 `subject`、`core_analysis`、`metrics`、`caliber`、`analysis_request`、`data_source`，按含义做标准化归类、3–5 句三层摘要和有证据的洞察；只引用本报告实际存在的 JSON Pointer，数字逐字取自所引 values，保留否定、最高/至少/至多、试用期/转正后、仅限/部分、疑似/待定/已失效等限定；无数据不生成事实或业务判断。
3. **准备审校请求**：`python scripts/review_report.py --input output/draft.json --request output/review-request.json`。该命令只读草稿，不调用 MCP、外部模型 API，也不新增查询。
4. **写回实际判断**：按请求的 `response_fields` 将逐句摘要、标准化和含 evidence 的洞察写入 `output/ai-review.json`；`source_sha256` 必须与请求一致。字段与 JSON Pointer 规则见 `references/report-output.md`。
5. **应用并核对成品**：`python scripts/review_report.py --input output/draft.json --review output/ai-review.json --output output/final/report.json --report-output output/final/report.html`；核对最终 JSON、HTML、Markdown（目录、图表、空章节）后才交付。不得停在审校请求文件，也不得另存手工润色件冒充最终报告。
6. **展示纪律**：空板块连标题和目录一起隐藏；真实 `0`/`False` 原样保留；失败或未查询只在口径限制中简述，不补 0、不推断“无风险”或“无数据”。空维度只影响展示，不从 `quality_report` 分母抹去未覆盖维度；AI 标准化/摘要是解释视图，不新增数据来源或源数据覆盖率。

## 脚本速查
以下采集示例只生成待审校草稿（`data_source.semantic_review.status=pending`）；最终报告须按上方门禁用 `review_report.py` 生成。

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
  --max-pages 1 \
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
