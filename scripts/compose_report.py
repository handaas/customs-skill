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
import copy
import datetime as dt
import json
import pathlib
from typing import Any, Dict, List, Mapping, Optional, Sequence

from common import REPORT_BANNER, REPORT_TYPE, attach_pagination, call_tool_once, collect_detail_pages, die, json_dumps, load_json_file, paid_call_scope, print_json
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


def _is_empty_response(value: Any) -> bool:
    if value is None or value == "" or value == {}:
        return True
    if isinstance(value, str):
        text = value.strip().lower()
        return any(token in text for token in ("查询数据为空", "暂无数据", "未查询到", "未检索到", "no data"))
    if isinstance(value, dict):
        for key in ("text", "message", "msg"):
            if key in value and _is_empty_response(value.get(key)):
                return True
        containers = [value.get(key) for key in ("resultList", "list", "items", "data") if key in value]
        if containers and all(item in (None, [], {}) for item in containers):
            total = value.get("total")
            return total in (None, 0, "0")
    return False


def _is_api_error(value: Any) -> bool:
    """Return True only for failed calls; platform empty-data text is not an error."""
    if value is None or _is_empty_response(value):
        return False
    if isinstance(value, str):
        text = value.lower()
        return any(token in text for token in (
            "接口调用失败", "查询失败", "状态码：4", "状态码：5",
            "upstream timeout", "tool failed", "unauthorized", "forbidden",
        ))
    if isinstance(value, dict):
        if value.get("isError") is True or value.get("is_error") is True or value.get("success") is False:
            return True
        if any(value.get(key) not in (None, "", False) for key in ("_error", "error")):
            return True
        code = value.get("code")
        if code not in (None, 0, "0", 200, "200", 10001, "10001"):
            return True
        return any(_is_api_error(value.get(key)) for key in ("text", "message", "msg") if key in value)
    return False


def _first_list(value: Any) -> List[Any]:
    if _is_api_error(value) or _is_empty_response(value):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("resultList", "list", "items"):
            if isinstance(value.get(key), list):
                return value[key]
        nested = value.get("data")
        if isinstance(nested, list):
            return nested
        if isinstance(nested, dict):
            return _first_list(nested)
    return []


def _first_record(value: Any) -> Dict[str, Any]:
    if _is_api_error(value) or _is_empty_response(value):
        return {}
    for record in _first_list(value):
        if isinstance(record, dict) and _has_evidence(record):
            return record
    if isinstance(value, dict):
        envelope = {"resultList", "list", "items", "data", "total", "pageNo", "pageSize", "text", "message", "msg", "code"}
        if not set(value).issubset(envelope) and _has_evidence(value):
            return value
    return {}


def _has_evidence(value: Any) -> bool:
    if value is None or value == "" or value == "-":
        return False
    if _is_api_error(value) or _is_empty_response(value):
        return False
    if isinstance(value, dict):
        return any(_has_evidence(item) for key, item in value.items() if key not in {"total", "pageNo", "pageSize"})
    if isinstance(value, (list, tuple, set)):
        return any(_has_evidence(item) for item in value)
    return True


def _meaningful_row(row: Mapping[str, Any]) -> bool:
    return any(_has_evidence(value) for value in row.values())


def _attach_call_status(report: Dict[str, Any], calls: Mapping[str, Any]) -> Dict[str, Any]:
    """Disclose failed and genuine-empty domains without exposing upstream details."""
    failed = [name for name, payload in calls.items() if _is_api_error(payload)]
    empty = [name for name, payload in calls.items() if not _is_api_error(payload) and _is_empty_response(payload)]
    source = report.setdefault("data_source", {})
    source["failed_products"] = failed
    source["empty_products"] = empty
    return report


def _apply_resolution_limit(report: Dict[str, Any], resolved: Mapping[str, Any]) -> Dict[str, Any]:
    subject = report.setdefault("subject", {})
    subject["resolved"] = False
    subject["requires_confirmation"] = True
    subject["resolve_reason"] = resolved.get("reason", "主体未唯一确认")
    subject["candidate_sample"] = list(resolved.get("candidate_sample") or [])
    if resolved.get("fuzzy_total") is not None:
        subject["fuzzy_total"] = resolved.get("fuzzy_total")
    message = "输入未唯一确认到特定企业；已停止主体专属数据调用，不生成该企业的经营、风险或舆情评价。请使用候选企业全称或企业标识重试。"
    report["abstract"] = message
    report["summary"] = message
    report["executive_summary"] = [message]
    report["metrics"] = []
    report["representative_records"] = []
    report["insights"] = [{"feature": "主体确认受限", "evidence": subject["resolve_reason"], "interpretation": message}]
    report.setdefault("data_source", {})["subject_resolution"] = "confirmation_required"
    return report


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


