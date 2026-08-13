#!/usr/bin/env python3
"""Compose a customs (海关) big-data report by orchestrating the customs MCP.

Calls the upstream customs-mcp-server tools and assembles a structured JSON
payload rendered into a professional HTML / Markdown report. Supports
``--dry-run`` which returns a well-formed skeleton from the bundled sample data
WITHOUT contacting the MCP.

Workflow (real run):
  1. Resolve the canonical enterprise name (fuzzy search if only a keyword).
  2. Query export trends / export product trends / recruitment profile / export
     order regions / overseas certifications.
  3. Build unified report JSON with domain sections (出口趋势 / 出口产品趋势 /
     外贸招聘画像 / 出口订单地区分布 / 海外认证).

This file never prints secrets; MCP credentials live in the server's own .env.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
from typing import Any, Dict, List, Mapping, Optional

from common import REPORT_BANNER, REPORT_TYPE, json_dumps, load_json_file, print_json
import mcp_client
from render_report import render_html, render_markdown, html_to_pdf

SAMPLE_PATH = pathlib.Path(__file__).resolve().parent.parent / "assets" / "report.example.json"

# Customs MCP tools.
T_FUZZY = "customs_bigdata_fuzzy_search"
T_EXPORT_TRENDS = "customs_bigdata_export_trends"
T_EXPORT_PRODUCT_TRENDS = "customs_bigdata_export_product_trends"
T_RECRUITMENT_PROFILE = "customs_bigdata_export_recruitment_profile"
T_ORDER_REGIONS = "customs_bigdata_export_order_regions"
T_OVERSEAS_CERTS = "customs_bigdata_overseas_certifications"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _is_api_error(value: Any) -> bool:
    """Detect MCP API error responses (not empty data, but actual failures like 405)."""
    if value is None:
        return False
    if isinstance(value, str):
        return any(s in value for s in ("接口调用失败", "查询失败", "状态码：4", "状态码：5"))
    if isinstance(value, dict):
        for v in value.values():
            if isinstance(v, str) and any(s in v for s in ("接口调用失败", "查询失败", "状态码：4", "状态码：5")):
                return True
    return False

def _first_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        if _is_api_error(value):
            return []
        for key in ("resultList", "list", "items", "data"):
            if isinstance(value.get(key), list):
                return value[key]
    if value in (None, "", {}):
        return []
    return [value]


def _first_record(value: Any) -> Dict[str, Any]:
    for record in _first_list(value):
        if isinstance(record, dict):
            return record
    if isinstance(value, dict):
        return value
    return {}


def _text(value: Any, limit: int = 0) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        t = json.dumps(value, ensure_ascii=False)
    else:
        t = str(value)
    t = " ".join(t.split())
    if limit and len(t) > limit:
        return t[: limit - 1].rstrip() + "…"
    return t


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_call(tool: str, arguments: Dict[str, Any]) -> Any:
    try:
        result = mcp_client.call_tool(tool, arguments)
        # Detect API error responses (405, etc.) and return error marker
        if _is_api_error(result):
            return {"_error": "API错误", "_raw": result}
        return result
    except Exception as exc:
        return {"_error": str(exc)}


def _safe_total(payload: Any) -> Any:
    if isinstance(payload, dict):
        if _is_api_error(payload):
            return None
        return payload.get("total")
    return None


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #

def resolve_enterprise_name(raw: str) -> Dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return {"keyword": "", "enterprise": "", "resolved": False, "reason": "关键词为空"}
    if any(suffix in raw for suffix in ("公司", "集团", "有限", "院", "厂", "中心", "事务所", "合作社", "合伙")):
        return {"keyword": raw, "enterprise": raw, "resolved": True, "reason": "视为企业全称"}
    fuzzy = _safe_call(T_FUZZY, {"matchKeyword": raw, "pageSize": 1})
    record = _first_record(fuzzy)
    name = str(record.get("name") or "").strip()
    if name:
        return {"keyword": raw, "enterprise": name, "resolved": True, "reason": "由关键词模糊查询补全", "fuzzy_total": _int(_safe_total(fuzzy)), "record": record}
    return {"keyword": raw, "enterprise": raw, "resolved": False, "reason": "模糊查询未命中企业全称，按关键词直查"}


# --------------------------------------------------------------------------- #
# Enterprise profile helpers (from fuzzy_search record)
# --------------------------------------------------------------------------- #

def _extract_profile(record: Dict[str, Any]) -> Dict[str, Any]:
    """Extract enterprise profile fields from a fuzzy_search record."""
    return {
        "name": _text(record.get("name")),
        "reg_capital": record.get("regCapitalValue"),
        "reg_capital_coin": _text(record.get("regCapitalCoinType")),
        "annual_turnover": _text(record.get("annualTurnover")),
        "oper_status": _text(record.get("operStatus")),
        "enterprise_type": _text(record.get("enterpriseType")),
        "found_time": _text(record.get("foundTime")),
        "legal_rep": _text(record.get("legalRepresentative")),
        "address": _text(record.get("address")),
        "homepage": _text(record.get("homepage")),
    }


def _format_capital(val: Any, coin: str = "") -> str:
    """Format capital value: 10995210218.0 -> '109.95 亿'."""
    try:
        v = float(val)
        if v >= 1e8:
            s = f"{v / 1e8:.2f} 亿"
        elif v >= 1e4:
            s = f"{v / 1e4:.2f} 万"
        else:
            s = f"{v:.0f}"
        if coin:
            s += f" {coin}"
        return s
    except (TypeError, ValueError):
        return _text(val) if val else "-"


def _enrich_metrics_with_profile(metrics: List[Dict[str, Any]], record: Any) -> List[Dict[str, Any]]:
    """Append enterprise profile metrics from a fuzzy_search record."""
    if not isinstance(record, dict):
        return metrics
    _prof = _extract_profile(record)
    if _prof.get("reg_capital") and _prof["reg_capital"] not in ("-", "", None):
        metrics.append({"label": "注册资本", "value": _format_capital(_prof["reg_capital"], _prof.get("reg_capital_coin", "")), "hint": "工商登记注册资本"})
    if _prof.get("found_time") and _prof["found_time"] != "-":
        metrics.append({"label": "成立时间", "value": _prof["found_time"], "hint": "工商登记成立日期"})
    if _prof.get("oper_status") and _prof["oper_status"] != "-":
        metrics.append({"label": "经营状态", "value": _prof["oper_status"], "hint": "工商登记经营状态"})
    if _prof.get("enterprise_type") and _prof["enterprise_type"] != "-":
        metrics.append({"label": "企业类型", "value": _prof["enterprise_type"], "hint": "工商登记企业类型"})
    if _prof.get("legal_rep") and _prof["legal_rep"] != "-":
        metrics.append({"label": "法定代表人", "value": _prof["legal_rep"], "hint": "工商登记法定代表人"})
    return metrics


def _derive_core_metrics(metrics: List[Dict[str, Any]], core: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Derive additional metrics from core analysis sections."""
    trends = core.get("export_trends", []) if isinstance(core, dict) else []
    products = core.get("export_product_trends", []) if isinstance(core, dict) else []
    regions = core.get("order_regions", []) if isinstance(core, dict) else []
    certs = core.get("overseas_certs", []) if isinstance(core, dict) else []
    if isinstance(trends, list) and trends:
        metrics.append({"label": "出口年份", "value": str(len(trends)), "hint": "有出口记录的年度数"})
        try:
            total_usd = sum(float(r.get("出口金额(美元)", 0) or 0) for r in trends if r.get("出口金额(美元)"))
            if total_usd > 0:
                if total_usd >= 1e6:
                    metrics.append({"label": "累计出口额", "value": f"${total_usd/1e6:.2f}M", "hint": "历年出口金额合计（美元）"})
                else:
                    metrics.append({"label": "累计出口额", "value": f"${total_usd/1e3:.0f}K", "hint": "历年出口金额合计（美元）"})
        except (ValueError, TypeError):
            pass
    if isinstance(products, list) and products:
        metrics.append({"label": "出口产品数", "value": str(len(products)), "hint": "不同HS编码的出口产品数"})
    if isinstance(regions, list) and regions:
        metrics.append({"label": "出口国家/地区数", "value": str(len(regions)), "hint": "出口目的地数量"})
        try:
            nums = [int(r.get("订单数", 0) or 0) for r in regions]
            total = sum(nums)
            if total > 0:
                top3 = sum(sorted(nums, reverse=True)[:3])
                metrics.append({"label": "地区CR3", "value": f"{top3/total*100:.1f}%", "hint": "前3大出口地区订单集中度"})
        except (ValueError, TypeError):
            pass
    if isinstance(certs, list) and certs:
        metrics.append({"label": "海外认证数", "value": str(len(certs)), "hint": "持有的海外认证数量"})
    return metrics


