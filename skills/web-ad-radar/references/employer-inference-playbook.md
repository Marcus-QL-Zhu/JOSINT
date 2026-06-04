# Employer Inference Playbook

Infer hidden employers from three evidence groups.

## Proprietary Terms

Look for product names, internal systems, role abbreviations, and company-specific operating frameworks.

Examples:

- `HOS` suggests Honeywell Operating System.
- `FDE` suggests Palantir Forward Deployed Engineer.

These terms should trigger Metaso searches and MiniMax-M3 reasoning.

## Company Description Narrowing

Use location, industry, ownership, country of origin, product category, listing status, factory footprint, R&D setup, reporting line, and market-rank claims.

Example: "embodied AI company in Shanghai Pudong" narrows the likely employer set enough to search targeted phrases.

## Cross-Job Joint Analysis

Cluster jobs from the same source when they share location, repeated anonymous descriptions, salary band, team structure, product clues, or recruiter clues.

If multiple jobs each leak one clue about the same hidden employer, the final guess should cite the related roles and explain how the clues combine.

## Confidence

- `high`: proprietary term or multiple independent clues point to one employer.
- `medium`: strong description match but plausible alternatives remain.
- `low`: generic JD, failed APIs, or weak evidence.