def _text_or_dash(value: Any) -> str:
    text = _text(value)
    return text if text else "-"


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_call(tool: str, arguments: Dict[str, Any]) -> Any:
    try:
        result = call_tool_once(mcp_client.call_tool, tool, arguments)
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
        return {"keyword": "", "enterprise": "", "resolved": False, "requires_confirmation": True, "reason": "关键词为空", "candidate_sample": []}
    # A full legal-name-looking input is already an explicit subject selection.
    if any(suffix in raw for suffix in ("公司", "有限", "院", "厂", "中心", "事务所", "合作社", "合伙")):
        return {"keyword": raw, "enterprise": raw, "resolved": True, "requires_confirmation": False, "reason": "用户提供企业全称", "candidate_sample": []}

    fuzzy = _safe_call(T_FUZZY, {"matchKeyword": raw, "pageSize": 10})
    records = [item for item in _first_list(fuzzy) if isinstance(item, dict)]
    total = _int(_safe_total(fuzzy))
    id_keys = ("nameId", "id", "enterpriseId", "regNumber", "socialCreditCode")
    exact = [
        item for item in records
        if raw == str(item.get("name") or "").strip()
        or any(raw == str(item.get(key) or "").strip() for key in id_keys)
    ]
    if len(exact) == 1:
        record = exact[0]
        name = str(record.get("name") or raw).strip()
        return {"keyword": raw, "enterprise": name, "resolved": True, "requires_confirmation": False, "reason": "与候选企业全称或标识精确匹配", "fuzzy_total": total, "record": record, "candidate_sample": []}
    if len(records) == 1 and total == 1:
        record = records[0]
        name = str(record.get("name") or "").strip()
        if name:
            return {"keyword": raw, "enterprise": name, "resolved": True, "requires_confirmation": False, "reason": "模糊查询唯一命中", "fuzzy_total": total, "record": record, "candidate_sample": []}

    candidates = []
    for item in records[:5]:
        candidate = {"name": str(item.get("name") or "").strip()}
        for key in id_keys:
            if item.get(key) not in (None, ""):
                candidate[key] = str(item.get(key))
        if candidate.get("name") or len(candidate) > 1:
            candidates.append(candidate)
    if candidates:
        reason = f"模糊查询命中 {total if total is not None else '多个'} 个候选，未唯一确认主体"
    else:
        reason = "模糊查询未唯一命中企业，需提供企业全称或企业标识"
    return {
        "keyword": raw, "enterprise": raw, "resolved": False, "requires_confirmation": True,
        "reason": reason, "fuzzy_total": total, "candidate_sample": candidates,
    }



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
        return _text(val) if val not in (None, "") else "-"


def _enrich_metrics_with_profile(metrics: List[Dict[str, Any]], record: Any) -> List[Dict[str, Any]]:
    """Append enterprise profile metrics from a fuzzy_search record."""
    if not isinstance(record, dict):
        return metrics
    _prof = _extract_profile(record)
    if _prof.get("reg_capital") not in ("-", "", None):
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
        try:
            total_usd = sum(float(r.get("出口金额(美元)", 0) or 0) for r in trends if r.get("出口金额(美元)"))
            if total_usd > 0:
                if total_usd >= 1e6:
                    metrics.append({"label": "已返回年度出口额合计", "value": f"${total_usd/1e6:.2f}M", "hint": "按已返回年度金额相加（美元），可能含当前未完整年度；不等于海关全量"})
                else:
                    metrics.append({"label": "已返回年度出口额合计", "value": f"${total_usd/1e3:.0f}K", "hint": "按已返回年度金额相加（美元），可能含当前未完整年度；不等于海关全量"})
        except (ValueError, TypeError):
            pass
    if isinstance(products, list) and products:
        specific = [r for r in products if isinstance(r, dict) and not _is_unclassified_product(r.get("HS 商品编码"))]
        unclassified = len(products) - len(specific)
        hint = "具体HS商品编码数（不含“其他/未分类”）" + (f"；另有 {unclassified} 条未分类记录" if unclassified else "")
        metrics.append({"label": "具体HS编码数", "value": str(len(specific)), "hint": hint})
    if isinstance(regions, list) and regions:
        metrics.append({"label": "纳入分析地区数", "value": str(len(regions)), "hint": "resultList 归纳的地区明细行数"})
        try:
            nums = [int(r.get("订单数", 0) or 0) for r in regions]
            total = sum(nums)
            if total > 0:
                top3 = sum(sorted(nums, reverse=True)[:3])
                metrics.append({"label": "地区CR3", "value": f"{top3/total*100:.1f}%", "hint": "仅按返回样本行计算的前3地区订单占比"})
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

    metrics.append({"label": "出口年度数（已返回）", "value": (str(trend_year_count) + " 年" if trend_year_count else "-"), "hint": "出口趋势中出现的年度数；不保证连续，也不等于完整经营年限"})
    metrics.append({"label": "累计外贸招聘", "value": (_text(recruit_total) if recruit_total is not None else "-"), "hint": "历史累计外贸相关岗位招聘总数（recruitingCount）"})
    metrics.append({"label": "近三月外贸招聘", "value": (_text(recruit_3m) if recruit_3m is not None else "-"), "hint": "近三个月外贸相关岗位招聘数"})
    metrics.append({"label": "出口地区总数", "value": (_text(regions_total) if regions_total is not None else "-"), "hint": "上游响应 total，可能大于纳入明细条数（仅当采集中断时）"})
    metrics.append({"label": "海外认证数", "value": (_text(cert_count) if cert_count is not None else "-"), "hint": "海外认证证书总数"})
    return [m for m in metrics if m.get("value") not in ("", None, "-")]


