# customs-skill：已验收示例

本目录只展示 **6 个完全通过的真实正向场景**（最低要求4例）。

每例均为真实、非空、非缓存调用；技术检查、独立业务检查和离线浏览器检查全部通过。不把空数据、报错处理或仅生成页面计作通过。历史验收记录保留在本地 test-records/；本目录的 HTML/Markdown 由原 JSON 重渲染，不代表重新查询或重新验收。

| 场景 | 真实使用需求 | 报告 |
|---|---|---|
| [新能源汽车出口画像](01-ev-export/README.md) | 请分析比亚迪股份有限公司的出口趋势、产品趋势、地区分布和海外认证。 | [HTML](01-ev-export/report.html) / [MD](01-ev-export/report.md) / [JSON](01-ev-export/report.json) |
| [动力电池出口画像](02-battery-export/README.md) | 请查看宁德时代新能源科技股份有限公司的出口产品、地区和认证线索。 | [HTML](02-battery-export/report.html) / [MD](02-battery-export/report.md) / [JSON](02-battery-export/report.json) |
| [家电出口结构](03-home-appliance-export/README.md) | 请分析海尔智家股份有限公司的出口产品结构与区域分布。 | [HTML](03-home-appliance-export/report.html) / [MD](03-home-appliance-export/report.md) / [JSON](03-home-appliance-export/report.json) |
| [电子制造出口能力](05-electronics-contract-export/README.md) | 请评估立讯精密工业股份有限公司的外贸活跃度、产品和地区覆盖。 | [HTML](05-electronics-contract-export/report.html) / [MD](05-electronics-contract-export/report.md) / [JSON](05-electronics-contract-export/report.json) |
| [手机厂商出口地区](06-smartphone-emerging-markets/README.md) | 请分析深圳传音控股股份有限公司的出口订单地区与产品趋势。 | [HTML](06-smartphone-emerging-markets/report.html) / [MD](06-smartphone-emerging-markets/report.md) / [JSON](06-smartphone-emerging-markets/report.json) |
| [企业简称外贸解析](07-fuzzy-export-match/README.md) | 请用“美的集团”查询海关数据，并明确匹配主体。 | [HTML](07-fuzzy-export-match/report.html) / [MD](07-fuzzy-export-match/report.md) / [JSON](07-fuzzy-export-match/report.json) |

- [逐例验收结果](results.json) · [业务复核](review.json) · [证据摘要](evidence-index.json)
- 完整测试记录仅存于本地 test-records/live-replaced-20261027/（仅本地审计用，不随本目录发布），不属于可展示示例。
- 当前数据与结论限于原查询时点及公开样本；覆盖率不代表准确率或低风险。
