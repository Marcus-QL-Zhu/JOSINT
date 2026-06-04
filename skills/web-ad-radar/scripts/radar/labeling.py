from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .models import JobRecord


FUNCTION_LABELS = {
    "VC/PE",
    "保险",
    "banking",
    "销售",
    "市场",
    "财务",
    "人事",
    "法务",
    "行政",
    "研发",
    "生产",
    "供应链",
    "IT",
}

INDUSTRY_LABELS = {
    "机器人",
    "半导体",
    "软件",
    "消费",
    "耐消",
    "专业服务",
    "电商",
    "金融",
    "化工",
    "工业",
}


@dataclass(slots=True)
class JobLabel:
    function_label: str | None
    industry_label: str | None
    confidence: str
    evidence: list[str] = field(default_factory=list)


MAX_LLM_LABEL_BATCH_SIZE = 10


FUNCTION_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("VC/PE", ("vc", "pe", "private equity", "venture capital", "portfolio", "投后", "投资经理", "基金")),
    ("保险", ("insurance", "actuarial", "underwriting", "claim", "reinsurance", "保险", "精算", "核保", "理赔", "银保")),
    ("banking", ("banking", "bank", "kyc", "credit", "trader", "investment banking", "银行", "券商", "资管", "信贷", "交易员")),
    ("销售", ("sales", "business development", "bd", "key account", "account manager", "commercial director", "销售", "客户开发", "商务拓展")),
    ("市场", ("marketing", "brand", "market intelligence", "pr ", "public relations", "市场", "品牌", "公关", "用户运营")),
    ("财务", ("finance", "accounting", "fp&a", "tax", "audit", "cfo", "财务", "会计", "税务", "审计")),
    ("IT", ("it ", "sap", "erp", "infrastructure", "cybersecurity", "data platform", "successfactors", "hris", "企业系统", "信息系统", "信息技术", "网络安全", "运维")),
    ("人事", ("hr", "human resources", "talent acquisition", "recruiter", "c&b", "hrbp", "人事", "招聘", "薪酬", "组织发展")),
    ("法务", ("legal", "compliance", "risk", "ip ", "intellectual property", "法务", "合规", "知识产权", "风控")),
    ("行政", ("admin", "office manager", "secretary", "assistant", "facility", "行政", "秘书", "助理", "前台")),
    ("研发", ("r&d", "research", "engineer", "developer", "scientist", "algorithm", "embedded", "software engineer", "研发", "工程师", "算法", "嵌入式", "产品研发", "运动控制")),
    ("生产", ("plant", "manufacturing", "production", "quality", "ehs", "process", "生产", "制造", "质量", "工艺", "厂长", "设备")),
    ("供应链", ("supply chain", "procurement", "logistics", "sourcing", "buyer", "planning", "供应链", "采购", "物流", "计划", "寻源")),
]

INDUSTRY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("机器人", ("robot", "robotics", "industrial robot", "humanoid", "embodied ai", "机器人", "工业机器人", "协作机器人", "具身智能", "运动控制")),
    ("半导体", ("semiconductor", "wafer", "chip", "eda", "功率器件", "半导体", "芯片", "晶圆", "封测")),
    ("软件", ("software", "saas", "cloud", "cybersecurity", "artificial intelligence", "ai software", "enterprise software", "软件", "云服务", "网络安全", "数据平台")),
    ("消费", ("fmcg", "consumer", "retail", "food", "beverage", "beauty", "消费", "快消", "食品", "饮料", "美妆", "零售")),
    ("耐消", ("automotive", "home appliance", "consumer electronics", "furniture", "汽车", "家电", "消费电子", "家具")),
    ("专业服务", ("consulting", "law firm", "audit firm", "recruitment", "professional service", "咨询", "律所", "会计师事务所", "人力资源服务")),
    ("电商", ("e-commerce", "ecommerce", "cross-border", "直播电商", "电商", "跨境")),
    ("金融", ("financial services", "bank", "insurance", "fund", "asset management", "fintech", "金融", "银行", "保险", "基金", "资管", "支付")),
    ("化工", ("chemical", "chemicals", "material", "coating", "plastic", "industrial gas", "化工", "化学品", "材料", "涂料", "塑料")),
    ("工业", ("industrial", "manufacturing", "machinery", "automation", "equipment", "component", "工业", "机械", "自动化", "设备", "零部件")),
]


def label_job(job: JobRecord) -> JobLabel:
    primary_text = _primary_job_text(job)
    detail_text = _detail_job_text(job)
    function_label, function_evidence = _first_match(primary_text, FUNCTION_RULES)
    if function_label is None:
        function_label, function_evidence = _first_match(detail_text, FUNCTION_RULES)
    industry_label, industry_evidence = _first_match(primary_text, INDUSTRY_RULES)
    if industry_label is None:
        industry_label, industry_evidence = _first_match(detail_text, INDUSTRY_RULES)
    confidence = _confidence(function_evidence, industry_evidence)
    _validate_label(function_label, FUNCTION_LABELS, "function")
    _validate_label(industry_label, INDUSTRY_LABELS, "industry")
    return JobLabel(
        function_label=function_label,
        industry_label=industry_label,
        confidence=confidence,
        evidence=function_evidence + industry_evidence,
    )