def _trend_rows(trends: Any) -> List[Dict[str, Any]]:
    out = []
    for item in _first_list((trends if isinstance(trends, dict) else {}).get("exportTrend")):
        if not isinstance(item, dict):
            continue
        count = item.get("count") if item.get("count") is not None else item.get("value")
        out.append({
            "年份": _text_or_dash(item.get("year")),
            "出口金额(美元)": _text(item.get("amount")) if item.get("amount") is not None else "-",
            "订单数量": _text_or_dash(count),
        })
    return sorted(out, key=lambda row: _int(row.get("年份")) or -1)


def _product_label(code: Any) -> str:
    text = _text(code)
    if not text or text == "-":
        return "未分类（上游未返回编码）"
    if text in {"其他", "未分类", "未知"}:
        return "其他（未分类，非具体HS编码）"
    return text


def _is_unclassified_product(code: Any) -> bool:
    text = _text(code)
    return text.startswith(("其他", "未分类")) or text in {"-", ""}


def _product_trend_rows(product_trends: Any) -> List[Dict[str, Any]]:
    out = []
    p = product_trends if isinstance(product_trends, dict) else {}
    dist = p.get("exportGoodsOrderDistribution")
    for item in _first_list(dist):
        if not isinstance(item, dict):
            continue
        out.append({
            "HS 商品编码": _product_label(item.get("hsCode")),
            "订单数量": (_text(item.get("count")) if item.get("count") is not None
                       else _text(item.get("value")) if item.get("value") is not None else "-"),
        })
    try:
        out.sort(key=lambda row: int(str(row.get("订单数量", "0")).replace(",", "")), reverse=True)
    except (TypeError, ValueError):
        pass
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
            "国家/地区": _text_or_dash(item.get("destCountry")),
            "订单数": _text(item.get("count")) if item.get("count") is not None else "-",
            "金额(美元)": _text(item.get("amount")) if item.get("amount") is not None else "-",
            "净重(千克)": _text(item.get("weight")) if item.get("weight") is not None else "-",
        })
    try:
        out.sort(key=lambda row: int(str(row.get("订单数", "0")).replace(",", "")), reverse=True)
    except (TypeError, ValueError):
        pass
    return out


def _cert_rows(certs: Any) -> List[Dict[str, Any]]:
    out = []
    c = certs if isinstance(certs, dict) else {}
    for item in _first_list(c.get("certList") if isinstance(c.get("certList"), list) else c):
        if not isinstance(item, dict):
            continue
        out.append({
            "认证类别": _text_or_dash(item.get("certType")),
            "认证领域": _text_or_dash(item.get("certDomain")),
            "证书号": _text_or_dash(item.get("certId")),
            "产品名称": _text_or_dash(item.get("certProductName")),
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
         "note": (f"上游总数 {_text(regions_total)} 个地区；本次归纳 {len(region_rows)} 条" if regions_total is not None
                  else f"上游未返回地区总数；本次归纳 {len(region_rows)} 条"),
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
            "国家/地区": item.get("国家/地区") if item.get("国家/地区") not in (None, "") else "-",
            "订单数": item.get("订单数") if item.get("订单数") not in (None, "") else "-",
            "金额(美元)": item.get("金额(美元)") if item.get("金额(美元)") not in (None, "") else "-",
        })
    return out[:20]


