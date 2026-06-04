# Source Registry

Enabled sources:

| Slug | Company | Start URL | Notes |
|---|---|---|---|
| `robert-half` | Robert Half China | `https://www.roberthalf.cn/cn/en/jobs` | Site may redirect from find-jobs to jobs. |
| `morgan-philips` | Morgan Philips Mainland China | `https://jobs.morganphilips.cn/en-cn` | Dedicated jobs domain. |
| `morgan-mckinley` | Morgan McKinley Mainland China | `https://www.morganmckinley.com.cn/en/jobs` | China jobs page exposes list text. |
| `hays` | Hays China | `https://www.hays-china.cn/en/jobs/` | Watch for JS-rendered search results. |
| `randstad` | Randstad China | `https://www.randstad.cn/en/jobs/` | Good first source for smoke tests. |
| `rgf` | RGF Professional Recruitment China | `https://www.rgf-professional.com.cn/zh/jobs` | Chinese titles and salary fields common. |
| `intellipro` | IntelliPro / 英特利普 | `https://intellipro.applytojob.com/` | Official ApplyToJob board. No reliable publish date; crawl 30 visible jobs. |
| `risfond` | Risfond / 锐仕方达 | `https://www.risfond.com/job/` | Official Vue job board backed by `/Services/BaseData.ashx?action=getjoblist`; date-aware via `LastUpdatedStr`. |

Temporarily disabled sources:

| Slug | Company | Reason |
|---|---|---|
| `robert-walters` | Robert Walters China | China jobs entry returned HTTP 403 during crawler verification. |
| `persolkelly` | PERSOLKELLY China | Current feed includes non-job placeholder posts and needs separate cleanup. |
| `imatch` | imatch talent | Official site found, but no public client job board or stable job-detail URLs were found. |
| `cgl` | CGL Consulting / 猎聘系 CGL | Official site exposes adviser/contact pages, not a public client job board; third-party profiles show 0 jobs. |
| `vip-hunter` | VIP-HUNTER | No official public job board found; available evidence is mainly LinkedIn/company profile pages. |
| `bo-le` | Bó Lè Associates / 伯乐 | Official site has internal `workwithus` recruiting pages, but no public client job-ad board found. |

Crawler policy:

- Continue when one source fails.
- Store source errors in the Markdown report.
- Prefer deterministic HTML extraction before LLM cleanup.
- Keep adapter selectors and URL patterns source-local.
- Default daily runs target yesterday. Date-aware sources filter to that date; no-date sources crawl up to 30 visible jobs through pagination.
