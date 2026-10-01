# ADR 0015 — GitHub connections, branch and pull request reviews, and publication

Status: accepted; implemented and verified against a fake GitHub. The live check against real
GitHub needs the owner's GitHub App and test repository. Date: 1 October 2026. Owner: repository
owner (Phase 6 scope in prompts/P06_GIT_AND_INCREMENTAL.md); implemented by the development agent.

Context and constraints:

- Teams want reviews of every push and pull request without uploading ZIPs. Uploads and folder
  captures must keep working unchanged.
- Repository content is untrusted. Cloning runs Git with repository configuration (hooks,
  filters, submodule URLs), which must not execute on our servers.
- Access must be least-privilege and revocable. Reading code and writing to GitHub (checks,
  comments, pull requests) are separate capabilities, and publication is opt-in per project.
- Several workspaces share one server (the shared demo workspace among them). One customer's code
  must never reach another's.
- The free hosting profile has no Temporal and one process; reviews must run there too.

Decision:

1. **One GitHub App, repository-scoped installation tokens.** The operator registers an App. Its
   RSA key signs short-lived JWTs (RS256, issued 60 s in the past, valid for 9 minutes; issuer =
   client ID). Every operation asks GitHub for an installation token limited to one repository and
   to what it needs:
   - reading: contents, metadata and pull requests (read);
   - checks: checks and pull requests (write);
   - fix pull requests: contents and pull requests (write).

   Tokens live in memory until five minutes before they expire. A 401 drops the cached tokens of
   that installation and mints once more, so an uninstall surfaces as `installation_not_found` and
   is recorded as revoked. Only the publishing code asks for write permissions, and only after the
   project's policy allows it. Pinned API version: `2026-03-10`.
2. **Verified linking.** GitHub's install redirect carries an `installation_id` anyone can forge,
   so it is never trusted. Linking works like this:
   1. An admin (never the demo account) starts it; refactorX stores a one-time state (hash only,
      10 minutes, bound to the user and workspace).
   2. The admin authorizes refactorX on GitHub.
   3. The callback only forwards code and state to the web app, because the session cookie is
      `SameSite=Strict`.
   4. The web app completes the link with a normal authenticated request.
   5. The server lists the installations that GitHub user can access and links them. The user
      token is used once and discarded.

   An installation belongs to one workspace at a time; one project per repository.
3. **Commit capture without Git.** refactorX downloads GitHub's ZIP of the exact commit.
   - The archive redirect gets no credentials.
   - Validation uses the hardened ZIP path, with three differences: the top-level folder is
     removed, symbolic links are recorded as excluded, and Git blob ids are computed.
   - Because archives honour `export-ignore` (which a pull request could use to hide a file),
     `export-subst` and line-ending attributes, every capture is reconciled with the commit's
     recursive tree:
     - matching blob ids are kept;
     - CRLF conversion is undone locally;
     - files missing or altered are fetched one by one as committed blobs (default limit 300)
       and otherwise recorded as excluded with the reason;
     - submodules and Git LFS pointers are recorded as excluded;
     - an archive file absent from the tree rejects the capture.
   - Snapshots carry provider, repository, ref, commit, tree and the capture report. They are
     reused per project and commit.