# --------------------------------------------------------------------------- #
# Section builders
# --------------------------------------------------------------------------- #

def build_subject(raw: str, resolved: Mapping[str, Any], keyword_type: str) -> Dict[str, Any]:
    return {
        "enterprise": resolved.get("enterprise") or raw,
        "matchKeyword": resolved.get("enterprise") or raw,
        "keywordType": keyword_type,
        "match_raw": raw,
        "resolved": bool(resolved.get("resolved")),
        "resolve_reason": resolved.get("reason", ""),
    }


def build_caliber(subject: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "match_target": subject.get("enterprise") or subject.get("match_raw"),
        "match_type": f"海关/外贸数据按企业主体匹配（keywordType={subject.get('keywordType', 'name')}）",
        "data_scope": "出口趋势（金额/订单）、出口产品趋势（HS 码）、外贸招聘画像、出口订单地区分布、海外认证",
        "products": ["出口趋势", "出口产品趋势", "外贸招聘画像", "出口订单地区分布", "海外认证"],
        "limit": "数据来自海关/外贸公开数据源；金额单位为美元，净重单位为千克；少量字段可能存在更新延迟。",
    }


def build_metrics(trends: Any, recruitment: Any, regions: Any, certs: Any) -> List[Dict[str, Any]]:
    metrics: List[Dict[str, Any]] = []
    t = trends if isinstance(trends, dict) else {}
    trend_list = t.get("exportTrend") if isinstance(t.get("exportTrend"), list) else []
    trend_year_count = len(trend_list) if isinstance(trend_list, list) else None
    r = recruitment if isinstance(recruitment, dict) else {}
    recruit_3m = r.get("recruitingLatestThreeMonthCount")
    recruit_total = r.get("recruitingCount")
    regions_total = _int(_safe_total(regions)) if isinstance(regions, dict) else None
    cert_count = None
    if isinstance(certs, dict):
        cert_count = certs.get("certCount")

    metrics.append({"label": "出口年份覆盖", "value": (str(trend_year_count) + " 年" if trend_year_count else "-"), "hint": "出口趋势所涉年份数"})
    metrics.append({"label": "累计外贸招聘", "value": (_text(recruit_total) if recruit_total is not None else "-"), "hint": "历史累计外贸相关岗位招聘总数（recruitingCount）"})
    metrics.append({"label": "近三月外贸招聘", "value": (_text(recruit_3m) if recruit_3m is not None else "-"), "hint": "近三个月外贸相关岗位招聘数"})
    metrics.append({"label": "出口地区数", "value": (_text(regions_total) if regions_total is not None else "-"), "hint": "出口订单分布地区总数"})
    metrics.append({"label": "海外认证数", "value": (_text(cert_count) if cert_count is not None else "-"), "hint": "海外认证证书总数"})
    return [m for m in metrics if m.get("value") not in ("", None, "-")]