def _trend_analysis(rows: List[Mapping[str, Any]], series_key: str, period_key: str = "年份") -> Dict[str, Any]:
    """Compute trend direction, peak, and YoY change for a numeric series in trend rows."""
    points: List[tuple[int, float]] = []
    for r in rows:
        try:
            raw = r.get(series_key)
            if raw in (None, "", "-"):
                continue
            year = _int(r.get(period_key))
            if year is None:
                continue
            points.append((year, float(str(raw).replace(",", ""))))
        except (TypeError, ValueError):
            continue
    if not points:
        return {}
    points.sort(key=lambda item: item[0])
    if all(value == 0 for _, value in points):
        return {"all_zero": True, "incomplete_latest": any(year == dt.datetime.now().year for year, _ in points), "yoy": "", "comparison_periods": []}
    peak_period, peak_value = max(points, key=lambda item: item[1])
    direction = "无法同比（缺少相邻完整年度）"
    yoy = ""
    current_year = dt.datetime.now().year
    incomplete_latest = any(year == current_year for year, _ in points)
    complete = [(year, value) for year, value in points if year < current_year]
    comparison_periods: List[str] = []
    if len(complete) >= 2 and complete[-1][0] - complete[-2][0] == 1:
        prev_year, prev = complete[-2]
        last_year, last = complete[-1]
        comparison_periods = [str(prev_year), str(last_year)]
        if prev > 0:
            pct = (last - prev) / prev * 100
            direction = "持平"
            if pct > 5:
                direction = f"上升 {pct:.0f}%"
            elif pct < -5:
                direction = f"下降 {abs(pct):.0f}%"
            yoy = f"同比 {pct:+.0f}%"
    elif incomplete_latest:
        direction = "未比较（当前年度未完整，且缺少相邻完整年度）"
    return {"peak_period": str(peak_period), "peak_value": peak_value, "direction": direction, "yoy": yoy, "last": points[-1][1], "incomplete_latest": incomplete_latest, "comparison_periods": comparison_periods, "all_zero": False}


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


