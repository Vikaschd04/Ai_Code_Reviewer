# Architecture smell evaluation set (P10 slice 3)

Synthetic, hand-labelled projects for the structural smells (ADR 0020): each smell has positive
and negative cases in Java, TypeScript, an SAP Commerce extension (with generated `gensrc`) and
Salesforce Lightning Web Components. `labels.json` lists, per case, every expected finding as
`(rule, path)`; anything else reported is a false positive.

Run `uv run crp-dev arch-eval` to analyze each case with the real dependency extraction and
compute precision and recall per smell. The set is synthetic: it measures that the detectors do
what they are specified to do, not how often real projects need the warnings (that needs
expert-labelled real projects, not yet available).