def _trend_rows(trends: Any) -> List[Dict[str, Any]]:
    out = []
    for item in _first_list((trends if isinstance(trends, dict) else {}).get("exportTrend")):
        if not isinstance(item, dict):
            continue
        out.append({
            "年份": _text(item.get("year")) or "-",
            "出口金额(美元)": _text(item.get("amount") or "-"),
            "订单数量": _text(item.get("count") or item.get("value") or "-"),
        })
    return out


def _product_trend_rows(product_trends: Any) -> List[Dict[str, Any]]:
    out = []
    p = product_trends if isinstance(product_trends, dict) else {}
    dist = p.get("exportGoodsOrderDistribution")
    for item in _first_list(dist):
        if not isinstance(item, dict):
            continue
        out.append({
            "HS 商品编码": _text(item.get("hsCode")) or "-",
            "订单数量": _text(item.get("count") or item.get("value") or "-"),
        })
    return out


def _recruitment_kv(recruitment: Any) -> Dict[str, Any]:
    r = recruitment if isinstance(recruitment, dict) else {}
    kv: Dict[str, Any] = {}
    if r.get("recruitingLatestThreeMonthCount") is not None:
        kv["近三个月招聘岗位数"] = _text(r.get("recruitingLatestThreeMonthCount"))
    if r.get("recruitingCount") is not None:
        kv["招聘岗位数"] = _text(r.get("recruitingCount"))
    if r.get("recruitingCityCount") is not None:
        kv["招聘城市数量"] = _text(r.get("recruitingCityCount"))
    if isinstance(r.get("recruitingCityList"), list) and r["recruitingCityList"]:
        kv["招聘城市"] = "、".join(_text(t) for t in r["recruitingCityList"][:15] if t)
    if isinstance(r.get("recruitingPositions"), list) and r["recruitingPositions"]:
        kv["招聘岗位"] = "、".join(_text(t) for t in r["recruitingPositions"][:15] if t)
    if r.get("recruitingSourceCount") is not None:
        kv["招聘渠道数量"] = _text(r.get("recruitingSourceCount"))
    if isinstance(r.get("recruitingSourceList"), list) and r["recruitingSourceList"]:
        kv["招聘渠道"] = "、".join(_text(t) for t in r["recruitingSourceList"][:15] if t)
    if isinstance(r.get("recruitingPlatforms"), list) and r["recruitingPlatforms"]:
        kv["外贸相关平台"] = "、".join(_text(t) for t in r["recruitingPlatforms"][:15] if t)
    return kv


