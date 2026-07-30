# Employer Inference Playbook

Infer hidden employers from three evidence groups.

## Proprietary Terms

Look for product names, internal systems, role abbreviations, and company-specific operating frameworks.

Treat possible proprietary terms as open-ended clues. Search and validate them
at runtime instead of mapping individual terms to hard-coded employers.

## Company Description Narrowing

Use location, industry, ownership, country of origin, product category, listing status, factory footprint, R&D setup, reporting line, and market-rank claims.

Example: "embodied AI company in Shanghai Pudong" narrows the likely employer set enough to search targeted phrases.

## Cross-Job Joint Analysis

Cluster jobs from the same source when they share location, repeated anonymous descriptions, salary band, team structure, product clues, or recruiter clues.

If multiple jobs each leak one clue about the same hidden employer, the final guess should cite the related roles and explain how the clues combine.

## Confidence

- `high` (`0.80-1.00`): the company description or a distinctive product or technology directly matches, with independent public corroboration or a near-duplicate public JD.
- `medium` (`0.50-0.79`): multiple independent, specific clues point to one employer, but a plausible alternative remains or public verification is incomplete.
- `low` (`0.00-0.49`): evidence is limited to generic location, industry, channel, or common-stack signals; evidence is insufficient; or material conflicts remain.

Signals such as `Shenzhen + 3C + DTC/Shopify` are generic profile matches and cannot by themselves exceed low confidence. A candidate-targeted search result only validates a hypothesis when its content substantively matches the anonymous company description or role; the appearance of the candidate name alone is not corroboration.

## Candidate Flow

1. Metaso chat produces zero to three evidence-backed candidates. Empty or short lists are valid and must not be padded.
2. Each returned candidate is checked independently against public jobs and distinctive company or role clues.
3. MiniMax-M3 compares all candidates, conflicts, public evidence, and related jobs once before returning the final result.