def build_insights(subject: Mapping[str, Any], core: Mapping[str, Any], metrics: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    insights: List[Dict[str, Any]] = []
    metric_map = {m["label"]: str(m["value"]) for m in metrics}
    recruit_3m = metric_map.get("近三月外贸招聘")
    regions_total = metric_map.get("出口地区总数")
    cert_count = metric_map.get("海外认证数")

    def _trend_clause(ta: Mapping[str, Any], unit: str, prefix: str) -> str:
        if not ta:
            return ""
        if ta.get("all_zero"):
            return "全部为上游原值 0，金额字段覆盖口径未验证，不据此判断规模"
        peak = f"{ta.get('peak_value', 0):.0f} {unit}"
        peak_label = "当前年度 YTD 峰值（未完整）" if ta.get("incomplete_latest") and str(ta.get("peak_period")) == str(dt.datetime.now().year) else "峰值"
        direction = str(ta.get("direction") or "")
        periods = ta.get("comparison_periods") or []
        if periods and len(periods) == 2 and direction:
            direction = f"最近两个完整年度 {periods[0]}→{periods[1]} {direction}"
        return f"{prefix}{peak_label}在“{ta.get('peak_period')}”（{peak}），{direction}"

    trend_rows = core.get("export_trends") or []
    if trend_rows:
        amt_ta = _trend_analysis(trend_rows, "出口金额(美元)")
        cnt_ta = _trend_analysis(trend_rows, "订单数量")
        ev_parts = []
        if amt_ta.get("all_zero"):
            ev_parts.append("出口金额均为上游原值 0；金额字段覆盖口径未验证，不据此判断出口规模或价格变化")
        elif amt_ta:
            ev_parts.append(_trend_clause(amt_ta, "美元", "金额"))
        if cnt_ta:
            ev_parts.append(_trend_clause(cnt_ta, "笔", "订单量"))
        years = sorted({_int(r.get("年份")) for r in trend_rows if _int(r.get("年份")) is not None})
        if len(years) >= 2:
            gaps = [str(y) for y in range(years[0], years[-1] + 1) if y not in years]
            if gaps:
                ev_parts.append(f"年度序列不连续，缺少 {'、'.join(gaps)} 年记录")
        if amt_ta.get("incomplete_latest") or cnt_ta.get("incomplete_latest"):
            ev_parts.append(f"{dt.datetime.now().year} 年为未完整年度（YTD），不与完整年度直接同比")
        insights.append({
            "feature": "出口活跃度",
            "evidence": f"出口趋势覆盖 {len(years)} 个年度。" + ("；".join(ev_parts) + "。" if ev_parts else ""),
            "interpretation": "年度金额与订单数仅按各自字段描述；缺年、低样本和当前年度 YTD 均会削弱趋势可比性，不能据此外推未来出口表现。",
        })
    product_rows = core.get("export_product_trends") or []
    if product_rows:
        conc = _concentration(product_rows, "HS 商品编码", "订单数量", 3)
        if conc:
            top_label = str(conc["top"])
            if _is_unclassified_product(top_label):
                specific_rows = [r for r in product_rows if not _is_unclassified_product(r.get("HS 商品编码"))]
                specific = _concentration(specific_rows, "HS 商品编码", "订单数量", 3) if specific_rows else {}
                if specific:
                    evidence = (
                        f"“{top_label}”占订单约 {conc['top_share']:.0f}%，说明 HS 编码覆盖不足，不能识别核心品类；"
                        f"在可识别的具体编码中，“{specific['top']}”占可识别样本约 {specific['top_share']:.0f}%（前 3 类 {specific['cr']:.0f}%）。"
                    )
                else:
                    evidence = f"“{top_label}”占订单约 {conc['top_share']:.0f}%，全部记录均未提供可识别 HS 编码。"
                insights.append({
                    "feature": "出口品类分类缺口",
                    "evidence": evidence,
                    "interpretation": "“其他/未分类”是分类缺口而非商品类别；不能把它当作核心出口品类，也不能据此判断产品竞争力或市场份额。",
                })
            else:
                insights.append({
                    "feature": "核心出口品类",
                    "evidence": f"“{top_label}”为订单最多的品类，占比约 {conc['top_share']:.0f}%，前 3 类合计 {conc['cr']:.0f}%（CR3）。",
                    "interpretation": "HS 编码占比仅描述当前返回样本中订单数量的分布与集中程度，不代表产品竞争力、售价或市场份额。",
                })
    region_rows = core.get("order_regions") or []
    if region_rows:
        conc = _concentration(region_rows, "国家/地区", "订单数", 3)
        if conc:
            insights.append({
                "feature": "出口市场集中度",
                "evidence": f"本次归纳样本中，“{conc['top']}”订单数最多，占比约 {conc['top_share']:.0f}%，前 3 地区合计 {conc['cr']:.0f}%（CR3）。",
                "interpretation": "地区占比仅描述当前返回订单样本的分布，不能据此判断企业整体市场结构、政策风险或订单可持续性。",
            })
    if recruit_3m:
        insights.append({
            "feature": "外贸招聘记录",
            "evidence": f"近三个月外贸相关岗位招聘 {recruit_3m} 人。",
            "interpretation": "该值只说明历史招聘数据中出现外贸相关岗位，不能单独证明出口增长、产能或新市场开拓。",
        })
    # 外贸职能结构（跨境电商信号检测）
    positions = core.get("recruitment_positions") or []
    if positions:
        cross_border_signals = [p for p in positions if p in ("外贸员", "运营", "美工", "精品开发", "亚马逊运营", "Shopee运营", "独立站运营")]
        signal_text = "、".join(cross_border_signals) if cross_border_signals else ""
        is_cb = len(cross_border_signals) >= 2
        evidence = f"招聘职能覆盖：{'、'.join(positions[:8])}。"
        if is_cb:
            evidence += f"其中 {signal_text} 等岗位同时出现。"
            interp = "岗位名称仅构成招聘职能线索；没有渠道或业务数据时，不能据此认定跨境电商团队，也不能推断 Amazon、Shopee 或独立站业务。"
        else:
            interp = "岗位名称仅描述招聘样本中的职能标签，不能单独推断国际化业务组织方式。"
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
                "feature": "招聘城市样本分布",
                "evidence": f"返回的招聘城市字段涉及 {total_c} 个城市，按城市群聚类：{shares}，样本数最高为“{top_region[0]}”。",
                "interpretation": "该分布仅描述当前返回样本中的城市字段，不代表企业整体人才布局、运营重心、业务类型或供应链安排。",
            })
    if regions_total:
        insights.append({
            "feature": "出口地区覆盖记录",
            "evidence": f"出口订单分布 {regions_total} 个国家/地区。",
            "interpretation": "地区数量仅说明上游产品返回的订单地区计数，不能单独评价市场多元化程度或经营稳健性。",
        })
    if cert_count:
        insights.append({
            "feature": "海外认证记录",
            "evidence": f"海外认证共 {cert_count} 项。",
            "interpretation": "认证数量仅描述上游返回记录；需核验认证类型、适用产品、有效期及证书状态，不能据此认定合规或出口能力。",
        })
    if not insights:
        insights.append({
            "feature": "数据完整性",
            "evidence": "部分维度未返回有效数据。",
            "interpretation": "建议核对匹配关键词是否为企业全称，或检查 MCP 连接与上游数据产品覆盖范围。",
        })
    return insights


