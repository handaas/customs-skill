# MCP 工具参考 — customs-mcp-server

本 skill 连接的 MCP server：`handaas-mcp-server/customs-mcp-server`（“海关大数据”）。

> **重要**：海关/外贸详情类工具入参为 `matchKeyword`（**企业全称** / 注册号 / 统一社会信用代码 / 企业 id）+ `keywordType`；当用户只给企业关键词时，必须先调关键词模糊查询补全全称。

## 通用约定

- `keywordType` 枚举：`name`（企业名称）/ `nameId`（企业 id）/ `regNumber`（注册号）/ `socialCreditCode`（统一社会信用代码）。
- 分页：`pageIndex` 从 1 开始；订单地区分布 `pageSize` 单页最多 50。
- 单位：金额（`amount`）为**美元**，重量（`weight`）为**千克**。

---

## 工具清单

### 1. `customs_bigdata_export_trends` — 出口趋势

用途：按企业主体返回每年的外贸出口金额与订单数量趋势。

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `matchKeyword` | string | 是 | 企业名称 / 注册号 / 统一社会信用代码 / 企业 id（无全称则先调 fuzzy_search） |
| `keywordType` | string | 否 | 主体类型：name / nameId / regNumber / socialCreditCode |

返回：`total`（出口年份总数）、`exportTrend`（list of {year, amount, count}：年份 / 金额(美元) / 订单数量）。

product_id：`66aa4eac4bb1f40b86c46eab`。

---

### 2. `customs_bigdata_export_product_trends` — 出口产品趋势

用途：查询企业外贸商品发展趋势，提供不同年份的出口商品订单量及海关 HS 编码分布。

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `matchKeyword` | string | 是 | 企业名称 / 注册号 / 统一社会信用代码 / 企业 id |
| `keywordType` | string | 否 | 主体类型：name / nameId / regNumber / socialCreditCode |

返回：`exportGoodsOrderDistribution`（list of {hsCode, count}：HS 商品编码 / 订单数量）、`orderCountOfYear`（每年订单量 list of {year, count}）、`exportGoodsOrderTrend`（出口商品订单量趋势 list of {year, count, name}）。

product_id：`66c9a953268e9d7292c1a919`。

---

### 3. `customs_bigdata_export_recruitment_profile` — 外贸招聘画像

用途：返回企业近三个月外贸相关岗位的招聘情况，包括招聘岗位数、招聘城市、招聘渠道等。

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `matchKeyword` | string | 是 | 企业名称 / 注册号 / 统一社会信用代码 / 企业 id |
| `keywordType` | string | 否 | 主体类型：name / nameId / regNumber / socialCreditCode |

返回：`recruitingLatestThreeMonthCount`（近三个月招聘岗位数）、`recruitingCount`（招聘岗位数）、`recruitingCityCount`（招聘城市数量）、`recruitingCityList`（招聘城市 list）、`recruitingPositions`（招聘岗位名称 list）、`recruitingSourceCount`（招聘渠道数量）、`recruitingSourceList`（招聘渠道 list）、`recruitingPlatforms`（外贸相关岗位平台 list）。

product_id：`66d5b7e0537c3f61d646c346`。

---

### 4. `customs_bigdata_export_order_regions` — 出口订单地区分布

用途：查询企业外贸订单的地理分布，输出订单地区、订单金额、订单数量、涉及国家/地区、净重等。

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `matchKeyword` | string | 是 | 企业名称 / 注册号 / 统一社会信用代码 / 企业 id |
| `pageIndex` | int | 否 | 从 1 开始（默认 1） |
| `pageSize` | int | 否 | 单页最多 50（默认 10） |
| `keywordType` | string | 否 | 主体类型：name / nameId / regNumber / socialCreditCode |

返回（list + `total`）：`destCountry`（国家/地区）、`count`（订单数）、`amount`（金额(美元)）、`weight`（净重(千克)）、`total`（分布地区总数）。

product_id：`66aa4eac4bb1f40b86c46ea0`。

---

### 5. `customs_bigdata_overseas_certifications` — 海外认证

用途：查询企业的海外认证信息，包括认证类别、证书编号、认证领域、相关产品名称等。

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `matchKeyword` | string | 是 | 企业名称 / 注册号 / 统一社会信用代码 / 企业 id |
| `keywordType` | string | 否 | 主体类型：name / nameId / regNumber / socialCreditCode |

返回：`certCount`（总数）、`certList`（list of {certType, certId, certDomain, certProductName}：认证类别 / 证书号 / 认证领域 / 产品名称）。

product_id：`66c9a953268e9d7292c1a96e`。

---

### 6. `customs_bigdata_fuzzy_search` — 关键词模糊查询企业

用途：根据企业名称 / 人名 / 品牌 / 产品 / 岗位等关键词模糊查询企业列表，用于补全企业全称。

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `matchKeyword` | string | 是 | 匹配关键词 |
| `pageIndex` | int | 否 | 分页开始位置（默认 1） |
| `pageSize` | int | 否 | 单页最多 50 |

返回：`total` + 企业列表（`name`、`nameId`、`regCapitalValue`、`foundTime`、`operStatus`、`address`、`legalRepresentative`、`enterpriseType`、`catchReason` 命中原因等）。

product_id：`675cea1f0e009a9ea37edaa1`。

---

## 推荐调用顺序（报告编排）

1. （若仅有关键词）`customs_bigdata_fuzzy_search` → 取 `name` 作为全称。
2. `customs_bigdata_export_trends` → 出口趋势。
3. `customs_bigdata_export_product_trends` → HS 码订单分布。
4. `customs_bigdata_export_recruitment_profile` → 外贸招聘画像。
5. `customs_bigdata_export_order_regions` → 订单地区分布。
6. `customs_bigdata_overseas_certifications` → 海外认证。

> 单次全量报告通常调用 5-6 个工具；除 fuzzy_search 外入参均为企业主体 `matchKeyword` + `keywordType`。
