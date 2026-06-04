from __future__ import annotations

from datetime import date
from pathlib import Path

from .models import RunConfig, SourceConfig


SOURCES: dict[str, SourceConfig] = {
    "robert-walters": SourceConfig("robert-walters", "Robert Walters China", "https://www.robertwalters.cn/"),
    "robert-half": SourceConfig("robert-half", "Robert Half China", "https://www.roberthalf.cn/cn/en/find-jobs"),
    "morgan-philips": SourceConfig("morgan-philips", "Morgan Philips Mainland China", "https://jobs.morganphilips.cn/en-cn"),
    "morgan-mckinley": SourceConfig("morgan-mckinley", "Morgan McKinley Mainland China", "https://www.morganmckinley.com.cn/en/jobs"),
    "hays": SourceConfig("hays", "Hays China", "https://www.hays-china.cn/en/jobs/"),
    "randstad": SourceConfig("randstad", "Randstad China", "https://www.randstad.cn/en/jobs/"),
    "rgf": SourceConfig("rgf", "RGF Professional Recruitment China", "https://www.rgf-professional.com.cn/zh/jobs"),
    "persolkelly": SourceConfig("persolkelly", "PERSOLKELLY China", "https://www.persolkellycn.com/"),
}

ALIASES = {
    "rw": "robert-walters",
    "robertwalters": "robert-walters",
    "roberthalf": "robert-half",
    "morganphilips": "morgan-philips",
    "mmk": "morgan-mckinley",
    "morganmckinley": "morgan-mckinley",
    "persol": "persolkelly",
}


def resolve_companies(value: str | None) -> list[str]:
    if not value:
        return list(SOURCES)
    resolved: list[str] = []
    for item in value.split(","):
        slug = item.strip().lower().replace("_", "-")
        slug = ALIASES.get(slug, slug)
        if slug not in SOURCES:
            choices = ", ".join(SOURCES)
            raise ValueError(f"Unknown competitor '{item}'. Available choices: {choices}")
        if slug not in resolved:
            resolved.append(slug)
    return resolved


def _resolve_under_workspace(workspace: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (workspace / path).resolve()


def build_run_config(
    *,
    workspace: Path,
    env_path: Path,
    output_dir: Path,
    data_dir: Path,
    companies: str | None,
    crawl_only: bool,
    date_from: str | None,
    date_to: str | None,
) -> RunConfig:
    workspace = workspace.resolve()
    today = date.today().isoformat()
    return RunConfig(
        workspace=workspace,
        env_path=_resolve_under_workspace(workspace, env_path),
        output_dir=_resolve_under_workspace(workspace, output_dir),
        data_dir=_resolve_under_workspace(workspace, data_dir),
        companies=resolve_companies(companies),
        crawl_only=crawl_only,
        date_from=date_from or today,
        date_to=date_to or date_from or today,
    )
