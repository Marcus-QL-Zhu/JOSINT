from __future__ import annotations

import re
from dataclasses import dataclass

from .models import JobRecord


SENIOR = (
    "director", "head", "vice president", "vp", "managing director", "md",
    "general manager", "gm", "chief", "ceo", "cto", "coo", "cmo", "总监",
    "副总裁", "总经理", "首席",
)
CONDITIONAL_SENIOR = ("负责人",)
LEADERSHIP_SCOPE = (
    "团队管理", "团队搭建", "组建团队", "部门负责人", "全面负责",
    "预算", "p&l", "损益", "绩效管理", "招聘决策", "向ceo汇报",
    "向cto汇报", "向总经理汇报",
)
TOO_JUNIOR = ("manager", "经理", "专家", "specialist", "associate", "assistant", "助理", "专员", "实习")
INDUSTRIES = (
    "robot", "robotics", "humanoid", "embodied", "机器人", "具身智能",
    "semiconductor", "wafer", "chip", "eda", "半导体", "芯片", "晶圆", "封测",
    "artificial intelligence", "generative ai", "genai", "machine learning", "llm",
    "人工智能", "大模型", "生成式ai", "算法",
    "consumer electronics", "smart hardware", "wearable", "消费电子", "智能硬件", "可穿戴",
)
FUNCTIONS = (
    "r&d", "research", "engineering", "algorithm", "software", "hardware", "embedded",
    "product", "sales", "business development", "commercial", "marketing", "market",
    "operations", "strategy", "研发", "开发", "算法", "软件", "硬件", "产品", "销售",
    "商务", "商业化", "市场", "运营",
)
EXCLUDED = (
    "human resources", "talent acquisition", "recruiter", "hrbp", "人力资源", "人事", "招聘", "薪酬",
    "finance", "accounting", "tax", "audit", "财务", "会计", "税务", "审计",
    "legal", "compliance", "法务", "合规", "admin", "administration", "行政", "秘书",
    "procurement", "sourcing", "采购", "寻源", "customer service", "客服",
)
AI_ADOPTION = (
    "ai transformation", "ai strategy", "ai adoption", "enterprise ai", "genai lead",
    "ai转型", "ai落地", "人工智能负责人", "大模型负责人", "智能化负责人",
)


@dataclass(frozen=True, slots=True)
class TargetDecision:
    is_target: bool
    reason: str
    seniority_label: str | None = None


def evaluate_target(job: JobRecord) -> TargetDecision:
    title = _text(job.title, job.raw_title)
    function_text = _text(
        title, job.function, job.function_label, job.list_excerpt,
    )
    all_text = _text(
        title, job.industry, job.industry_label, job.function, job.function_label,
        job.list_excerpt, job.jd_text, job.company_description,
    )
    if _contains(title, TOO_JUNIOR) and not _contains(title, SENIOR):
        return TargetDecision(False, "低于总监级")
    conditional_senior = (
        _contains(title, CONDITIONAL_SENIOR)
        and sum(1 for term in LEADERSHIP_SCOPE if term in all_text) >= 2
    )
    if not _contains(title, SENIOR) and not conditional_senior:
        return TargetDecision(False, "未识别到总监级或以上头衔")
    if _contains(function_text, EXCLUDED):
        return TargetDecision(False, "属于排除的支持职能", "director+")
    if _contains(all_text, AI_ADOPTION):
        return TargetDecision(True, "非科技企业 AI 落地/转型领导岗位", "director+")
    if not _contains(all_text, INDUSTRIES):
        return TargetDecision(False, "未命中目标行业", "director+")
    if not _contains(all_text, FUNCTIONS):
        return TargetDecision(False, "未命中目标业务职能", "director+")
    return TargetDecision(True, "总监级以上，且命中目标行业与业务职能", "director+")


def _text(*values: str | None) -> str:
    return "\n".join(value or "" for value in values).lower()


def _contains(text: str, keywords: tuple[str, ...]) -> bool:
    for keyword in keywords:
        normalized = keyword.lower()
        if any("\u4e00" <= char <= "\u9fff" for char in normalized):
            if normalized in text:
                return True
        elif re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", text):
            return True
    return False
