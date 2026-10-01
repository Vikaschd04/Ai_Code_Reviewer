# Phase P06 validation report — GitHub, incremental reviews and publication

- Date: 1 October 2026. Actor: Claude Code development agent (Opus 5.5), single agent.
- Status: **BLOCKED only on the live connector check.** Every deliverable is implemented, and
  every mandatory check passes against the labelled fake GitHub
  (`crp_devtools.testing.fake_github`). The fake is a real HTTP server with real Git object ids,
  `git archive` semantics, RS256 app authentication and token scoping. No real GitHub App,
  installation or repository has been used: the prompt requires a controlled, authorized test
  repository, which the owner has not provided yet (K-P06-01).
- Design: [ADR 0015](../adr/0015_GITHUB_REVIEWS.md). Setup and runbook: [GITHUB.md](../GITHUB.md).
- Environment: macOS 26.5.2 arm64, Python 3.14.3, Node 22.22.0, PostgreSQL 18.6, Temporal CLI
  1.9.1; engines as in P05_REPORT; `cryptography` 50.0.2.
- GitHub REST behaviour was checked against GitHub Docs on 1 October 2026 (links in ADR 0015). API
  version `2026-03-10` is pinned; its breaking changes do not touch the endpoints used.

## Deliverables (P06 prompt)

| Deliverable | Delivered | Where |
|---|---|---|
| 1. Least-privilege GitHub integration; read and publish separate; revocation | One operator App. Installation tokens are limited to one repository and to read, checks or fix permissions, and live only in memory. Linking is verified (a forged `installation_id` is ignored; one-time state bound to the admin). Uninstall, suspend and removal are handled from webhooks and from GitHub's answers; unlinking and disconnecting are available | `crp_analysis/sources/github.py`, `routes/github.py` |
| 2. Signed webhooks, replay and idempotency, immutable captures; no repository hooks or config | HMAC check before parsing, size limit, unique delivery ids. Captures are GitHub's archive of an exact commit, reconciled with the commit tree: `export-ignore` cannot hide files, `export-subst` and CRLF are corrected, symlinks, submodules and LFS are excluded. No Git runs | `routes/github.py`, `sources/capture.py`, `zip_intake.GitArchiveOptions` |
| 3. Baseline and pull request scans at merge base and head; history, renames, deletions, submodules and LFS | Default-branch reviews compare with the previously reviewed commit; pull requests with the merge base (`reference` scan). Change sets come from manifests. Renamed findings are not new; deleted files are "not rechecked". The submodule and LFS policy is recorded per capture | `crp_worker/git_review.py`, `sources/changes.py` |
| 4. Dependency- and configuration-aware scope and cache invalidation; scheduled full reconciliation | Per-file cache by content, rules and config. Cross-file engines (dependencies, frameworks, graph) always cover the whole snapshot. Configuration changes and graph dependents are listed. Full re-checks run hourly when due (default every 7 days) in both runtimes | `git_review.py` (`reconcile_due`), `runtime.py`, `inline.py` |
| 5. Checks, comments and human-authorized fix pull requests; duplicate updates; exact-head freshness | Publication is off by default per project (admin-only, audited). One check per commit with up to 50 annotations, and one comment updated in place. Fix pull requests need a validated fix and a person's click; the branch head must equal the reviewed commit (`stale_patch` otherwise). A single commit, never merged, idempotent | `git_review.publish`, `sources/publication.py`, `routes/fixes.py` |
| 6. Git metadata beside snapshot identity; lineage without resolving not-rechecked issues | Snapshots show provider, repository, ref, commit, tree and the capture check. Only `baseline` scans change issues (not pull request, reference or superseded ones). Exact renames move issues (event `moved`) | `lifecycle.py`, `routes/snapshots.py`, UI |

## Mandatory checks (P06 prompt)

