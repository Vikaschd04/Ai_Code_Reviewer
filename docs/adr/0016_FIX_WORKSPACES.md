# ADR 0016 — Fix workspaces: change sets, re-checks, comparison and exports

Status: accepted. Slices 1–5 are implemented and verified:
- change sets, editor and comparison, bulk fixes, re-check and exports;
- AI candidates, verified with the labelled test model; live quality needs the owner's AI key;
- one pull request per checked workspace, verified against the labelled fake GitHub. Date: 8 October 2026. Owner: repository owner (requirements of
8 October 2026; prompts/P08_FIX_WORKSPACE.md). Implemented by the development agent.

Context and constraints:

- Reviewers must be able to fix many issues inside refactorX and take the result away without Git
  hosting:
  - by hand, with recipes and later with AI;
  - by downloading a patch, only the changed files or the full project;
  - after checking the result and comparing it with the upload.
- P05 fixes are single-finding, single-file proposals. Real fixing touches several files and mixes
  manual and automatic changes.
- The upload is evidence: it must never change, and every result must name the exact content it
  describes.
- Hiding a problem (suppression markers, skipped or weakened tests) must never look like fixing it.
- No project code may run outside an isolated sandbox. Compilation is P09; until then the product
  says "not compiled".
- The free profile has no Temporal and one process. Checks must run there too.

Decision:

1. **Change set ("workspace") bound to one base snapshot.** Tables:
   - `change_sets`: project, base snapshot, optional pinned base scan, title, `content_sha256`,
     `version` used as the ORM version column;
   - `change_set_files`: one row per changed path, holding:
     - the action: modify, add or delete;
     - the upload's hash and the current revision's hash;
     - size, lines and language;
     - policy flags and provenance sources;
   - `change_set_events`: an append-only history with:
     - the source: manual, recipe, ai, revert or export;
     - the path, action, findings, recipe and flags;
     - the revision hash and the workspace digest after the event;
   - `change_set_checks`.

   Revisions are content-addressed blobs in the existing artifact store. Uploads are only read.
   Saving the uploaded content again removes the path from the workspace.
2. **One digest binds everything.** `content_sha256` is the SHA-256 of the canonical JSON of
   (path, action, revision hash) for every changed file, with a digest for the empty workspace
   too. Checks store the digest they checked (`current` = same as now) and so do export events.
   The derived state is:
   - `checking` while a check runs;
   - `exported` when the newest event is a download of the current content;
   - `ready` when a successful check matches the current content;
   - `draft` otherwise.
3. **Editing rules.**
   - Paths are canonicalised like intake paths; `.git/` and directories are refused.
   - Binary, oversized, excluded and non-UTF-8 files are not editable. Text is decoded strictly, so
     a save can never write replacement characters.
   - Browser edits arrive with LF line endings. A file uploaded with CRLF keeps CRLF on save.
   - Unicode and byte order marks round-trip exactly.
   - Limits: the per-file text limit (413) and `CRP_CHANGE_SET_MAX_FILES` (default 500, 409
     `workspace_full`).
   - Concurrent edits use optimistic versions (409 `version_conflict`). A stale save is refused,
     never merged.
4. **Two policies on the same P05 checks.**
   - Manual edits may legitimately change configuration (for example a dependency upgrade). Their
     flags are information: configuration change, suppression added, test weakened.
   - Recipe and AI edits keep the strict P05 policy and are skipped with the reason when they
     would suppress, weaken tests, exceed the size limit or touch an unsafe path.
   - The policy now also treats newly skipped or focused tests as weakened tests:
     - `it.skip`, `xit`, `describe.only`;
     - `@Disabled`, `@Ignore`;
     - `pytest.mark.skip` and similar.
5. **Bulk recipes on changed text.** A recipe is computed against the upload (its finding's
   evidence) and applied to the workspace's current text:
   - first at the exact position;
   - otherwise where the same original lines appear exactly once nearby (`relocate`);
   - otherwise it is skipped as "changed in this workspace".

   It is never guessed or merged. "All occurrences of a rule" selects every finding of that rule
   in the upload's review. Every applied fix is an event with its finding and recipe.
6. **Re-check = a derived snapshot plus a normal scan.**
   - The check freezes the file list it checks (digest included).
   - It builds a derived snapshot: the upload's manifest with the workspace revisions applied. A
     workspace file whose path the scope policy excludes, for example `.env`, is excluded exactly
     as at upload.
   - The derived snapshot is reused when the same digest was checked before.
   - It runs a `change_set` scan of the whole snapshot. Unchanged files reuse the per-file engine
     cache, so new findings in unchanged files caused by configuration edits are still found.
   - It compares the result with the upload's review using the P06 finding diff.

   Outcomes per upload finding:
   - `still_present`;
   - `fixed`: the finding is absent and its file was rechecked by the same engine;
   - `suppressed`: the finding is absent but its file gained a suppression marker. It is never
     counted as fixed;
   - `not_rechecked`.

   New findings are reported as well. The result says `compiled: false` and "Source-level checks
   only".

   Isolation: derived snapshots carry `derived_from` and `change_set_id` and are hidden from upload
   lists, project counts and AI defaults. `change_set` scans never change issues; only `baseline`
   scans do (ADR 0015). Temporal runs `ChangeSetCheckWorkflow` with child scan workflows. The lite
   profile runs the same activities in process and resumes queued checks after a restart.