def _region_rows(regions: Any) -> List[Dict[str, Any]]:
    out = []
    for item in _first_list(regions):
        if not isinstance(item, dict):
            continue
        out.append({
            "国家/地区": _text(item.get("destCountry")) or "-",
            "订单数": _text(item.get("count") or "-"),
            "金额(美元)": _text(item.get("amount") or "-"),
            "净重(千克)": _text(item.get("weight") or "-"),
        })
    return out


def _cert_rows(certs: Any) -> List[Dict[str, Any]]:
    out = []
    c = certs if isinstance(certs, dict) else {}
    for item in _first_list(c.get("certList") if isinstance(c.get("certList"), list) else c):
        if not isinstance(item, dict):
            continue
        out.append({
            "认证类别": _text(item.get("certType")) or "-",
            "认证领域": _text(item.get("certDomain")) or "-",
            "证书号": _text(item.get("certId")) or "-",
            "产品名称": _text(item.get("certProductName")) or "-",
        })
    return out


def build_core_analysis(trends: Any, product_trends: Any, recruitment: Any, regions: Any, certs: Any) -> Dict[str, Any]:
    trend_rows = _trend_rows(trends)
    product_rows = _product_trend_rows(product_trends)
    recruit_kv = _recruitment_kv(recruitment)
    region_rows = _region_rows(regions)
    cert_rows = _cert_rows(certs)
    regions_total = _safe_total(regions) if isinstance(regions, dict) else None

    sections = [
        {"key": "export_trends", "title": "出口趋势", "kind": "multi_line", "note": "按年度统计出口金额（美元）与订单数量",
         "chart": {"x": "年份", "series": ["出口金额(美元)", "订单数量"], "area": True},
         "columns": [("年份", "年份"), ("出口金额(美元)", "出口金额(美元)"), ("订单数量", "订单数量")]},
        {"key": "export_product_trends", "title": "出口产品趋势", "kind": "bar", "note": "按海关 HS 商品编码统计出口订单量",
         "chart": {"name": "HS 商品编码", "value": "订单数量", "orient": "h"},
         "columns": [("HS 商品编码", "HS 商品编码"), ("订单数量", "订单数量")]},
        {"key": "recruitment_profile", "title": "外贸招聘画像", "kind": "kv"},
        {"key": "order_regions", "title": "出口订单地区分布", "kind": "bar",
         "note": f"共 {regions_total if regions_total is not None else '若干'} 个分布地区，展示前 N 个",
         "chart": {"name": "国家/地区", "value": "订单数", "orient": "h"},
         "columns": [("国家/地区", "国家/地区"), ("订单数", "订单数"), ("金额(美元)", "金额(美元)"), ("净重(千克)", "净重(千克)")]},
        {"key": "overseas_certs", "title": "海外认证", "kind": "table", "note": "海外认证证书明细",
         "columns": [("认证类别", "认证类别"), ("认证领域", "认证领域"), ("证书号", "证书号"), ("产品名称", "产品名称")]},
    ]

    return {
        "sections": sections,
        "export_trends": trend_rows,
        "export_product_trends": product_rows,
        "recruitment_profile": recruit_kv,
        "order_regions": region_rows,
        "overseas_certs": cert_rows,
        # raw recruitment lists for downstream insights
        "recruitment_positions": (recruitment.get("recruitingPositions") if isinstance(recruitment, dict) and isinstance(recruitment.get("recruitingPositions"), list) else []),
        "recruitment_cities": (recruitment.get("recruitingCityList") if isinstance(recruitment, dict) and isinstance(recruitment.get("recruitingCityList"), list) else []),
    }


