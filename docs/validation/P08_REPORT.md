# Phase P08 validation report — Fix workspaces

Date: 8 October 2026. Environment: macOS arm64 (Darwin 25.5), Python 3.14 (uv), Node 22 / pnpm 11.20.0,
PostgreSQL 18.6, Temporal CLI dev server, PMD 7.27.0, ESLint 10.11.0, Opengrep 1.30.0, Trivy 0.69.3
(offline DB). Decision record: [ADR 0016](../adr/0016_FIX_WORKSPACES.md).

Status: **IN_PROGRESS.** Slices 1–3 are delivered and every mandatory check that applies to them
passes:
- change-set core and exports;
- editor and comparison;
- bulk fixes and re-check.

Open:
- Slice 4, AI fix candidates (K-P08-01). It is built next against the labelled fake model; its live
  quality needs the owner's key (K-P03-01).
- Slice 5, the change-set pull request (K-P08-02).

Compile results depend on P09 and are shown as "not compiled".

## Deliverables (P08 prompt)

| Deliverable | Delivered | Where |
|---|---|---|
| 1. Change sets bound to one base snapshot; provenance; states; optimistic versions; upload never modified | Delivered: `change_sets`, `change_set_files`, `change_set_events` and `change_set_checks` (migration 0009). Revisions are content-addressed blobs. Events record manual, recipe, revert and export changes with findings and recipe. Derived state: draft / checking / ready / exported. Concurrency uses version 409s | `crp_api/services/change_sets.py`, `routes/change_sets.py`, `crp_core/db/models.py` |
| 2. Editing in the portal (accessible editor, Java/JS/TS/XML/JSON/Apex, binary refused; policy on every save; add and delete files) | Delivered: CodeMirror 6 (MIT, lazily loaded, grammars on demand) with search, keyboard save, line jump and an aria label. Manual flags are information; recipe edits use the strict policy. The policy now also catches skipped or focused tests. Binary, oversized, excluded and non-UTF-8 files are refused. CRLF and Unicode are kept. Properties files show as plain text | `components/CodeEditor.tsx`, `pages/WorkspacePage.tsx`, `fixes/changeset.py`, `fixes/policy.py` |
| 3. Fixing issues in bulk (recipes incl. "all occurrences"; AI; by hand at the line; conflicts never merged; budgets) | Partial. Recipes on a selection or a whole rule, applied on the current text exactly or where the same lines are; otherwise skipped with the reason. "Edit" opens the editor at the line. Limits: files per workspace (500), file size. **AI candidates not yet** (slice 4) | `routes/change_sets.py` (`/fixes`), `fixes/changeset.apply_on_current` |
| 4. Re-check on a derived snapshot with per-file reuse; fixed / still / not rechecked / new; bound to the content hash; "not compiled" | Delivered: derived snapshot reused per digest, a `change_set` scan of the whole snapshot with the engine cache, and the P06 finding diff. Hiding is reported as `suppressed`, never `fixed`. Runs on Temporal (`ChangeSetCheckWorkflow`) and the lite runner (resumes after restart) | `crp_worker/change_set.py`, `comparison.py`, `inline.py` |
| 5. Compare (side by side and inline, collapsed regions, word highlights, inert; any two uploads) | Delivered: per-file comparison in the workspace and between two uploads (added / changed / removed / moved). **Not yet:** ignore-whitespace (P08-F2) | `CompareView`, `pages/CompareUploadsView.tsx`, `/v1/snapshots/{id}/compare` |
| 6. Export (patch for `git apply` / `git am`; changed-files ZIP + manifest; full ZIP; JSON + Markdown summary; PR; base named, exact-base warning) | Delivered: all formats except the pull request (slice 5). Summary schema `crp-change-set-export/v1`. ZIPs are built off the event loop with file modes; unchanged files are copied byte for byte | `routes/change_sets.py` (`/export`), `schemas/crp-change-set-export-v1.schema.json` |
| 7. UI (queue with filters, file list with markers, editor, compare, check results, export; finding page link; plain language; light/dark/mobile) | Delivered: project "Fix workspaces" tab, workspace page (Issues, Changes, Edit), "Open in workspace" on findings, "Compare" on uploads | `apps/web/src/pages/*`, UI_SPEC "Implemented in P08" |

## Mandatory tests (P08 prompt)