def _populated_section_titles(core: Mapping[str, Any]) -> List[str]:
    titles: List[str] = []
    sections = core.get("sections")
    if not isinstance(sections, list):
        return titles
    for section in sections:
        if not isinstance(section, dict):
            continue
        key = section.get("key")
        title = section.get("title")
        if isinstance(key, str) and isinstance(title, str) and _has_evidence(core.get(key)):
            titles.append(title)
    return titles


def _populated_section_count(core: Mapping[str, Any]) -> int:
    sections = core.get("sections")
    if isinstance(sections, list) and sections:
        return sum(1 for s in sections if isinstance(s, dict) and _has_evidence(core.get(s.get("key"))))
    return sum(1 for k in core if k != "sections" and _has_evidence(core.get(k)))


def build_abstract(subject: Mapping[str, Any], core: Mapping[str, Any], metrics: Sequence[Mapping[str, Any]]) -> str:
    name = subject.get("enterprise") or subject.get("match_raw") or "目标企业"
    section_titles = _populated_section_titles(core)
    if section_titles:
        parts = [f"本报告以“{name}”为分析对象，基于海关/外贸大数据。本次有效数据仅覆盖：{'、'.join(section_titles)}。"]
    else:
        parts = [f"本报告以“{name}”为分析对象；本次未返回有效海关、外贸或招聘画像章节。"]
    if metrics:
        kv = "、".join(f"{m['label']} {m['value']}" for m in metrics[:5])
        parts.append(f"关键指标包括：{kv}。")
    parts.append("报告仅对已返回字段与当前样本分布作结构化描述；未覆盖维度不作推断，能力、因果与未来表现需另行核验。")
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