def build_records(core: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for item in core.get("order_regions") or []:
        out.append({
            "国家/地区": item.get("国家/地区") or "-",
            "订单数": item.get("订单数") or "-",
            "金额(美元)": item.get("金额(美元)") or "-",
        })
    return out[:20]


def _trend_analysis(rows: List[Mapping[str, Any]], series_key: str, period_key: str = "年份") -> Dict[str, Any]:
    """Compute trend direction, peak, and YoY change for a numeric series in trend rows."""
    nums = []
    for r in rows:
        try:
            nums.append(float(str(r.get(series_key, 0)).replace(",", "")))
        except (TypeError, ValueError):
            nums.append(0.0)
    if not nums:
        return {}
    peak_idx = max(range(len(nums)), key=lambda i: nums[i])
    direction = "持平"
    yoy = ""
    if len(nums) >= 2:
        last, prev = nums[-1], nums[-2]
        if prev > 0:
            pct = (last - prev) / prev * 100
            if pct > 5:
                direction = f"上升 {pct:.0f}%"
            elif pct < -5:
                direction = f"下降 {abs(pct):.0f}%"
            yoy = f"同比 {pct:+.0f}%"
    return {"peak_period": rows[peak_idx].get(period_key, "-"), "peak_value": nums[peak_idx], "direction": direction, "yoy": yoy, "last": nums[-1]}


def _concentration(rows: List[Mapping[str, Any]], name_key: str, value_key: str, top_n: int = 3) -> Dict[str, Any]:
    """Compute top-N concentration (CRn) and dominant category from generic rows."""
    items = []
    for r in rows:
        try:
            items.append((r.get(name_key, "-"), float(str(r.get(value_key, 0)).replace(",", ""))))
        except (TypeError, ValueError):
            items.append((r.get(name_key, "-"), 0.0))
    total = sum(v for _, v in items)
    if not total:
        return {}
    items.sort(key=lambda x: x[1], reverse=True)
    cr = sum(v for _, v in items[:top_n]) / total * 100
    return {"top": items[0][0], "top_share": items[0][1] / total * 100, "cr": cr, "total": total}


def _city_region_cluster(cities: List[Any]) -> Dict[str, int]:
    """Cluster Chinese cities into region groups (珠三角/长三角/京津冀/其他)
    for recruitment geography concentration."""
    pearl_river = {"广州市", "深圳市", "珠海市", "佛山市", "东莞市", "中山市", "惠州市", "江门市", "肇庆市"}
    yangtze = {"上海市", "南京市", "苏州市", "无锡市", "常州市", "南通市", "扬州市", "镇江市", "盐城市", "泰州市",
               "杭州市", "宁波市", "温州市", "嘉兴市", "湖州市", "绍兴市", "金华市", "台州市", "舟山市",
               "合肥市", "芜湖市"}
    jingjinji = {"北京市", "天津市", "石家庄市", "唐山市", "保定市", "廊坊市"}
    cluster = {"珠三角": 0, "长三角": 0, "京津冀": 0, "其他": 0}
    for c in cities:
        name = _text(c)
        if name in pearl_river:
            cluster["珠三角"] += 1
        elif name in yangtze:
            cluster["长三角"] += 1
        elif name in jingjinji:
            cluster["京津冀"] += 1
        else:
            cluster["其他"] += 1
    # drop empty groups
    return {k: v for k, v in cluster.items() if v > 0}


def build_insights(subject: Mapping[str, Any], core: Mapping[str, Any], metrics: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    insights: List[Dict[str, Any]] = []
    metric_map = {m["label"]: str(m["value"]) for m in metrics}
    trend_years = metric_map.get("出口年份覆盖")
    recruit_3m = metric_map.get("近三月外贸招聘")
    regions_total = metric_map.get("出口地区数")
    cert_count = metric_map.get("海外认证数")

    trend_rows = core.get("export_trends") or []
    if trend_rows:
        amt_ta = _trend_analysis(trend_rows, "出口金额(美元)")
        cnt_ta = _trend_analysis(trend_rows, "订单数量")
        ev_parts = []
        if amt_ta:
            ev_parts.append(f"出口金额峰值在“{amt_ta['peak_period']}”（{amt_ta['peak_value']:.0f} 美元），近年{amt_ta['direction']}{('，' + amt_ta.get('yoy', '')) if amt_ta.get('yoy') else ''}")
        if cnt_ta:
            ev_parts.append(f"订单量峰值在“{cnt_ta['peak_period']}”（{cnt_ta['peak_value']:.0f} 笔），近年{cnt_ta['direction']}")
        insights.append({
            "feature": "出口活跃度",
            "evidence": f"出口趋势覆盖 {trend_years or '若干'}。" + ("；".join(ev_parts) + "。" if ev_parts else ""),
            "interpretation": "出口金额与订单量的年度走势反映企业外贸规模扩张或收缩；金额与订单同步上升通常意味着量价齐升，订单升而金额降则可能存在价格竞争加剧。",
        })
    product_rows = core.get("export_product_trends") or []
    if product_rows:
        conc = _concentration(product_rows, "HS 商品编码", "订单数量", 3)
        if conc:
            insights.append({
                "feature": "核心出口品类",
                "evidence": f"“HS {conc['top']}”为订单最多的品类，占比约 {conc['top_share']:.0f}%，前 3 类合计 {conc['cr']:.0f}%（CR3）。",
                "interpretation": "出口品类集中度反映企业核心出口产品的竞争力；CR3 越高说明出口结构越聚焦，反之则品类更分散。",
            })
    region_rows = core.get("order_regions") or []
    if region_rows:
        conc = _concentration(region_rows, "国家/地区", "订单数", 3)
        if conc:
            insights.append({
                "feature": "出口市场集中度",
                "evidence": f"“{conc['top']}”为订单最多的市场，占比约 {conc['top_share']:.0f}%，前 3 市场合计 {conc['cr']:.0f}%（CR3）。",
                "interpretation": "出口市场集中度过高存在单一市场政策/汇率波动风险；分散布局更稳健，建议关注头部市场的订单可持续性。",
            })
    if recruit_3m:
        insights.append({
            "feature": "外贸团队扩张",
            "evidence": f"近三个月外贸相关岗位招聘 {recruit_3m} 人。",
            "interpretation": "外贸岗位招聘强度反映企业国际化业务的扩张意愿；高频招聘通常对应出口业务增长或新市场开拓。",
        })
    # 外贸职能结构（跨境电商信号检测）
    positions = core.get("recruitment_positions") or []
    if positions:
        cross_border_signals = [p for p in positions if p in ("外贸员", "运营", "美工", "精品开发", "亚马逊运营", "Shopee运营", "独立站运营")]
        signal_text = "、".join(cross_border_signals) if cross_border_signals else ""
        is_cb = len(cross_border_signals) >= 2
        evidence = f"招聘职能覆盖：{'、'.join(positions[:8])}。"
        if is_cb:
            evidence += f"其中 {signal_text} 等岗位同时出现，呈现典型跨境电商团队特征。"
            interp = "外贸员+运营+美工等岗位组合是跨境电商（B2C/平台运营）的典型团队结构，提示企业可能依托亚马逊/Shopee/独立站等渠道开展跨境零售出口，而非传统 B2B 外贸。"
        else:
            interp = "外贸岗位结构反映国际化业务组织方式；传统外贸以单证/跟单/外贸员为主，跨境电商则叠加运营/美工/精品开发等岗位。"
        insights.append({"feature": "外贸职能结构", "evidence": evidence, "interpretation": interp})
    # 招聘地域集中度（按城市群聚类：珠三角/长三角/其他）
    cities = core.get("recruitment_cities") or []
    if len(cities) >= 3:
        cluster = _city_region_cluster(cities)
        if cluster:
            top_region = max(cluster.items(), key=lambda kv: kv[1])
            total_c = sum(cluster.values())
            shares = "、".join(f"{k} {v} 城（{v / total_c * 100:.0f}%）" for k, v in sorted(cluster.items(), key=lambda kv: kv[1], reverse=True))
            insights.append({
                "feature": "招聘地域集中度",
                "evidence": f"招聘覆盖 {total_c} 个城市，按城市群聚类：{shares}，最高为“{top_region[0]}”。",
                "interpretation": "招聘城市集中度反映企业人才布局与运营重心；珠三角/长三角高集中度通常对应制造业带与跨境电商集聚区，便于线下管理与供应链协同。",
            })
    if regions_total:
        insights.append({
            "feature": "市场多元化",
            "evidence": f"出口订单分布 {regions_total} 个国家/地区。",
            "interpretation": "出口地区数量反映市场多元化程度；集中度过高存在单一市场风险，分散布局更稳健。",
        })
    if cert_count:
        insights.append({
            "feature": "国际合规资质",
            "evidence": f"海外认证共 {cert_count} 项。",
            "interpretation": "海外认证数量反映企业进入目标市场的合规能力，常见于 CE/FCC/RoHS 等，是出口能力的硬性背书。",
        })
    if not insights:
        insights.append({
            "feature": "数据完整性",
            "evidence": "部分维度未返回有效数据。",
            "interpretation": "建议核对匹配关键词是否为企业全称，或检查 MCP 连接与上游数据产品覆盖范围。",
        })
    return insights


def build_abstract(subject: Mapping[str, Any], core: Mapping[str, Any], metrics: List[Mapping[str, Any]]) -> str:
    name = subject.get("enterprise") or subject.get("match_raw") or "目标企业"
    parts = [f"本报告以“{name}”为分析对象，基于海关/外贸大数据，系统呈现企业出口趋势、出口产品趋势、外贸招聘画像、出口订单地区分布与海外认证。"]
    if metrics:
        kv = "、".join(f"{m['label']} {m['value']}" for m in metrics[:5])
        parts.append(f"关键指标包括：{kv}。")
    parts.append("报告同时给出出口活跃度、外贸团队扩张、市场多元化与国际合规资质的结构化解读，便于外贸经营、市场拓展与合规风控决策参考。")
    return "".join(parts)


# --------------------------------------------------------------------------- #
# Dry-run sample
# --------------------------------------------------------------------------- #

def build_dry_run_payload(raw: str, keyword_type: str) -> Dict[str, Any]:
    try:
        sample = load_json_file(SAMPLE_PATH)
    except Exception:
        sample = {}
    sample = sample if isinstance(sample, dict) else {}
    subject = sample.get("subject") or {"enterprise": raw, "matchKeyword": raw, "keywordType": keyword_type, "match_raw": raw}
    subject = {**subject, "match_raw": raw, "keywordType": keyword_type}
    core = sample.get("core_analysis") or {}
    metrics = sample.get("metrics") or []
    return _assemble(subject, core, metrics, dry_run=True)


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #

def _assemble(subject: Mapping[str, Any], core: Mapping[str, Any], metrics: List[Mapping[str, Any]], *, dry_run: bool) -> Dict[str, Any]:
    abstract = build_abstract(subject, core, metrics)
    records = build_records(core)
    insights = build_insights(subject, core, metrics)
    # Quality gate: count populated core-analysis sections.
    ca = core if isinstance(core, dict) else {}
    secs = ca.get("sections", [])
    if secs:
        total_secs = len(secs)
        populated = sum(1 for s in secs if isinstance(s, dict) and ca.get(s.get("key")) not in (None, "", [], {}))
    else:
        total_secs = max(1, len([k for k in ca if k != "sections"]))
        populated = sum(1 for k in ca if k != "sections" and ca.get(k) not in (None, "", [], {}))
    quality_report = {
        "total_sections": total_secs,
        "populated_sections": populated,
        "empty_sections": total_secs - populated,
        "coverage_pct": round(populated / max(1, total_secs) * 100),
    }
    if populated == 0:
        import sys
        print("⚠️ 质量门禁警告: 所有核心分析维度均无数据", file=sys.stderr)
    title = f"{subject.get('enterprise') or '目标企业'} 海关大数据报告"
    return {
        "report_type": REPORT_TYPE,
        "title": title,
        "banner": REPORT_BANNER,
        "subject": dict(subject),
        "abstract": abstract,
        "summary": abstract,
        "executive_summary": [item["interpretation"] for item in insights][:5] or [abstract[:120]],
        "metrics": list(metrics),
        "caliber": build_caliber(subject),
        "core_analysis": dict(core),
        "representative_records": records,
        "insights": insights,
        "data_source": {
            "mcp_server": "customs-mcp-server",
            "products": [
                {"name": "出口趋势", "product_id": "66aa4eac4bb1f40b86c46eab"},
                {"name": "出口产品趋势", "product_id": "66c9a953268e9d7292c1a919"},
                {"name": "外贸招聘画像", "product_id": "66d5b7e0537c3f61d646c346"},
                {"name": "出口订单地区分布", "product_id": "66aa4eac4bb1f40b86c46ea0"},
                {"name": "海外认证", "product_id": "66c9a953268e9d7292c1a96e"},
            ],
            "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "dry_run": dry_run,
            "quality_report": quality_report,
        },
    }


def build_payload(raw: str, keyword_type: str, page_size: int) -> Dict[str, Any]:
    resolved = resolve_enterprise_name(raw)
    enterprise = resolved["enterprise"]
    mk_args: Dict[str, Any] = {"matchKeyword": enterprise, "keywordType": keyword_type}

    trends = _safe_call(T_EXPORT_TRENDS, mk_args)
    product_trends = _safe_call(T_EXPORT_PRODUCT_TRENDS, mk_args)
    recruitment = _safe_call(T_RECRUITMENT_PROFILE, mk_args)
    regions = _safe_call(T_ORDER_REGIONS, {"matchKeyword": enterprise, "keywordType": keyword_type, "pageIndex": 1, "pageSize": page_size})
    certs = _safe_call(T_OVERSEAS_CERTS, mk_args)

    subject = build_subject(raw, resolved, keyword_type)
    core = build_core_analysis(trends, product_trends, recruitment, regions, certs)
    metrics = build_metrics(trends, recruitment, regions, certs)
    _derive_core_metrics(metrics, core if isinstance(core, dict) else {})
    # --- Enterprise profile enrichment (from fuzzy_search) ---
    _enrich_metrics_with_profile(metrics, resolved.get("record") if isinstance(resolved, dict) else None)
    return _assemble(subject, core, metrics, dry_run=False)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description="Compose a customs big-data report via the customs MCP.")
    parser.add_argument("--enterprise", required=True, help="企业全称或关键词（关键词将自动模糊补全）")
    parser.add_argument("--keyword-type", default="name", help="主体类型：name/nameId/regNumber/socialCreditCode")
    parser.add_argument("--page-size", type=int, default=10, help="出口订单地区明细分页大小（最多 50）")
    parser.add_argument("--dry-run", action="store_true", help="不调用真实 MCP，使用样例数据组装报告骨架")
    parser.add_argument("--output", help="输出 JSON 路径；省略则打印到 stdout")
    parser.add_argument("--report-output", help="同时输出 HTML 报告（.html）与 Markdown 报告（.md）")
    parser.add_argument("--pdf-output", help="额外输出 PDF 报告（.pdf）；需要 Playwright + Chromium")
    args = parser.parse_args()

    if args.dry_run:
        payload = build_dry_run_payload(args.enterprise, args.keyword_type)
    else:
        payload = build_payload(args.enterprise, args.keyword_type, args.page_size)

    if args.output:
        out = pathlib.Path(args.output).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json_dumps(payload, pretty=True), encoding="utf-8")
        print_json({"ok": True, "json": str(out), "dry_run": args.dry_run})
    else:
        print_json(payload)

    if args.report_output:
        base_out = pathlib.Path(args.report_output).expanduser()
        base_out.parent.mkdir(parents=True, exist_ok=True)
        html_path = base_out.with_suffix(".html") if base_out.suffix.lower() not in (".html", ".htm") else base_out
        md_path = html_path.with_suffix(".md")
        html_path.write_text(render_html(payload), encoding="utf-8")
        md_path.write_text(render_markdown(payload), encoding="utf-8")
        if args.pdf_output:
            pdf_path = pathlib.Path(args.pdf_output).expanduser()
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            html_to_pdf(render_html(payload), str(pdf_path))
        print_json({"ok": True, "html": str(html_path), "markdown": str(md_path), "pdf": str(pdf_path) if args.pdf_output else None, "dry_run": args.dry_run})


if __name__ == "__main__":
    main()