| Check | Procedure | Outcome | Evidence |
|---|---|---|---|
| Invalid webhook signature | Forged and missing `X-Hub-Signature-256`; malformed delivery id; oversized body | PASS: 401/400/413; nothing recorded | `test_github_api.py::test_webhooks_need_a_valid_signature_and_are_idempotent`, `test_p06_github.py::test_pull_request_review_compares_with_the_merge_base` |
| Duplicate and out-of-order events | Same delivery twice; newer push delivered before the older one | PASS: second delivery `duplicate`; the late older event ends SKIPPED `already_reviewed`; nobody reviews the older commit | `test_events_converge_on_the_newest_commit`, API test |
| Revoked access | Installation deleted on GitHub without notice; uninstall webhook; suspended installation | PASS: review FAILED `installation_not_found` (a revoked cached token is replaced and the 404 surfaces), installation marked revoked, later reviews refused (409), active reviews canceled | `test_partial_scans_outages_and_revoked_access`, `test_revoked_and_suspended_installations`, `test_connections_are_admin_managed_and_scoped` |
| Forked pull request trust boundary | Fork pull request webhook; manual review; fix on fork code | PASS: ignored by default (plain reason); a member's manual review runs (`fork: true`); fix pull request refused ("comes from a fork") | `test_fix_pull_requests_forks_and_stale_patches`, API test |
| Concurrent pushes | A push arrives while the previous push's review is scanning (PMD held) | PASS: older review SUPERSEDED, its scan CANCELED and never applied to issues; the newest commit SUCCEEDED | `test_a_newer_push_supersedes_a_running_review` |
| Stale patch | The branch moves after a fix was validated | PASS: 409 `stale_patch` with reviewed and current commits; nothing created on GitHub | `test_fix_pull_requests_forks_and_stale_patches` |
| Changed merge base | Pull request rebased onto an advanced `main` | PASS: new review with the new merge base; the base-branch fix is not credited to the pull request | `test_a_changed_merge_base_is_followed` |
| Deleted and renamed files | Rename (identical), deletion and modification in one pull request | PASS: change set lists each; only the modified line's findings are new; the renamed file's are unchanged; deleted findings "not rechecked", never "fixed"; issues untouched | `test_pull_request_review_compares_with_the_merge_base`, `test_findings_in_renamed_files_are_not_new` |
| Configuration-only change | Pull request changes only `pom.xml` (log4j-core 2.14.1) | PASS: new dependency findings (Trivy) in `pom.xml`; configuration listed; unchanged files' per-file results reused (cache hits > 0) | `test_a_configuration_only_change_reaches_dependency_checks` |
| Failed partial scan | PMD crashes on every review | PASS: reviews PARTIAL listing `pmd`; published check `neutral` (never `success`) with "Not every check finished: pmd"; nothing marked fixed | `test_partial_scans_outages_and_revoked_access` |
| Unavailable provider | GitHub answers 503 repeatedly | PASS: bounded retries, then review FAILED `github_unavailable`; a later review works | same; `test_outages_are_retried_then_reported` |
| Regenerate and revalidate when the patch base changes | Stale fix | PASS: refused; P05 "move to another upload" + re-check remains the path (one-step automation is P06-F5) | as above |
| No leaks across repositories or workspaces | Token for repo A reading repo B; other workspaces and the demo account on connections and reviews | PASS: 404 everywhere; installations bound to one workspace; caches project-scoped | `test_tokens_are_scoped_to_one_repository_and_the_needed_permissions`, `test_connections_are_admin_managed_and_scoped`, `test_linking_is_verified_and_bound_to_the_admin` |
| Capture equals the commit | `export-ignore`, `export-subst`, `eol=crlf`, symlink, submodule, LFS pointer, executable; fetch limit; archive with a file outside the commit | PASS: analyzable files byte-identical to the commit; exclusions with reasons; limits honest; mismatch rejected | `test_capture_*`, `test_connected_repository_is_reviewed_exactly_as_committed` |
| Publication | Check, annotations, comment; a new commit on the pull request; failing threshold | PASS: one check per head; `failure` only at threshold; comment updated in place (one comment); Markdown escaped; 50-annotation cap | `test_publication_posts_one_check_and_keeps_one_comment`, `test_check_conclusions_never_overstate`, `test_published_text_escapes_repository_content_and_caps_annotations` |
| Scheduled full reconciliation | Last full review older than `reconcile_days` | PASS: one full review queued (`trigger: reconcile`), none while pending | `test_push_reviews_and_publication_on_the_lite_profile` |
| Lite profile (free hosting) | Push review, publication and reconciliation in-process | PASS | `test_push_reviews_and_publication_on_the_lite_profile` |
| Non-Git regression | Existing upload, scan, issue, AI and fix suites | PASS | `make test`, `make test-e2e` below |
| Live connector on real GitHub | Controlled, authorized test repository | **BLOCKED**: needs the owner's GitHub App and test repository (K-P06-01) | Live checklist below |

