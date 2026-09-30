# Labelled AI review evaluation set

Small, synthetic, repository-owned code units used to measure refactorX's AI review
(`crp-dev ai-eval`). Nothing here comes from a customer. The code is deliberately short, so
results say nothing about whole-repository review.

## Layout

`<split>/<case-id>/case.json` describes one case; `files/` holds the code exactly as the model
sees it (paths are relative to the upload root).

| Field | Meaning |
|---|---|
| `split` | `dev` (may be looked at while changing prompts or tools) or `held_out` (never used for tuning; report it separately) |
| `task` | `file_review`: review `review_paths` for problems the automatic checks may miss |
| `defective` | `true` when the unit contains at least one labelled defect, `false` for a clean twin |
| `labels` | Each known defect: path, 1-based line range, category and a one-sentence description |

Set: 8 `dev` cases (6 defective, 2 clean) and 4 `held_out` cases (3 defective, 1 clean) in Java,
Python, JavaScript and TypeScript.

## Review criteria (fixed before any model run)

Labels were written from the defect definitions, not from model output. Scoring:

1. Only AI findings whose citations are not **rejected** by the deterministic anchor check count
   as reported findings. Rejected findings are counted separately.
2. A reported finding **matches** a label when one of its anchors is in the same file and its
   line range overlaps the label's range widened by 2 lines on each side. Category is not
   required to match; category agreement is reported separately.
3. **Precision** = reported findings that match a label / all reported findings.
4. **Labelled recall** = labels matched by at least one reported finding / all labels. Defects
   that are real but unlabelled count as false positives here; review them by hand before
   changing a label, and record any label change in the git history.
5. **False alarms on clean cases** = reported findings on `defective: false` cases.
6. **Anchor validity** = anchors with status `verified` / all anchors.
7. Tokens are the provider's reported usage (flagged when estimated). Cost is shown only when
   the operator configured prices; no prices are assumed.

`crp-dev ai-eval` without `--live` runs the pipeline against the labelled fake test provider.
That checks plumbing and scoring only: its numbers are **not** model quality. Model quality needs
`--live` with an approved provider configuration (`CRP_AI_*`); it sends only these synthetic
files.
