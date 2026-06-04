# Source Registry

Enabled sources:

| Slug | Company | Start URL | Notes |
|---|---|---|---|
| `robert-walters` | Robert Walters China | `https://www.robertwalters.cn/jobs.html` | If list extraction fails, try the main site job search URL. |
| `robert-half` | Robert Half China | `https://www.roberthalf.cn/cn/en/jobs` | Site may redirect from find-jobs to jobs. |
| `morgan-philips` | Morgan Philips Mainland China | `https://jobs.morganphilips.cn/en-cn` | Dedicated jobs domain. |
| `morgan-mckinley` | Morgan McKinley Mainland China | `https://www.morganmckinley.com.cn/en/jobs` | China jobs page exposes list text. |
| `hays` | Hays China | `https://www.hays-china.cn/en/jobs/` | Watch for JS-rendered search results. |
| `randstad` | Randstad China | `https://www.randstad.cn/en/jobs/` | Good first source for smoke tests. |
| `rgf` | RGF Professional Recruitment China | `https://www.rgf-professional.com.cn/zh/jobs` | Chinese titles and salary fields common. |
| `persolkelly` | PERSOLKELLY China | `https://www.persolkellycn.com/` | Homepage includes job links; dedicated paths may vary. |

Crawler policy:

- Continue when one source fails.
- Store source errors in the Markdown report.
- Prefer deterministic HTML extraction before LLM cleanup.
- Keep adapter selectors and URL patterns source-local.
