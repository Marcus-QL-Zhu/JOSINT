#!/usr/bin/env python3
"""Feishu IM notification helper for JOSINT."""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any
import urllib.request


log = logging.getLogger(__name__)

FEISHU_IM_BASE = "https://open.feishu.cn/open-apis"


def _post(url: str, body: dict[str, Any], token: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def get_tenant_token(app_id: str, app_secret: str) -> str:
    """Get a fresh tenant_access_token."""
    url = f"{FEISHU_IM_BASE}/auth/v3/tenant_access_token/internal"
    resp = _post(url, {"app_id": app_id, "app_secret": app_secret}, token="")
    if resp.get("code") != 0:
        raise RuntimeError(f"Failed to get token: {resp}")
    return resp["tenant_access_token"]


def send_text(open_id: str, text: str, app_id: str, app_secret: str) -> str:
    """Send a text message to a Feishu user by open_id."""
    token = get_tenant_token(app_id, app_secret)
    url = f"{FEISHU_IM_BASE}/im/v1/messages?receive_id_type=open_id"
    body = {
        "receive_id": open_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    resp = _post(url, body, token)
    if resp.get("code") != 0:
        raise RuntimeError(f"Failed to send IM: {resp}")
    return resp.get("data", {}).get("message_id", "")


def format_daily_summary(
    *,
    run_date: str,
    sync_stats: dict[str, int],
    total_local_jobs: int,
    high_confidence: list[dict[str, Any]],
    low_confidence_count: int,
    pending_count: int,
    industry_counts: Counter,
    function_counts: Counter,
    top_employers: Counter,
    bitable_url: str,
    failed_sources: list[str] | None = None,
) -> str:
    """Render a compact Chinese daily digest message."""
    failed_section = ""
    if failed_sources:
        failed_section = f"\n\n**抓取失败**: {', '.join(failed_sources)}"

    lines = [
        f"**JOSINT | {run_date} 抓取汇总**",
        "",
        f"**今日抓取**: 共 **{sync_stats.get('scanned', 0)}** 个职位",
        f"- 新增: **{sync_stats.get('new', 0)}**",
        f"- 已存在/更新: **{sync_stats.get('updated', 0)}**",
        f"- 同步失败: **{sync_stats.get('failed', 0)}**",
        f"- 本地库总数: **{total_local_jobs}**",
        failed_section,
        "",
        "**子集分析**: 机器人/AI/研发/半导体/人事相关职位",
        f"- 命中子集: **{len(high_confidence) + low_confidence_count + pending_count}**",
        f"- 高置信度 (>=0.7): **{len(high_confidence)}**",
        f"- 低置信度: **{low_confidence_count}**",
        f"- 分析失败/跳过: **{pending_count}**",
        "",
        "**招聘趋势**",
    ]

    if industry_counts:
        top_ind = ", ".join(f"{k} ({v})" for k, v in industry_counts.most_common(5))
        lines.append(f"- 行业 Top 5: {top_ind}")
    if function_counts:
        top_func = ", ".join(f"{k} ({v})" for k, v in function_counts.most_common(5))
        lines.append(f"- 职能 Top 5: {top_func}")
    if top_employers:
        top_emp = ", ".join(f"{k} ({v})" for k, v in top_employers.most_common(5))
        lines.append(f"- 热门雇主猜测: {top_emp}")

    if high_confidence:
        lines.extend(["", "**高置信度雇主分析**"])
        for i, item in enumerate(high_confidence[:10], 1):
            lines.append(
                f"{i}. **{item.get('title', '?')}** @ {item.get('source', '?')} "
                f"(confidence {item.get('confidence', 0):.2f})"
            )
            lines.append(f"   雇主: {item.get('employer', '?')}")
            if item.get("reasoning"):
                lines.append(f"   依据: {item['reasoning']}")
            if item.get("url"):
                lines.append(f"   链接: {item['url']}")
            lines.append("")

    lines.append(f"[完整数据] {bitable_url}")
    return "\n".join(lines)


def format_failure_message(
    *,
    run_date: str,
    stage: str,
    error: str,
    log_path: str | None = None,
) -> str:
    """Render a Chinese failure notification."""
    parts = [
        f"**JOSINT | {run_date} 抓取失败**",
        "",
        f"**阶段**: {stage}",
        f"**错误**: {error}",
    ]
    if log_path:
        parts.extend(["", f"日志: `{log_path}`"])
    return "\n".join(parts)