## Checks

| Check | Command | Outcome |
|---|---|---|
| Lint/format/types/contracts | `make check` | exit 0 |
| Tests | `make test` | exit 0: 451 pytest (P06: 31 — GitHub sources 18, API boundaries 3, real-stack worker 10) and 24 vitest (GitHub components 3, router 1 added) |
| Types for Linux | `uv run mypy --platform linux` | no issues in 165 source files |
| Browser E2E | `make test-e2e` | exit 0: 18/18 (foundation 7, P01 2, P02 2, P03 1, P04 2, P05 1, P06 1, UI tour 2) |
| Screens | `p06-github-page.png`, `p06-project-github.png`, `p06-review.png`, `p06-review-dark.png`, `p06-review-mobile.png` (390 px, no horizontal scroll), `p06-fix-pull-request.png` | Reviewed: plain language; file names first; technical details collapsed; admin-only GitHub page |

Issues found and fixed during this phase:
- **Revoked cached token.** An uninstall left the worker with a cached token, which reported a
  generic 401 instead of recognising the uninstall. The token is now replaced once, so the
  uninstall is recorded.
- **Settings save rejected.** The worker's `last_full_review_at` write bumped the connection's
  optimistic version, so an admin's settings save failed (found by the browser test; a regression
  assertion was added).
- **Review list did not refresh.** The project review list refreshed only while it already knew
  of a running review; webhook-created reviews appeared only after a reload. It now refreshes
  every 10 s.

## Live checklist (owner; to unblock K-P06-01)

1. Register the App and set the variables (docs/GITHUB.md §1–2) on the hosted service.
2. Create a private test repository with:
   - a Java file containing `x == "Y"`;
   - a `pom.xml`;
   - `.gitattributes` with `hidden/* export-ignore` and a file under `hidden/`;
   - a symlink.

   Install the App on that repository only.
3. **Administration → GitHub:** Install, then Confirm access. Expect "Linked <account>".
4. Create a project, then **GitHub** tab → connect → the first review completes. The upload's
   technical details show the `hidden/` file as fetched.
5. Push a commit to `main`. Expect a branch review compared with the first.
6. Open a pull request that renames one file, deletes one and adds a problem. Expect only the new
   problem listed.
7. Enable checks (threshold *High*). Push to the pull request. Expect one `refactorX` check
   (failure) with an annotation, and one comment updated on the second push.
8. Prepare and check a fix on a default-branch finding. Allow fix pull requests, then **Open pull
   request**. Expect a pull request from `refactorx/fix-…`. Push to `main`, then try another fix:
   expect "stale".
9. Uninstall the App on GitHub. Expect the project to show "uninstalled" and reviews to be
   refused.
10. Record outcomes here and switch P06 to COMPLETE if they match.

## Limitations

- Live GitHub not exercised yet (K-P06-01).
- Webhooks need a public HTTPS address; on Render free, deliveries made while the instance wakes
  can time out and are not resent by GitHub (K-P06-02, K-P06-07).
- Renames are recognised only with identical content (K-P06-03).
- A failed publication is not retried on demand (K-P06-04).
- Reviews queue one at a time on free hosting (K-P06-05).
- The App key can mint tokens for every installation (K-P06-06).
- GitHub only; GitHub Enterprise Server and other hosts are follow-ups (P06-F4).
