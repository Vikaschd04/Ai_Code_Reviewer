# Deployment

Two ways to run the complete application (web UI, API, worker, analyzers, PostgreSQL) in the cloud:

| | Cost | Always on | Notes |
|---|---|---|---|
| **GitHub Codespaces** (below) | Free within GitHub's monthly quota (120 hours of compute and 15 GB storage for personal accounts; with no payment method on file, usage simply stops at the quota — you are never charged) | No — runs while the codespace runs (it stops after about 30 minutes without activity; restart it any time) | Best for trying and testing everything; URL private to your GitHub login by default |
| **Render Blueprint** (further below) | Paid plans (2 GB memory + persistent disk) | Yes | For a permanent URL |

Why not a free always-on host: the analyzers need ~2 GB of memory and ~2 GB of disk (the offline vulnerability database alone is 1.3 GB) plus an always-running worker. Render's free instances have no persistent disk and sleep after 15 minutes (free PostgreSQL expires after 30 days); Railway offers a one-time $5 trial with 1 GB memory/1 GB disk, then $1/month of credit with 0.5 GB memory; Hugging Face now requires a paid plan for Docker Spaces.

## Free: GitHub Codespaces

1. Open <https://github.com/Vikaschd04/Ai_Code_Reviewer>, click **Code → Codespaces → Create codespace on main** (machine: 2-core, 8 GB is enough).
2. Wait. The codespace starts, then `.devcontainer/devcontainer.json` runs `bash deploy/codespace.sh up`, which builds the application image and starts it with PostgreSQL via Docker Compose. The **first** start takes about 10–15 minutes (image build); later starts take about a minute. Progress is visible in the codespace terminal (**View → Terminal**, the "postStartCommand" log).
3. When it prints `Code Review Platform is running: https://<codespace-name>-8080.app.github.dev`, open that URL (or open the **Ports** tab and click the globe icon for port 8080 "Code Review Platform").
4. Get the sign-in token in the codespace terminal: `bash deploy/codespace.sh token`. Paste it on the sign-in page.
5. Test everything: create a project, upload a ZIP, scan, open findings, issues, architecture, compare and exports. During the first minutes Trivy reports UNAVAILABLE while its database downloads; the other analyzers work immediately.

Useful commands in the codespace terminal: `bash deploy/codespace.sh logs` (application logs), `bash deploy/codespace.sh down` (stop; data is kept), `bash deploy/codespace.sh up` (start again).

Sharing: port 8080 is **private** by default — only you (signed in to GitHub) can open it. To let someone else test, right-click the port in the **Ports** tab → *Port Visibility → Public*, and give them the token; switch it back to private afterwards.

Saving your free quota: stop the codespace when you are done (github.com → **Codespaces** → ⋯ → *Stop codespace*). Your data (projects, scans) stays in the codespace's Docker volumes until you delete the codespace (GitHub deletes stopped codespaces after 30 days of inactivity by default).

Updates: after new commits on `main`, run `git pull` in the codespace terminal and `bash deploy/codespace.sh up` (rebuilds only what changed).

## Paid, always on: Render (Blueprint)

Status: configuration implemented and verified locally (the container entrypoint end-to-end, serving the UI) and in CI (Linux image build, started through `deploy/codespace.sh` + Docker Compose with PostgreSQL, smoke-tested on every push). Creating a live service requires the owner's GitHub Codespaces or Render account (steps above/below).

## What runs where

```
browser ──https──► Render web service "ai-code-reviewer" (Docker, 1 CPU / 2 GB, Singapore)
                     ├─ web UI (built React app) and API on the same address
                     ├─ crp-dev hosted: API (uvicorn) + Temporal worker + Temporal dev server
                     ├─ PMD 7.27.0 (Java 21 JRE), ESLint 10.11.0 (Node 22), Opengrep 1.30.0, Trivy 0.69.3
                     ├─ /data persistent disk (10 GB): uploads, scan work area, Temporal SQLite,
                     │   offline Trivy DB (downloaded at first boot, refreshed daily)
                     └─ PostgreSQL 18 "ai-code-reviewer-db" (private: no external connections)
```

Everything is defined in `render.yaml` (Render Blueprint) and `Dockerfile`. The UI and the API share one origin, so the sign-in cookie, uploads and live progress need no proxy or cross-site configuration.

## Deploy with the Blueprint (one time)

