from __future__ import annotations

from typing import Callable

from .base import SourceAdapter


class RobertWaltersAdapter(SourceAdapter):
    slug = "robert-walters"
    name = "Robert Walters China"
    start_url = "https://www.robertwalters.cn/jobs.html"
    include_url_patterns = ("/jobs/", "/job/")


class RobertHalfAdapter(SourceAdapter):
    slug = "robert-half"
    name = "Robert Half China"
    start_url = "https://www.roberthalf.cn/cn/en/jobs"
    include_url_patterns = ("/cn/en/job/", "/jobs/")


class MorganPhilipsAdapter(SourceAdapter):
    slug = "morgan-philips"
    name = "Morgan Philips Mainland China"
    start_url = "https://jobs.morganphilips.cn/en-cn/jobs-in-shanghai"
    include_url_patterns = ("/en-cn/", "/zh-cn/")
    exclude_url_patterns = SourceAdapter.exclude_url_patterns + ("/jobs-in-", "/shortlist", "?")
    job_url_regex = r"/(?:en-cn|zh-cn)/[^/?#]+-\d+/?$"
    detail_xpaths = ("/html/body/div[2]/div[1]/div[2]/div[2]/div[2]/div[1]/div/div/div/div/div",)


class MorganMcKinleyAdapter(SourceAdapter):
    slug = "morgan-mckinley"
    name = "Morgan McKinley Mainland China"
    start_url = "https://www.morganmckinley.com.cn/en/jobs"
    include_url_patterns = ("/en/job/", "/zh/job/", "/en/jobs/", "/zh-hans/jobs/")
    exclude_url_patterns = SourceAdapter.exclude_url_patterns + ("/jobs/discipline/", "/jobs/send-us-your-cv")
    job_url_regex = r"/(?:en|zh-hans)/jobs/[^/]+/[^/]+/\d+/?$|/(?:en|zh)/job/[^/?#]+/?$"


class HaysAdapter(SourceAdapter):
    slug = "hays"
    name = "Hays China"
    start_url = "https://careers.hays-china.cn/jobs"
    include_url_patterns = ("/jobs/",)
    exclude_url_patterns = SourceAdapter.exclude_url_patterns + ("/jobs?page=", "/zh-cn/jobs")
    job_url_regex = r"(?:^\.?/jobs/|/jobs/)[^?/#]+-\d+/?$"


class RandstadAdapter(SourceAdapter):
    slug = "randstad"
    name = "Randstad China"
    start_url = "https://www.randstad.cn/en/jobs/"
    include_url_patterns = ("/jobs/",)
    exclude_url_patterns = SourceAdapter.exclude_url_patterns + ("/jobs/s-", "/jobs/q-")
    job_url_regex = r"/jobs/.+_\d+_(?:en|cn)/?$|/jobs/.+_\d+/?$"


class RgfAdapter(SourceAdapter):
    slug = "rgf"
    name = "RGF Professional Recruitment China"
    start_url = "https://www.rgf-professional.com.cn/zh/jobs"
    include_url_patterns = ("/zh/jobs/", "/en/jobs/", "/job/")
    exclude_url_patterns = SourceAdapter.exclude_url_patterns + ("/jobs/location/", "/jobs/industry/", "/jobs/function/")


class PersolkellyAdapter(SourceAdapter):
    slug = "persolkelly"
    name = "PERSOLKELLY China"
    start_url = "https://persolchina.career.gllue.com/jobs"
    include_url_patterns = ("/job/", "/jobs/", "/zh-hans/job/")
    exclude_url_patterns = SourceAdapter.exclude_url_patterns + ("/jobs?page=", "/zh-cn/jobs")
    job_url_regex = r"(?:^\.?/jobs/|/jobs/)[^?/#]+-\d+/?$"
    prefer_title_from_url = True


ADAPTERS: dict[str, type[SourceAdapter]] = {
    cls.slug: cls
    for cls in (
        RobertWaltersAdapter,
        RobertHalfAdapter,
        MorganPhilipsAdapter,
        MorganMcKinleyAdapter,
        HaysAdapter,
        RandstadAdapter,
        RgfAdapter,
        PersolkellyAdapter,
    )
}


def build_adapters(slugs: list[str], fetch: Callable[[str], str] | None = None) -> list[SourceAdapter]:
    return [ADAPTERS[slug](fetch=fetch) for slug in slugs if slug in ADAPTERS]