| Check | Procedure | Outcome | Evidence |
|---|---|---|---|
| Export correctness | A multi-file change set covering a modified, a CRLF, an added (path with a space) and a deleted file. `git apply --check` is run on the exact base and on a changed copy. The changed-files ZIP is listed. The full ZIP is compared with base + edits | PASS. The patch applies on the exact base and the result matches byte for byte; the changed copy fails. The changed-files ZIP holds exactly the changed files plus the manifest (deleted files listed, schema-valid) and the patch. The full ZIP equals base + edits, without the deleted file, with a "not included" list | `test_change_sets_api.py::test_exports_apply_only_to_the_exact_upload`, `test_changeset.py::test_patch_applies_with_git_only_on_the_exact_base` |
| `git am` | Mailbox export applied with `git am` to a repository of the exact upload | PASS. One commit with the workspace title (UTF-8 subject); the deletion is applied | `test_git_am_commit_and_workspace_states` |
| Provenance and honesty | Manual, recipe and revert events; suppression edit; weakened and skipped tests | PASS. Sources and events are recorded with their findings. A suppression is flagged, and the vanished finding is `suppressed`, never fixed. Weakened, skipped and focused tests are flagged; recipes refuse them | `test_edit_files_with_flags_conflicts_and_line_endings`, `test_bulk_recipe_fixes_issue_queue_and_conflicts`, `test_p08_workspace.py`, `test_fixes.py` policy cases, `test_changeset.py` |
| Conflicts and limits | Stale version; a hand edit on a recipe's lines; ambiguous lines; binary, oversized and non-UTF-8 files; the files-per-workspace limit; CRLF and Unicode | PASS. 409 `version_conflict`. The recipe is skipped as "changed in this workspace", and an ambiguous relocation is a conflict. Binary and non-UTF-8 files return 409 `not_editable`, an oversized file 413, a full workspace 409 `workspace_full`. CRLF is kept and a BOM, emoji and CJK round-trip | `test_edit_files…`, `test_bulk…`, `test_encodings_unicode_and_limits`, `test_recipe_edits_apply_on_changed_text_or_conflict` |
| Re-check | Real engines on the seeded fixture, with a bulk recipe, a suppression with a real fix, and a new `eval` | PASS. The target finding and the same-line PMD finding are `fixed`; the NaN finding is `suppressed`; the other InvoiceService findings are `still_present`; new problems include `app.js`. The digest is current. A second check of the same content reuses the derived copy with identical outcomes | `test_p08_workspace.py` (Temporal and lite) |
| Configuration edits and unchanged files | The whole derived snapshot is scanned (unchanged files from the cache), so new findings in unchanged files are found | PASS by design and the full-snapshot scan in the worker test. A dedicated dependency-upgrade fixture is not yet added (Trivy DB offline) | `change_set.py` (`prepare` builds the full manifest) |
| AI | Off when the policy is off; labelled; validated; budgets; injection stays data | **NOT DONE** (slice 4, K-P08-01) | — |
| Safety | Upload digests and the original folder; isolation for other workspaces; inert rendering | PASS. Upload blob digests and file rows are unchanged; the uploaded folder digest is unchanged. Upload list, review list, overview counts and issues are unchanged after checks. The demo workspace gets 404 on every workspace, file, issue, export, check and compare route. Code renders as CodeMirror text, and the prompt-injection `AGENTS.md` shows as plain text in the comparison | `test_edit_files…`, `test_p08_workspace.py`, `test_other_workspaces_see_nothing`, `p08-compare-uploads.png` |
| Real stack | API, worker on Temporal and lite, and the browser journey: select issues → bulk fix → hand edit → re-check → compare → download the patch and changed files → compare two uploads | PASS (AI step pending slice 4) | `test_change_sets_api.py` (10), `test_p08_workspace.py` (2), `e2e/p08-workspace.spec.ts` |

## Checks

| Check | Command | Outcome |
|---|---|---|
| Lint, format, types and contracts | `make check` | exit 0 |
| Types for Linux | `uv run mypy --platform linux` | no issues in 172 source files |
| Tests | `make test` | exit 0. 485 pytest; P08 added 34: changeset helpers 18, policy 4, API 10, real-stack worker 2. 29 vitest; P08 added 5: router 3, editor 2 |
| Browser E2E | `make test-e2e` | exit 0: 19/19 (P08 workspace journey added; foundation, P01–P06 and UI tour unchanged) |
| Screens | `p08-workspace.png`, `p08-workspace-dark.png`, `p08-workspace-mobile.png`, `p08-editor.png`, `p08-compare.png`, `p08-compare-mobile.png`, `p08-compare-uploads.png` | Reviewed. Plain language with technical details collapsed. On phones, the issue table shows severity, result and Edit under the title. The compare gutters align once the viewer is in view. No horizontal page scroll at 390 px |
| Bundle | `pnpm build` | Main bundle unchanged (440 kB). Editor chunk 347 kB (112 kB gzip) and grammars (2–92 kB) load only on workspace and compare pages |
| Supply chain | `npm view` license and publish time for all 23 new packages | All MIT. Pinned with overrides to releases that were public for at least two weeks. `@codemirror/language` 6.13.x (and its new dependency `@codemirror/streamparser`), `@lezer/java` 1.1.5 and `@lezer/lr` 1.4.11, all published on 7 October 2026, were not adopted |

## Findings during the phase

- **Policy gap:** skipped or focused tests (`it.skip`, `xit`, `describe.only`, `@Disabled`, `@Ignore`, `pytest.mark.skip`) were not treated as weakened tests. Now flagged and refused for automatic fixes; P05 fixes benefit too.
- **Data-safety gap:** stored text was decoded with replacement characters, so saving a Latin-1 file would have corrupted it, and the full-project ZIP would have re-encoded such files. Editing now decodes strictly (non-UTF-8 is refused with a plain reason), and the full ZIP copies unchanged files byte for byte.
- **UI:** after a save, the editor remounted with stale content and lost the policy flags. It now stays mounted across saves and remounts only on freshly loaded content. CRLF files no longer look edited when unchanged.
- **Same-line findings** are correctly reported as fixed together (PMD `CompareObjectsWithEquals` disappears with `UseEqualsToCompareStrings`).

## Limitations

- AI candidates (slice 4) and change-set pull requests (slice 5) are not built yet (K-P08-01, K-P08-02).
- Checks are source-level: nothing is compiled, built or tested (K-P08-03, P09).
- Not yet available:
  - editing non-UTF-8 files (K-P08-04);
  - moving a workspace to a newer upload (K-P08-05);
  - ignore-whitespace comparison (K-P08-06).
- Verified on macOS arm64 only. Linux runs in CI (`make check` / `make test` there per the workflow).
