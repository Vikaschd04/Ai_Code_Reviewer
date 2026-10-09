# GitHub setup, permissions and runbook (P06)

refactorX connects to GitHub through **your own GitHub App**. The App's key stays on your
server. refactorX asks GitHub for short-lived tokens limited to one repository and to what each
step needs. Design: [ADR 0015](adr/0015_GITHUB_REVIEWS.md).

ZIP uploads and folder captures keep working whether or not GitHub is set up.

## 1. Register the GitHub App (once, by the server owner)

On GitHub: **Settings → Developer settings → GitHub Apps → New GitHub App** (for an
organization: the organization's settings). Replace `https://YOUR-HOST` with your refactorX
address, for example `https://refactorx.onrender.com`.

| Field | Value |
|---|---|
| GitHub App name | Any unique name, for example `refactorX for Acme` |
| Homepage URL | `https://YOUR-HOST` |
| Callback URL | `https://YOUR-HOST/v1/github/callback` |
| Request user authorization (OAuth) during installation | **Off** (refactorX runs that step itself so it can bind it to the signed-in admin) |
| Setup URL | `https://YOUR-HOST/v1/github/setup` (tick *Redirect on update*) |
| Webhook | Active. URL `https://YOUR-HOST/v1/github/webhook`. Secret: a random value of at least 32 characters |
| Where can this GitHub App be installed? | *Only on this account* unless other accounts need it |

**Repository permissions.** Grant only what you will use:

| Permission | Needed for | Level |
|---|---|---|
| Metadata | always (GitHub requires it) | Read-only |
| Contents | reviews (download commits) | Read-only. **Read and write** only to open fix pull requests |
| Pull requests | reviewing pull requests | Read-only. **Read and write** to post the summary comment or open fix pull requests |
| Checks | posting the refactorX check | Read and write (leave out if you never post checks) |

**Subscribe to events:** *Push*, *Pull request* and *Repository* (for renames). Installation
events are always sent.

After creating the App, collect:
- **App ID** and **Client ID** (shown on the App page);
- **a client secret** (Generate a new client secret);
- **a private key** (Generate a private key; a `.pem` file downloads);
- **the slug**, the last part of the App's public URL `https://github.com/apps/<slug>`.

## 2. Give the values to refactorX

Set these on the server: on Render, *Environment*; locally, `.local/dev.env` or your shell.
Never paste them into chat, tickets or the repository.

| Variable | Value |
|---|---|
| `CRP_GITHUB_APP_ID` | App ID |
| `CRP_GITHUB_CLIENT_ID` | Client ID |
| `CRP_GITHUB_CLIENT_SECRET` (or `CRP_GITHUB_CLIENT_SECRET_FILE`) | client secret |
| `CRP_GITHUB_PRIVATE_KEY` (or `CRP_GITHUB_PRIVATE_KEY_FILE`) | the `.pem` content; a single line with `\n` for line breaks also works |
| `CRP_GITHUB_WEBHOOK_SECRET` (or `..._FILE`) | the webhook secret |
| `CRP_GITHUB_APP_SLUG` | slug |
| `CRP_GITHUB_CALLBACK_URL` (optional) | only if you registered several callback URLs |

Optional:
- `CRP_GITHUB_MAX_BLOB_FETCHES` (default 300): files left out of GitHub's archive that are
  fetched one by one.
- `CRP_GITHUB_WEBHOOK_MAX_BYTES` (default 25 MiB).
- `CRP_GIT_RECONCILE_DAYS` (default 7): days between full re-checks.

Fix workspaces (P08) use the same "Open pull requests" permission and project switch as fix pull requests: a checked workspace becomes one pull request with all its changes, only while the branch still points at the reviewed commit.

File variants must be owner-only (`chmod 600`). Restart the service; **Administration → GitHub**
then shows the connect steps. If a value is missing, the page says which one, for admins only.

## 3. Connect repositories (workspace admins)

1. **Administration → GitHub → Install on GitHub.** Choose the account and the repositories
   refactorX may read.
2. **Confirm access.** GitHub asks you to approve; you come back and the installations you can
   manage are linked. The installation id GitHub puts in the address is never trusted.
3. Open a project → **GitHub** tab → choose the repository → **Connect and review.** The default
   branch is reviewed in full right away.
4. In **What refactorX does**, choose what is reviewed (pushes, pull requests, forks) and what
   may be posted (check and comment, fix pull requests, failing threshold). Publishing is off
   until you switch it on.

Members can start reviews (**Review latest commit**, **Review pull request**) and open fix pull
requests once allowed. Viewers can read. The demo account cannot connect GitHub.

Local development: GitHub cannot reach `127.0.0.1`, so webhooks do not arrive. Use the
*Review* buttons; everything else works the same.

## 4. What happens on GitHub

- **Reading:**
  - refactorX downloads the exact commit (GitHub's archive) and checks it against the commit's
    file list;
  - files GitHub leaves out of archives (`export-ignore`) or changes in them are fetched as
    committed;
  - submodules and Git LFS files are listed as not included;
  - no Git command or repository script ever runs.
- **When checks are on:**
  - one check named **refactorX** per reviewed commit, with annotations on new problems;
  - one summary comment per pull request, updated rather than repeated;
  - the check fails only when you set a threshold and a new problem reaches it. Incomplete
    reviews are neutral.
- **When fix pull requests are allowed:**
  - a person clicks **Open pull request** on a fix whose checks passed;
  - refactorX creates a branch `refactorx/fix-…` with one commit on top of the reviewed commit
    and opens a pull request;
  - if the branch moved since the review, it refuses ("stale"): review the new commit, move the
    fix and check it again;
  - nothing is merged.

## 5. Revocation and key rotation (runbook)

| Situation | Action |
|---|---|
| Stop refactorX's access to an account | On GitHub: *Settings → Applications → Installed GitHub Apps → Configure → Uninstall*. refactorX marks it uninstalled on the webhook (or the next review, which fails with "uninstalled") and stops reviews. In refactorX, **Unlink** the account to remove it. Earlier results stay |
| Stop one repository | On GitHub, remove it from the installation's repository list; refactorX shows "no longer has access". Or **Disconnect repository** on the project's GitHub tab |
| Pause reviews | Suspend the installation on GitHub, or untick *Review every push* and *Review pull requests* |
| Private key leaked or rotated | App settings → generate a new private key → set `CRP_GITHUB_PRIVATE_KEY` → restart → delete the old key on GitHub |
| Webhook secret rotated | Change it in the App settings and `CRP_GITHUB_WEBHOOK_SECRET` together. Deliveries in between are refused (401) and can be redelivered from the App's *Advanced* tab |
| Client secret rotated | Generate a new one, update `CRP_GITHUB_CLIENT_SECRET`, delete the old one |

refactorX stores **no GitHub tokens**. Installation tokens live in memory for up to an hour; the
linking user's token is used once. It stores:
- delivery ids and event names (no payloads);
- pull request titles and author logins;
- snapshots of reviewed commits, deleted with the project.

## 6. Troubleshooting

| Message | Meaning |
|---|---|
| `invalid_signature` (webhook 401) | The webhook secret in GitHub and `CRP_GITHUB_WEBHOOK_SECRET` differ |
| "The GitHub App was not granted the permission this needs" | Add the permission to the App, then accept the new permissions on each installation |
| "GitHub could not be reached; run the review again later" | GitHub outage or rate limit; reviews are not retried forever |
| A push or pull request was not reviewed | The delivery failed (for example while a free instance was asleep); GitHub does not resend automatically. Redeliver it from the App's *Advanced* tab, or press **Review latest commit** / **Review pull request** |
| `stale_patch` | The branch moved since the reviewed commit (see above) |
| `github_rejected` when opening a fix pull request | A branch ruleset blocks `refactorx/fix-*` branches or the pull request; adjust the ruleset or apply the downloaded patch yourself |
| "Left out of GitHub's download; could not be fetched" in an upload's files | More than `CRP_GITHUB_MAX_BLOB_FETCHES` files were left out of GitHub's archive; raise the limit and review again |