def label_jobs(jobs: list[JobRecord], *, minimax: Any | None = None, batch_size: int = MAX_LLM_LABEL_BATCH_SIZE) -> None:
    local_labels: dict[str, JobLabel] = {}
    for job in jobs:
        label = label_job(job)
        local_labels[job.id] = label
        _apply_job_label(job, label)
    if minimax is None:
        return
    llm_candidates = [job for job in jobs if local_labels[job.id].confidence != "high"]
    if not llm_candidates:
        return
    safe_batch_size = min(max(1, batch_size), MAX_LLM_LABEL_BATCH_SIZE)
    for batch in _chunks(llm_candidates, safe_batch_size):
        try:
            labels = _call_llm_labeler(batch, minimax)
        except Exception:
            continue
        for job in batch:
            payload = labels.get(job.id)
            if not payload:
                continue
            label = _label_from_llm_payload(payload)
            if label:
                _apply_job_label(job, label)


def _apply_job_label(job: JobRecord, label: JobLabel) -> None:
    job.function_label = label.function_label
    job.industry_label = label.industry_label
    job.label_confidence = label.confidence
    job.label_evidence = label.evidence


def _primary_job_text(job: JobRecord) -> str:
    return f"{job.title}\n{job.industry or ''}\n{job.function or ''}\n{job.list_excerpt or ''}".lower()


def _detail_job_text(job: JobRecord) -> str:
    text = (job.detail_text or "").lower()
    if "optanonwrapper" in text or text.lstrip().startswith(("function ", "var ")):
        return ""
    return text


def _first_match(text: str, rules: list[tuple[str, tuple[str, ...]]]) -> tuple[str | None, list[str]]:
    for label, keywords in rules:
        hits = [keyword for keyword in keywords if _keyword_in_text(keyword, text)]
        if hits:
            return label, [f"{label}: {', '.join(hits[:3])}"]
    return None, []


def _keyword_in_text(keyword: str, text: str) -> bool:
    normalized = keyword.lower().strip()
    if not normalized:
        return False
    if _contains_cjk(normalized):
        return normalized in text
    if len(normalized) <= 3 and re.fullmatch(r"[a-z0-9&/+-]+", normalized):
        return re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", text) is not None
    return normalized in text


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _confidence(function_evidence: list[str], industry_evidence: list[str]) -> str:
    if function_evidence and industry_evidence:
        return "high"
    if function_evidence or industry_evidence:
        return "medium"
    return "low"


def _validate_label(label: str | None, allowed: set[str], label_type: str) -> None:
    if label is not None and label not in allowed:
        raise ValueError(f"Invalid {label_type} label: {label}")


def _chunks(jobs: list[JobRecord], size: int) -> list[list[JobRecord]]:
    return [jobs[index : index + size] for index in range(0, len(jobs), size)]


def _call_llm_labeler(jobs: list[JobRecord], minimax: Any) -> dict[str, dict[str, Any]]:
    prompt = {
        "task": "为猎头职位广告批量判断职能标签和行业标签。只返回严格 JSON，不要 Markdown。",
        "model_role": "Layer 4 fallback classifier. 本地规则低置信时才调用你。",
        "requirements": [
            "每个职位必须返回同一个 id。",
            "function_label 只能来自 function_labels，无法判断则为 null。",
            "industry_label 只能来自 industry_labels，无法判断则为 null。",
            "不要因为猎头网站页脚、cookie、导航、招聘声明中的 consulting/recruitment/sales 误判行业或职能。",
            "confidence 只能是 high、medium、low。",
            "evidence 用中文简短说明来自标题、结构化字段或 JD 正文的依据。",
        ],
        "function_labels": sorted(FUNCTION_LABELS),
        "industry_labels": sorted(INDUSTRY_LABELS),
        "jobs": [_job_payload(job) for job in jobs],
        "schema": {
            "labels": [
                {
                    "id": "string",
                    "function_label": "one of function_labels or null",
                    "industry_label": "one of industry_labels or null",
                    "confidence": "high|medium|low",
                    "evidence": ["中文依据"],
                }
            ]
        },
    }
    content = minimax.chat(
        [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
        response_format={"type": "json_object"},
        max_completion_tokens=3000,
    )
    parsed = _parse_json_object(content)
    labels = parsed.get("labels", [])
    if not isinstance(labels, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    valid_ids = {job.id for job in jobs}
    for item in labels:
        if isinstance(item, dict) and item.get("id") in valid_ids:
            result[item["id"]] = item
    return result


def _job_payload(job: JobRecord) -> dict[str, str | None]:
    return {
        "id": job.id,
        "source": job.source_name,
        "title": job.title,
        "location": job.location,
        "structured_industry": job.industry,
        "structured_function": job.function,
        "list_excerpt": job.list_excerpt,
        "detail_text": _truncate(_detail_job_text(job), 1600),
    }


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def _label_from_llm_payload(payload: dict[str, Any]) -> JobLabel | None:
    function_label = _allowed_or_none(payload.get("function_label"), FUNCTION_LABELS)
    industry_label = _allowed_or_none(payload.get("industry_label"), INDUSTRY_LABELS)
    if payload.get("function_label") and function_label is None:
        return None
    if payload.get("industry_label") and industry_label is None:
        return None
    confidence = payload.get("confidence") if payload.get("confidence") in {"high", "medium", "low"} else "low"
    evidence = payload.get("evidence")
    if not isinstance(evidence, list):
        evidence = []
    evidence = [str(item) for item in evidence if str(item).strip()]
    evidence.append("Layer 4: MiniMax-M2.7-highspeed")
    return JobLabel(
        function_label=function_label,
        industry_label=industry_label,
        confidence=confidence,
        evidence=evidence,
    )


def _allowed_or_none(value: Any, allowed: set[str]) -> str | None:
    if value in (None, ""):
        return None
    return value if isinstance(value, str) and value in allowed else None


def _parse_json_object(content: str) -> dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))