1. Sign in at <https://dashboard.render.com> with the account whose GitHub connection can see `Vikaschd04/Ai_Code_Reviewer`.
2. Click **New → Blueprint**. Select the repository `Vikaschd04/Ai_Code_Reviewer`, branch `main`. Render finds `render.yaml` at the repository root (Blueprint path `render.yaml`). Give the Blueprint a name, e.g. `ai-code-reviewer`.
3. Review what Render will create, then click **Apply** (or **Deploy Blueprint**):
   - web service `ai-code-reviewer` — Docker, plan `1c-2g`, region Singapore, 10 GB disk at `/data`, health check `/v1/health/live`, auto-deploy after CI passes;
   - PostgreSQL `ai-code-reviewer-db` — version 18, plan `0.1c-256mb`, no external access.
   There is nothing to type: the database URL is wired automatically and `CRP_ACCESS_TOKEN` is generated. These are paid plans (a persistent disk and 2 GB of memory are required by the analyzers); Render shows the monthly price and asks for a payment method before creating them.
4. Wait for the first build and deploy (the image build takes several minutes: Java, Node, Python dependencies and analyzers). Follow it under the service's **Events/Logs**. At first boot the service migrates the database and downloads the Trivy vulnerability database in the background.
5. Open the service URL shown at the top of the service page (normally `https://ai-code-reviewer.onrender.com`; Render adds a suffix if the name is taken — no configuration change is needed because the service reads its own address from Render).
6. Sign in: service → **Environment** → reveal `CRP_ACCESS_TOKEN` → paste it on the sign-in page. Then create a project and upload a ZIP. Until the Trivy DB download finishes (a few minutes after the first boot), scans show Trivy as UNAVAILABLE; the other engines work immediately.

## Continuous deployment

- Every push to `main` runs GitHub Actions `ci` (lint, types, contract drift, unit tests, web build, Linux image build and container smoke test). Render deploys the new commit only when those checks pass (`autoDeployTrigger: checksPass`). A failed check leaves the running version untouched.
- Database migrations run automatically at container start.
- To deploy manually: service → **Manual Deploy → Deploy latest commit**.

## Operating the service

- **Rotate the sign-in token:** service → Environment → edit `CRP_ACCESS_TOKEN` (≥ 32 random characters) → save; the service restarts and all sessions and upload links are invalidated.
- **Custom domain:** add it under service → Settings → Custom Domains, then add environment variables `CRP_EXTRA_PUBLIC_HOSTS=<your domain>` and `CRP_ALLOWED_WEB_ORIGINS=https://<your domain>,https://<service>.onrender.com`.
- **Logs and health:** service → Logs; `GET /v1/health/live` (public) and `/v1/health/ready` (signed in) report dependencies.
- **Data:** uploads, scan data and history live on the disk and database of your Render account. Deleting the Blueprint's resources deletes them.

## Security posture (read before sharing the URL)

This is a **single-user** hosted mode (`CRP_ENVIRONMENT=hosted`), not the multi-tenant/SSO deployment planned for P07:

- One shared access token (≥ 32 characters) signs in; failed sign-ins are throttled. Sessions are HttpOnly, Secure, SameSite=Strict cookies. Share the URL if you like, never the token.
- The service answers only its own host name(s) (liveness excepted), requires its own origin for cookie-authenticated changes, sends HSTS and a strict Content-Security-Policy for the UI.
- Upload only source you are allowed to store with Render. No source is sent to any AI provider.
- Analyzers run as an unprivileged user inside the container with bounded time/output/heap, on copies of the snapshot; they do not execute uploaded code. There is no per-scan OS sandbox (KNOWN_ISSUES K-P01-01), which is why this mode is single-user.

## Running the container elsewhere

The same image works on any Docker host with a persistent volume at `/data` and these variables: `CRP_ACCESS_TOKEN`, `CRP_DATABASE_URL` or `DATABASE_URL`, `CRP_PUBLIC_HOST` (the public host name; Render provides `RENDER_EXTERNAL_HOSTNAME` instead), optional `PORT`, `CRP_ALLOWED_WEB_ORIGINS`, `CRP_EXTRA_PUBLIC_HOSTS`, `CRP_TRIVY_DB_AUTO_REFRESH=0`, `CRP_INTAKE_*`/`CRP_ENGINE_*`. The host must terminate TLS.

## Known limits

- One instance only (a persistent disk cannot be shared or scaled); Temporal runs as the single-node dev server with SQLite.
- Engine binaries are pinned for Linux x86_64 (Render) and macOS arm64 (development).
- The image is built by Render and by CI, not on the development Mac (insufficient disk for a Docker VM).
