# Source intake contract

This is the key Phase 1 specification. Intake must work without Git history or an external account.

## Intake modes

1. ZIP: browser uploads an archive through a bounded streaming API, then receives an intake ID and validation status.
2. Local runner: user supplies an explicit directory to a local CLI. It inventories only that root, creates a frozen capture and submits it using a scoped project token. It must explain that bytes leave the machine in this mode. A future private-execution mode is separate.
3. Browser folder: optional selected files with relative paths, subject to the same manifest and quota rules; unsupported browsers fall back to ZIP.
4. Registered server directory: optional administrator-managed root, exposed as a source ID. Users cannot supply arbitrary absolute server paths.

Folder/Git names are display labels, not trusted identifiers. Only capture optional Git metadata by safe bounded reads when requested. Do not initialize, checkout, clean, reset or modify the user's project.

## Identity and immutability

Create source_id, intake_id, snapshot_id and a manifest version. Hash accepted regular-file bytes and normalized paths; derive snapshot identity from a canonical sorted manifest plus manifest version. Record original archive digest separately. Retain discovered/rejected/excluded entries with safe relative path, reason, size where known and disposition. Do not follow symlinks to hash external content.

Concurrent local changes are detected by before/after metadata plus content hashing/revalidation; retry boundedly or reject inconsistent capture. Analyze only the stored immutable snapshot. Verify submitted hashes server-side; never trust a client-declared checksum or total size.

## Archive and path defenses

Stream uploads into an isolated temporary location; enforce compressed size even without Content-Length. Stream extraction with cumulative and per-entry expanded limits. Never use a naive unrestricted extract-all call.

Reject traversal, absolute paths, drive/UNC paths, null/control path components, symlinks/hardlinks, device/special files, duplicate normalized paths and case/Unicode collisions under the documented canonicalization policy. Validate containment at file-open time, not only via a string prefix. Bound filename length, nesting, total entries and compression ratio. Do not recursively unpack nested archives; record them as unsupported binary artifacts. Disable XML external entities in later config parsing.

Reject encrypted or unsupported archive formats explicitly in the first release. Remove partial artifacts on rejection/cancellation according to bounded cleanup rules. Redact suspicious filenames safely in UI/logs without losing forensic IDs.

## Configurable initial quotas

Suggested development defaults: 100 MiB compressed upload, 1 GiB expanded snapshot, 50,000 entries, 10 MiB analyzable text per file and 100:1 compression ratio. These are policy defaults to implement in typed config, not performance promises. A project exceeding them receives an actionable message and a configured higher-tier/private-runner option later. Binary/artifact size and cumulative budgets still apply to excluded files during intake.

## Scope policy

List all submitted/captured entries, then apply versioned analysis exclusions. Show generated/vendor/build/cache directories separately; excluded is not reviewed. Permit user scope overrides within safety limits. Default secret-bearing paths such as .env and private-key files to exclusion with a visible reason; do not include them via an agent suggestion. Secret detection may run locally or in a restricted stage, but findings/snippets must redact values and no secret goes to an AI provider by default.

Local runner skip rules must preserve manifest accounting for traversed excluded entries and disclose unreadable directories. A ZIP cannot report files the uploader omitted: UI must say submitted snapshot, not entire laptop/repository.

ZIP bytes have already reached the server before extraction exclusions are applied. Do not claim that excluding a secret-bearing ZIP entry prevented its upload. Explain this in intake guidance, offer local preflight/filtering, redact findings and delete rejected/raw artifacts according to retention policy. Never send such values onward to a model by default.

## Lifecycle

CREATED → UPLOADING/CAPTURING → VALIDATING → READY; terminal alternatives REJECTED, FAILED, CANCELED. Only READY frozen snapshots may start scans. Return stable error codes and human-readable remediation. Upload retry/finalize is idempotent; abandoned temp data has an expiry and cleanup job.

## Required tests

ZIP traversal, Windows paths, Unicode/case collision, symlink, bomb/oversize, huge entry count, encrypted archive, interruption, repeated finalize, unauthorized project access, local file mutation, unreadable entry, deterministic hash and unchanged original directory. Browser folder input passes the same server-side checks.