4. **Reviews converge on the newest commit.**
   - Webhook deliveries (HMAC `X-Hub-Signature-256`, constant time, size-bounded) are recorded by
     delivery id, so redelivery is a no-op.
   - Pushes to the default branch create branch reviews. Pull request opened, reopened,
     synchronize and ready-for-review events, and base changes, create pull request reviews.
   - Closing a pull request cancels its reviews.
   - A review resolves the branch head (or the pull request's head, base and merge base) when it
     runs, and decides under the connection's row lock:
     - an automatic review of a commit already claimed is SKIPPED (`already_reviewed`);
     - claimed reviews of an older commit or merge base are canceled as SUPERSEDED (their scans
       stop);
     - a QUEUED review is never superseded, because it may be for a newer push.

   Late, duplicate and out-of-order events therefore end on one review of the current commit.
5. **Comparison and lineage.**
   - Branch reviews compare with the branch's previously reviewed commit; pull requests with their
     merge base (scanned once as a `reference` scan if needed).
   - Change sets come from manifests (provider-independent; exact-content renames).
   - Findings in renamed files are matched by engine, rule and evidence line, so they are not
     reported as new.
   - "Fixed" requires the strict verified-absence rule (P02); deleted files stay "not rechecked".
   - Only `baseline` scans (uploads, the default branch) change issues. Pull request and reference
     scans never do, and neither does a scan whose review was superseded.
   - On the default branch, issues of files renamed with identical content move to the new path
     (event `moved`) and keep their triage.
6. **Incremental by construction, with reconciliation.**
   - Per-file engines (PMD, ESLint, Opengrep) are single-file and cached by content, rules and
     configuration, so unchanged files are reused.
   - Engines that read other files (Trivy dependencies, framework configuration, the graph) are
     never cached per file and always run on the whole snapshot. A changed lockfile, Spring XML or
     permission set therefore reaches unchanged code.
   - The change set lists configuration changes and one-hop graph dependents to explain the scope.
   - A full review (no reuse) runs on the first connection, on demand, and hourly-checked every
     `reconcile_days` (default 7) in both runtimes.
7. **Publication is optional and exact.**
   - When an admin switches it on, one check run per reviewed head is posted. It carries up to 50
     annotations for new findings, and Markdown from the repository is escaped. One summary
     comment per pull request is updated in place.
   - The check fails only if the project set a severity threshold and a new finding reaches it.
     Incomplete reviews are `neutral`, never `success`.
   - A publication failure never fails the review.
   - Fix pull requests are separate. A person clicks the button, and only for a validated fix
     (current patch passed, P05) from a non-fork commit. The target branch must still point at the
     reviewed commit (otherwise 409 `stale_patch`). refactorX then creates one commit on top of
     it (same file mode) and a `refactorx/fix-…` branch, and opens the pull request. It never
     merges and is idempotent per fix.
8. **Forks.** Pull requests from forks are not reviewed automatically unless the project allows
   it; a member can start one by hand. Fix pull requests never target fork code.

Alternatives considered:

- *Clone with Git (even shallow):* rejected. Git executes configuration and hooks, and
  submodules and LFS widen the attack surface; the archive plus tree check needs no Git binary.
- *Trust the archive as is:* rejected. `export-ignore` would let a change hide files from review.
- *Blob-by-blob download only:* rejected as the default (one request per file, rate limits);
  used only for what the archive leaves out.
- *Two GitHub Apps (reader and publisher):* possible later. One App with down-scoped tokens,
  publishing code isolated and gated per project, keeps setup simple for single-user hosting.
- *Trust `installation_id` from the setup redirect:* rejected (GitHub warns it can be forged).
- *Commit-graph incremental scanning (only changed files analysed):* rejected for correctness.
  Content-addressed per-file reuse plus always-whole-snapshot cross-file engines gives the same
  speed for unchanged files without missing configuration effects.

Consequences and migration/reversal approach:

- **Migration `0008`:**
  - new tables: `git_installations`, `git_repositories`, `git_connections`,
    `git_connection_events`, `git_link_requests`, `git_deliveries`, `code_reviews`,
    `fix_pull_requests`;
  - snapshot Git columns;
  - the `github` source mode.

  Downgrade deletes GitHub sources (and their snapshots) and drops the tables.
- New dependency `cryptography` 50.0.2 (Apache-2.0 OR BSD-3-Clause; with `cffi` 2.1.1 MIT-0 and
  `pycparser` 3.0 BSD-3-Clause).
- Without App settings, GitHub shows as "not set up" and everything else is unchanged.
- The `git_integration` capability is `available` or `not_configured`, no longer `planned`.

Evidence and source/version references:

- GitHub Docs (fetched 1 October 2026):
  - JWT for GitHub Apps;
  - installation access tokens (`repository_ids`, `permissions`, 1 h);
  - validating webhook deliveries (`X-Hub-Signature-256`, constant-time compare);
  - setup URL (forged `installation_id` warning) and user access tokens (`/user/installations`);
  - zipball endpoint (temporary redirect);
  - git trees (recursive, 100,000 entries or 7 MB, `truncated`) and compare (`merge_base_commit`);
  - check runs (50 annotations per request);
  - REST API versions (`2026-03-10`; 2022-11-28 supported until 10 March 2028) and their breaking
    changes;
  - downloading source code archives and Git LFS objects in archives (`git archive`,
    `export-ignore`).
- Report: docs/validation/P06_REPORT.md.

Affected contracts, phases and tests:

- **API:**
  - `/v1/github/*` (status, callback, setup, webhook);
  - `/v1/workspaces/{id}/github/*` (link, link/complete, installations, sync, unlink);
  - `/v1/projects/{id}/git-connection` (GET, PUT, PATCH, DELETE);
  - `/v1/projects/{id}/code-reviews`, `/v1/code-reviews/{id}` (+ `/cancel`);
  - `POST /v1/fix-proposals/{id}/pull-request`;
  - snapshot Git fields.
- **Tests:**
  - `packages/analysis/tests/test_github_sources.py`;
  - `services/api/tests/test_github_api.py`;
  - `services/worker/tests/test_p06_github.py`;
  - `apps/web/src/components/GitHub.test.tsx`;
  - `apps/web/e2e/p06-github.spec.ts`.