def _dedupe_metrics(metrics: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: Dict[str, Dict[str, Any]] = {}
    for item in metrics:
        if not isinstance(item, Mapping):
            continue
        row = dict(item)
        label = str(row.get("label") or "")
        if not label:
            continue
        if label in seen:
            existing = seen[label]
            if row.get("hint") and row.get("hint") not in str(existing.get("hint") or ""):
                existing["hint"] = f"{existing.get('hint')}；{row.get('hint')}".strip("；")
            continue
        seen[label] = row
        out.append(row)
    return out


def _assemble(subject: Mapping[str, Any], core: Mapping[str, Any], metrics: Sequence[Mapping[str, Any]], *, dry_run: bool) -> Dict[str, Any]:
    metrics = _dedupe_metrics(metrics)
    abstract = build_abstract(subject, core, metrics)
    records = build_records(core)
    insights = build_insights(subject, core, metrics)
    # Quality gate: count populated core-analysis sections.
    ca = core if isinstance(core, dict) else {}
    secs = ca.get("sections", [])
    if secs:
        total_secs = len(secs)
        populated = sum(1 for s in secs if isinstance(s, dict) and _has_evidence(ca.get(s.get("key"))))
    else:
        total_secs = max(1, len([k for k in ca if k != "sections"]))
        populated = sum(1 for k in ca if k != "sections" and _has_evidence(ca.get(k)))
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
    report = {
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
    return apply_content_review(report)


@paid_call_scope
def build_payload(raw: str, keyword_type: str, page_size: int, max_pages: int = 1) -> Dict[str, Any]:
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
        raise ValueError("max_pages 必须为正整数")
    resolved = resolve_enterprise_name(raw)
    if resolved.get("requires_confirmation"):
        subject = build_subject(raw, resolved, keyword_type)
        return _apply_resolution_limit(_assemble(subject, {"sections": []}, [], dry_run=False), resolved)
    enterprise = resolved["enterprise"]
    mk_args: Dict[str, Any] = {"matchKeyword": enterprise, "keywordType": keyword_type}

    trends = _safe_call(T_EXPORT_TRENDS, mk_args)
    product_trends = _safe_call(T_EXPORT_PRODUCT_TRENDS, mk_args)
    recruitment = _safe_call(T_RECRUITMENT_PROFILE, mk_args)
    regions = collect_detail_pages(
        _safe_call, T_ORDER_REGIONS,
        {"matchKeyword": enterprise, "keywordType": keyword_type, "pageSize": page_size},
        get_rows=_first_list, get_total=_safe_total, is_error=_is_api_error,
        max_pages=max_pages,
    )
    certs = _safe_call(T_OVERSEAS_CERTS, mk_args)

    subject = build_subject(raw, resolved, keyword_type)
    core = build_core_analysis(trends, product_trends, recruitment, regions, certs)
    metrics = build_metrics(trends, recruitment, regions, certs)
    _derive_core_metrics(metrics, core if isinstance(core, dict) else {})
    # --- Enterprise profile enrichment (from fuzzy_search) ---
    _enrich_metrics_with_profile(metrics, resolved.get("record") if isinstance(resolved, dict) else None)
    report = _assemble(subject, core, metrics, dry_run=False)
    report = _attach_call_status(report, {"出口趋势": trends, "出口产品趋势": product_trends, "外贸招聘": recruitment, "订单地区": regions, "海外认证": certs})
    return attach_pagination(report, {"订单地区": regions})


def _refresh_quality_report(report: Dict[str, Any]) -> Dict[str, Any]:
    """Refresh section-fill metrics after offline review changed sections.

    ``0`` is a populated value; None/""/[]/{} and documented empty sentinels are
    empty. coverage_pct is section fill only, never an accuracy claim.
    """
    core = report.get("core_analysis") or {}
    sections = core.get("sections")
    if isinstance(sections, list) and sections:
        total = len(sections)
        populated = sum(1 for s in sections if isinstance(s, dict) and _has_evidence(core.get(s.get("key"))))
    else:
        keys = [k for k in core if k != "sections"]
        total = max(1, len(keys))
        populated = sum(1 for k in keys if _has_evidence(core.get(k)))
    source = report.setdefault("data_source", {})
    source["quality_report"] = {
        "total_sections": total,
        "populated_sections": populated,
        "empty_sections": total - populated,
        "coverage_pct": round(populated / max(1, total) * 100),
        "metric": "section_fill_rate",
        "note": "章节填充率，仅表示有返回内容的 section 占比，不是内容准确率",
    }
    return report


def _merge_review_records(previous: Any, current: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Keep earlier review records when the same report is processed again."""
    merged: List[Dict[str, Any]] = []
    seen = set()
    for item in list(previous or []) + list(current or []):
        if not isinstance(item, Mapping):
            continue
        row = dict(item)
        key = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        merged.append(row)
    return merged


def apply_content_review(report: Mapping[str, Any], source_evidence: Optional[Any] = None) -> Dict[str, Any]:
    """Rebuild customs narrative from fields already present in a report.

    Pure/offline: no MCP call and no new facts. Repairs deterministic issues:

    * duplicated year cards ("出口年份覆盖" + "出口年份");
    * "其他/未分类" HS rows presented as a real core product category;
    * product counts that included the unclassified bucket;
    * trend statements that ignored missing years or called an incomplete year
      a normal peak ("近年下降 100%" beside a large YTD value).
    """
    out = copy.deepcopy(dict(report))
    corrections: List[Dict[str, Any]] = []
    subject = out.get("subject") or {}
    core = out.get("core_analysis") or {}
    previous_review = (out.get("data_source") or {}).get("content_review") or {}

    # Product rows: label and sort unclassified bucket explicitly.
    product_rows = core.get("export_product_trends") or []
    for row in product_rows:
        if not isinstance(row, dict):
            continue
        before = row.get("HS 商品编码")
        after = _product_label(before)
        if after != before:
            row["HS 商品编码"] = after
            corrections.append({
                "field": "core_analysis.export_product_trends[].HS 商品编码",
                "before": before,
                "after": after,
                "reason": "“其他”不是具体 HS 编码，不能作为核心品类参与分类结论",
            })
    try:
        product_rows.sort(key=lambda row: int(str(row.get("订单数量", "0")).replace(",", "")), reverse=True)
    except (TypeError, ValueError):
        pass

    # Region rows: bar order follows order count.
    region_rows = core.get("order_regions") or []
    try:
        region_rows.sort(key=lambda row: int(str(row.get("订单数", "0")).replace(",", "")), reverse=True)
    except (TypeError, ValueError):
        pass

    # Metrics: dedupe year cards, rename totals, recompute specific-code count.
    raw_metrics = [m for m in (out.get("metrics") or []) if isinstance(m, dict)]
    duplicate_years = [m for m in raw_metrics if m.get("label") == "出口年份"]
    before_metrics = [m for m in raw_metrics if m.get("label") != "出口年份"]
    if duplicate_years:
        corrections.append({
            "field": "metrics[出口年份]",
            "before": f"{len(duplicate_years)} 张“出口年份”卡",
            "after": "移除；保留“出口年度数（已返回）”",
            "reason": "与“出口年份覆盖”同值重复，非两个不同指标",
        })
    for metric in before_metrics:
        label = metric.get("label")
        if label == "出口年份覆盖":
            metric["label"] = "出口年度数（已返回）"
            metric["hint"] = "出口趋势中出现的年度数；不保证连续，也不等于完整经营年限"
            corrections.append({
                "field": "metrics[出口年份覆盖]",
                "before": "出口年份覆盖",
                "after": "出口年度数（已返回）",
                "reason": "与“出口年份”指标同名同值重复；合并为一张卡并说明不保证连续",
            })
        elif label == "累计出口额":
            metric["label"] = "已返回年度出口额合计"
            metric["hint"] = "按已返回年度金额相加（美元），可能含当前未完整年度；不等于海关全量"
            corrections.append({
                "field": "metrics[累计出口额]",
                "before": "累计出口额",
                "after": "已返回年度出口额合计",
                "reason": "原标签暗示完整历史累计，实际只合计已返回年度且可能含 YTD",
            })
        elif label == "出口产品数":
            specific = [r for r in product_rows if not _is_unclassified_product(r.get("HS 商品编码"))]
            unclassified = len(product_rows) - len(specific)
            metric["label"] = "具体HS编码数"
            metric["value"] = str(len(specific))
            metric["hint"] = "具体HS商品编码数（不含“其他/未分类”）" + (f"；另有 {unclassified} 条未分类记录" if unclassified else "")
            corrections.append({
                "field": "metrics[出口产品数]",
                "before": "出口产品数",
                "after": "具体HS编码数",
                "reason": "原计数把“其他/未分类”当成一个产品类别，夸大可识别品类数",
            })
    metrics = _dedupe_metrics(before_metrics)
    if len(metrics) != len(before_metrics):
        corrections.append({
            "field": "metrics",
            "before": f"{len(before_metrics)} 张指标卡",
            "after": f"{len(metrics)} 张指标卡",
            "reason": "“出口年份覆盖/出口年份”为同一计数的重复卡片",
        })
    out["metrics"] = metrics

    if _populated_section_count(core) == 0:
        target = subject.get("enterprise") or subject.get("match_raw") or "目标企业"
        abstract = (
            f"本次对“{target}”的查询未获得有效数据（海关、外贸或招聘画像均为空）；"
            "报告不据此生成出口规模、市场或认证结论。"
        )
    else:
        abstract = build_abstract(subject, core, metrics)
    insights = build_insights(subject, core, metrics)
    out["insights"] = insights
    out["executive_summary"] = [item["interpretation"] for item in insights][:5] or [abstract[:120]]
    out["abstract"] = abstract
    out["summary"] = abstract
    source = out.get("data_source") or {}
    source["content_review"] = {
        "mode": "offline_report_fields_only",
        "source_evidence_supplied": bool(source_evidence),
        "reviewed_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "corrections": _merge_review_records(previous_review.get("corrections"), corrections),
        "not_changed": [
            "未重新查询 MCP；未新增国家、产品、金额或订单事实",
            "未删除“其他/未分类”记录，只禁止把它当具体品类",
            "缺年与未完整年度保留，不插值、不补齐",
        ],
    }
    out["data_source"] = source
    return _refresh_quality_report(out)




def main() -> None:
    parser = argparse.ArgumentParser(description="Compose a customs big-data report via the customs MCP.")
    parser.add_argument("--enterprise", required=True, help="企业全称或关键词（关键词将自动模糊补全）")
    parser.add_argument("--keyword-type", default="name", help="主体类型：name/nameId/regNumber/socialCreditCode")
    parser.add_argument("--page-size", type=int, default=10, help="出口订单地区明细分页大小（最多 50）")
    parser.add_argument("--max-pages", type=int, default=1, help="出口订单地区明细最多付费翻页数（默认 1；仅在明确需要更多结论/证据时增大）")
    parser.add_argument("--dry-run", action="store_true", help="不调用真实 MCP，使用样例数据组装报告骨架")
    parser.add_argument("--output", help="输出 JSON 路径；省略则打印到 stdout")
    parser.add_argument("--report-output", help="同时输出 HTML 报告（.html）与 Markdown 报告（.md）")
    parser.add_argument("--pdf-output", help="额外输出 PDF 报告（.pdf）；需要 Playwright + Chromium")
    args = parser.parse_args()

    if not args.dry_run:
        if args.max_pages is not None and args.max_pages < 1:
            die("--max-pages 必须为正整数")

    if args.dry_run:
        payload = build_dry_run_payload(args.enterprise, args.keyword_type)
    else:
        payload = build_payload(args.enterprise, args.keyword_type, args.page_size, args.max_pages)

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