7. **Exports name their base and refuse to pretend.** Formats:
   - Patch (`git apply -p1`): a git diff with new-file and deleted-file modes and executable bits
     from the capture. A `#` header names the workspace, upload, manifest hash and content hash.
   - Mailbox commit (`git am`): author refactorX, RFC 2047 subject, the same hashes in the body.
   - Changed-files ZIP: exactly the changed files at their paths, plus `refactorx-changes.json`
     (deleted files listed) and the patch.
   - Full-project ZIP: the stored files byte for byte (any encoding) with the edits applied, and a
     `refactorx-not-included.txt` listing files that were never stored (binary, oversized,
     excluded).
   - Summary: JSON `crp-change-set-export/v1` with its own schema, and Markdown.

   ZIPs are built off the event loop into temporary files that are removed after sending. Entries
   keep file modes and one timestamp. Every download records an event. The patch fails on code
   that changed since the upload (tested with `git apply --check`).
8. **Comparison.**
   - Any two uploads or commits of one project, compared on stored text files only: added,
     modified, removed and renamed (identical content).
   - Per-file before and after text, with plain notes for files that are new, removed or not
     stored.
   - The UI uses CodeMirror 6 with `@codemirror/merge`: side by side or inline, unchanged regions
     collapsed, word-level highlights. Code is always inert text.
9. **UI.**
   - Pages: the project tab "Fix workspaces" and the workspace page (`#/workspaces/:id`) with three
     tabs:
     - Issues: check card, issue queue with filters and bulk actions;
     - Changes: changed files, comparison, history;
     - Edit: file search, editor, issues in this file.
   - Entry points: "Open in workspace" on the finding page and "Compare" on the upload page.
   - The editor and grammars load lazily: the main bundle is unchanged, and a grammar loads per
     language on demand.
   - Packages: CodeMirror packages are MIT. Transitive versions are pinned with
     `pnpm-workspace.yaml` overrides to releases that were public for at least two weeks when
     adopted. Two packages published the day before adoption were deliberately not taken.

10. **AI candidates (slice 4) are suggestions, never changes.**
    - A person asks for them per issue. The request is refused unless:
      - the project's AI switch is on;
      - the server has a provider with monthly budget left;
      - the finding has a line;
      - its file is editable UTF-8 text in the workspace.
    - The run (`ai_runs.kind = fix`) records the workspace and the SHA-256 of the exact text the
      candidates are made for. The workspace link is `SET NULL`, so usage accounting survives
      deletion.
    - The model sees the current file and submits whole-line edits for that file only.
    - Every candidate is checked like a recipe fix: exact lines, strict policy, and the P05
      ladder with the trusted engines.
    - Hiding candidates, invented lines, ones that keep the problem and ones that add a new
      one are shown with the reason but cannot be applied.
    - Applying re-checks the policy. It applies at the same lines or where they moved, and
      records an `ai` change with the finding.
    - A suggestion is applied at most once; one that no longer matches is stale.
    - Budgets stop honestly (`BUDGET_EXHAUSTED`, nothing invented). Source text, including
      instruction files, stays fenced, labelled data.

11. **One pull request per checked workspace content (slice 5).**
    - When the upload is a capture of a connected GitHub repository, a member can open a pull
      request.
    - Preconditions: the P06 publication rules (an admin allowed pull requests, not a fork,
      active installation, same repository), a current successful workspace check, and the
      reviewed branch still at the reviewed commit (`stale_patch` otherwise).
    - It is one commit on that commit with every change: blobs for added and changed files,
      null tree entries for deletions, and the executable bit from the capture. It is pushed
      to `refactorx/workspace-<id>-<digest>`.
    - The body lists each file with its provenance and notes (hiding markers, weakened tests,
      configuration), the check counts and "not compiled, built or tested".
    - The same content returns the same pull request; new content needs a new check and opens
      a new one. It is recorded in `change_set_pull_requests` (migration 0011) and as a
      delivery event. Nothing is ever merged.
    - The fix and workspace pull requests share one target function
      (`services/git.publish_target`).

Consequences:

- Users can fix, check, compare and take away many fixes without Git. GitHub stays optional;
  for connected repositories a checked workspace becomes one pull request.
- Checks are source-level. Compile, type and test results arrive with P09's sandbox. Until then
  the UI, exports and summaries say "not compiled, built or tested".
- Each check adds a derived snapshot and two scans. They are deleted with the workspace or the
  project. Unchanged files cost little because of the engine cache.
- A workspace pins one upload. Moving edits to a newer upload works only per P05 fix (rebase); a
  workspace rebase is backlog (P08-F1).
- Not yet available:
  - ignore-whitespace comparison;
  - three-way merges with a newer upload;
  - non-UTF-8 editing;
  - measured AI candidate quality (needs the owner's key).

Alternatives considered:

- Monaco editor (MIT): heavier, worse mobile support, harder to theme with our tokens.
- Editing P05 proposals in place: one file per proposal cannot express multi-file fixes or manual
  work.
- Storing whole patched trees per save: wasteful. Per-file content-addressed revisions plus a
  derived manifest give the same result.
- Counting any vanished finding as fixed: rejected. Suppression must stay visible.
