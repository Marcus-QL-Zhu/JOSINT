from __future__ import annotations

from .models import EmployerGuess, JobRecord


def render_report(
    *,
    report_date: str,
    scope: str,
    mode: str,
    jobs: list[JobRecord],
    guesses: dict[str, EmployerGuess],
    source_errors: list[str],
) -> str:
    high_confidence = sum(1 for guess in guesses.values() if guess.confidence.lower() == "high")
    needs_review = sum(1 for guess in guesses.values() if guess.review_flags)
    lines = [
        f"# 职位广告雷达报告 - {report_date}",
        "",
        f"范围: {scope}",
        f"模式: {_mode_label(mode)}",
        "",
        "## 执行摘要",
        "",
        f"- 爬取职位数: {len(jobs)}",
        f"- 已分析职位数: {len(guesses)}",
        f"- 高置信度雇主猜测: {high_confidence}",
        f"- 需要人工复核: {needs_review}",
    ]
    if source_errors:
        lines.append(f"- 爬取失败公司数: {len(source_errors)}")
    lines.extend(
        [
            "",
            "## 职位列表",
            "",
            "| 发布方 | 职位名称 | 职能标签 | 行业标签 | 标签置信度 | 地点 | 日期 | URL | 雇主猜测 | 置信度 |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for job in jobs:
        guess = guesses.get(job.id)
        employer = guess.guessed_employer if guess and guess.guessed_employer else ""
        confidence = guess.confidence if guess else ""
        date_value = job.published_at or job.updated_at or job.first_seen_at or ""
        lines.append(
            f"| {job.source_name} | {job.title} | {job.function_label or ''} | {job.industry_label or ''} | {job.label_confidence or ''} | {job.location or ''} | {date_value} | {job.url} | {employer} | {confidence} |"
        )
    if guesses:
        lines.extend(["", "## 雇主猜测详情", ""])
        for job in jobs:
            guess = guesses.get(job.id)
            if not guess:
                continue
            lines.extend(
                [
                    f"### {job.title}",
                    "",
                    f"- 发布方: {job.source_name}",
                    f"- URL: {job.url}",
                    f"- 雇主猜测: {guess.guessed_employer or '未知'}",
                    f"- 置信度: {guess.confidence}",
                    "- 证据:",
                ]
            )
            for item in guess.evidence:
                text = item.get("text") or item.get("summary") or str(item)
                lines.append(f"  - {text}")
            lines.extend(["- 推理摘要:", f"  - {guess.reasoning_summary or '无可用推理摘要。'}"])
            if guess.review_flags:
                lines.append("- 复核提示:")
                for flag in guess.review_flags:
                    lines.append(f"  - {flag}")
            lines.append("")
    if source_errors:
        lines.extend(["## 爬取失败公司", ""])
        for error in source_errors:
            lines.append(f"- {error}")
    return "\n".join(lines).rstrip() + "\n"


def _mode_label(mode: str) -> str:
    return {"crawl only": "仅爬取", "crawl + analysis": "爬取 + 雇主分析"}.get(mode, mode)
