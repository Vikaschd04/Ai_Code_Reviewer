# Deployment

Ways to run **refactorX** — the complete application (web UI, API, reviews with PMD, ESLint, Opengrep and Trivy, PostgreSQL) — in the cloud:

| | Cost | Always on | Notes |
|---|---|---|---|
| **Render free** (recommended for the trial; below) | Free (Render free web service + free PostgreSQL) | Public URL; sleeps after 15 idle minutes and wakes on the next visit (about a minute) | Lite profile: every feature, one scan at a time, slow CPU (0.1), uploads up to 25 MB, database 1 GB and deleted by Render 30 days after creation unless upgraded |
| **GitHub Codespaces** (further below) | Free within GitHub's monthly quota (120 hours, 15 GB for personal accounts; without a payment method usage stops, you are never charged) | Only while the codespace runs | Full speed; URL private to your GitHub login by default |
| **Render paid** (further below) | Paid plans (2 GB memory + disk) | Yes | Standard profile; for a permanent URL later |

Railway is not used: its free credit is a one-time $5 trial and then $1/month with 0.5 GB of memory, which cannot hold the 1.3 GB offline vulnerability database. Decision and measurements: ADR 0010.

## Free: Render (Blueprint)

### Deploy (one time)

1. Sign in at <https://dashboard.render.com> with the account whose GitHub connection can see `Vikaschd04/Ai_Code_Reviewer`.
2. Click **New → Blueprint**. Select the repository `Vikaschd04/Ai_Code_Reviewer`, branch `main`. Keep the Blueprint path `render.yaml`. Name the Blueprint, e.g. `ai-code-reviewer`.
3. Render lists what it will create — both on the **free** plan, no payment method needed:
   - web service `ai-code-reviewer` — Docker, plan `free`, region Singapore, health check `/v1/health/live`, auto-deploy after CI passes, `CRP_PROFILE=lite`;
   - PostgreSQL `ai-code-reviewer-db` — version 18, plan `free`, no external access.
   There is nothing to type: the database URL is wired automatically and the sign-in token `CRP_ACCESS_TOKEN` is generated. Click **Apply** (or **Deploy Blueprint**).
   If Render still asks for payment, check that every resource shows plan **Free**; a workspace can have only one free PostgreSQL database, so delete an older free database first if you already have one.
4. Wait for the first build and deploy (roughly 10–20 minutes: Java, Node, Python dependencies, analyzers and the vulnerability database are built into the image). Follow it under the service's **Events/Logs**; the deploy is done when the log shows the server listening and the service is **Live**.
5. Open the service URL shown at the top of the service page (normally `https://ai-code-reviewer.onrender.com`; Render adds a suffix if the name is taken — no configuration change needed, the service reads its own address from Render).
6. Quickest test: click **Try the demo** on the sign-in page, then **Run the sample review** — the sample online store is uploaded and reviewed by every analyzer (about a minute or two on the free instance), and you land on its results.
7. Owner sign-in: service → **Environment** → reveal `CRP_ACCESS_TOKEN` → copy it → paste it under "or sign in with your access token". The owner's workspace is separate from the demo workspace, and only the owner sees **System status**.
8. Test end to end: create a project, upload a ZIP (up to 25 MB), start a review, then open findings, issues, architecture, changes and the report downloads. Trivy works from the first review (its database is inside the image).

### What runs where

```
browser ──https──► Render free web service "ai-code-reviewer" (Docker, 0.1 CPU / 512 MB, Singapore)
                     ├─ one process (lite profile): web UI + API + in-process scan runner
                     ├─ PMD 7.27.0 (Java 21), ESLint 10.11.0 (Node 22), Opengrep 1.30.0, Trivy 0.69.3,
                     │   one analyzer at a time; Trivy DB baked into the image, refreshed daily
                     └─ Render free PostgreSQL 18 "ai-code-reviewer-db" (1 GB, private):
                         projects, scans, findings, uploads and scan artifacts
```

Everything is defined in `render.yaml` and `Dockerfile`. The UI and the API share one origin, so the sign-in cookie, uploads and live progress need no proxy or cross-site configuration.

### What "free" means in practice

- **Sleep:** after 15 minutes without visits the service stops; the next visit wakes it in about a minute (Render shows a loading page). A scan that was running when the service stopped continues automatically after the wake-up. Keep the scan page open during long scans: it checks progress every few seconds, which keeps the service awake.
- **Speed:** 0.1 CPU. A small project takes a few minutes to scan; large projects take much longer. One scan runs at a time; others wait in the queue.
- **Size:** uploads up to 25 MB (ZIP); files over 2 MB are listed in the snapshot as OVERSIZED and not analyzed.
- **Storage:** the free database holds 1 GB, including uploaded snapshots and scan artifacts. Delete test projects you no longer need.
- **30-day database:** Render deletes free databases 30 days after creation (with a 14-day grace period and email notices). Before that, download exports you want to keep. To continue afterwards, either upgrade the database to a paid plan (data kept) or delete it and let the Blueprint create a new empty one (**Blueprint → Manual sync**).
- **Instance hours:** Render grants 750 free hours per workspace per month — enough for one service running all month.
- **Builds:** each deploy builds the image; builds count toward the workspace's included build minutes (see Render's billing page).

The CI job proves the lite profile inside these limits on every push: it runs the complete smoke test with 512 MB of memory, no swap and 0.1 CPU and fails on any restart or out-of-memory kill.

### Continuous deployment

- Every push to `main` runs GitHub Actions `ci` (lint, types, contract drift, unit tests, web build, Linux image build, container smoke tests in the standard and the lite profile). Render deploys the new commit only when those checks pass (`autoDeployTrigger: checksPass`). A failed check leaves the running version untouched.
- Database migrations run automatically at start.
- To deploy manually: service → **Manual Deploy → Deploy latest commit**.

## Operating the service

- **Demo account:** on by default in `render.yaml` (`CRP_DEMO_ENABLED=true`) so anyone with the URL can try refactorX in a **shared** demo workspace (they see each other's uploads; limited to 30 projects and 20 reviews per hour). It never sees the owner's projects. Set `CRP_DEMO_ENABLED=false` under service → Environment before storing real customer code on the same deployment (ADR 0011).

- **Rotate the sign-in token:** service → Environment → edit `CRP_ACCESS_TOKEN` (≥ 32 random characters) → save; the service restarts and all sessions and upload links are invalidated.
- **Custom domain:** add it under service → Settings → Custom Domains, then add environment variables `CRP_EXTRA_PUBLIC_HOSTS=<your domain>` and `CRP_ALLOWED_WEB_ORIGINS=https://<your domain>,https://<service>.onrender.com`.
- **Logs and health:** service → Logs; `GET /v1/health/live` (public) and `/v1/health/ready` (signed in) report dependencies. Lines starting `crp-hosted:` report migrations and the Trivy DB date/refresh.
- **Tuning (optional):** every lite default can be overridden with an environment variable, e.g. `CRP_INTAKE_MAX_UPLOAD_BYTES`, `CRP_PMD_JAVA_HEAP`, `CRP_ESLINT_HEAP_MB`, `CRP_ENGINE_TIMEOUT_SECONDS`, `CRP_TRIVY_DB_AUTO_REFRESH=0`. Raising memory settings on the free instance risks out-of-memory restarts.
- **Data:** everything lives in your Render account (database; the free service keeps no files between restarts). Deleting the Blueprint's resources deletes it.

## AI review (optional)

refactorX works fully without AI. To let reviewers ask questions about their code and get AI reviews of selected files, give the server a model provider key. The key stays on the server; you add it yourself — never paste it into a chat or commit it.

1. Get an API key from your provider: **Anthropic** (console.anthropic.com → API keys) or any **OpenAI-compatible** service (OpenAI, Azure OpenAI, or a gateway that speaks the chat-completions API).
2. Render dashboard → service `ai-code-reviewer` → **Environment** → **Add Environment Variable**, then **Save, rebuild and deploy** (or **Save and deploy**):

   | Variable | Anthropic | OpenAI-compatible |
   |---|---|---|
   | `CRP_AI_PROVIDER` | `anthropic` | `openai_compatible` |
   | `CRP_AI_API_KEY` | your key (mark it secret) | your key (mark it secret) |
   | `CRP_AI_MODEL` | optional, default `claude-opus-5-5` | **required**, the model name your service offers |
   | `CRP_AI_BASE_URL` | optional | optional (default `https://api.openai.com/v1`); must be `https://` |
   | `CRP_AI_OPENAI_AUTH_HEADER` | — | `api-key` for Azure-style gateways (default bearer) |
   | `CRP_AI_OPENAI_MAX_TOKENS_FIELD` | — | `max_tokens` for services that do not accept `max_completion_tokens` |

3. Optional limits and costs: `CRP_AI_MONTHLY_TOKEN_LIMIT` (default 5,000,000; `0` = none), `CRP_AI_PRICE_INPUT_PER_MTOK_USD` and `CRP_AI_PRICE_OUTPUT_PER_MTOK_USD` (your provider's price per million tokens; without them costs show as unknown), `CRP_AI_MONTHLY_COST_LIMIT_USD`, `CRP_AI_RUN_MAX_COST_USD`, `CRP_AI_KEEP_TRANSCRIPTS=false` (do not keep masked transcripts).
4. After the deploy, sign in as the owner, open a project → **AI review**, tick the confirmation and **Switch on AI review**. Only then can anyone ask questions or start AI reviews in that project; the demo account cannot switch it on.

What is sent: only short excerpts of that project's latest upload that the AI asks for, with values that look like passwords or keys masked first. Nothing is sent for projects where AI review is off. Every call's token usage is recorded, runs stop at their limits and are never retried automatically, and each answer shows which cited lines were checked against the code. Design: ADR 0012; evaluation status: docs/validation/P03_REPORT.md.

## GitHub (optional)

Reviews of pushes and pull requests use **your own GitHub App**. Register it with the exact URLs, permissions and events in [docs/GITHUB.md](GITHUB.md), using `https://<your-service>.onrender.com` as the host. Then add on Render → **Environment**: `CRP_GITHUB_APP_ID`, `CRP_GITHUB_CLIENT_ID`, `CRP_GITHUB_CLIENT_SECRET`, `CRP_GITHUB_PRIVATE_KEY` (the `.pem` content; mark secret), `CRP_GITHUB_WEBHOOK_SECRET` and `CRP_GITHUB_APP_SLUG`, and **Save and deploy**. Sign in as the owner → **Administration → GitHub** → install and confirm → connect repositories from each project's **GitHub** tab. Nothing is posted to GitHub until a project admin allows it. On the free plan reviews run one at a time, and the service sleeps after 15 idle minutes: an event sent while it is asleep can time out on GitHub's side (GitHub does not resend it automatically). Redeliver it from the App's **Advanced** tab or press **Review latest commit**; the next push or pull request update is reviewed normally.

## Security posture (read before sharing the URL)

This is a **single-user** hosted mode (`CRP_ENVIRONMENT=hosted`), not the multi-tenant/SSO deployment planned for P07:

- One shared access token (≥ 32 characters) signs in; failed sign-ins are throttled. Sessions are HttpOnly, Secure, SameSite=Strict cookies. Share the URL if you like, never the token.
- The service answers only its own host name(s) (liveness excepted), requires its own origin for cookie-authenticated changes, sends HSTS and a strict Content-Security-Policy for the UI.
- Upload only source you are allowed to store with Render. No source is sent to an AI provider unless you configure one and a workspace admin switches AI review on for a project (below).
- Analyzers run as an unprivileged user inside the container with bounded time/output/heap, on copies of the snapshot; they do not execute uploaded code. There is no per-scan OS sandbox (KNOWN_ISSUES K-P01-01), which is why this mode is single-user.

## Free alternative: GitHub Codespaces

Full speed and private, but only running while you use it.

1. Open <https://github.com/Vikaschd04/Ai_Code_Reviewer>, click **Code → Codespaces → Create codespace on main** (machine: 2-core, 8 GB is enough).
2. Wait. `.devcontainer/devcontainer.json` runs `bash deploy/codespace.sh up`, which builds the application image and starts it with PostgreSQL via Docker Compose (standard profile). The **first** start takes about 10–15 minutes (image build); later starts take about a minute. Progress: **View → Terminal**, the "postStartCommand" log.
3. When it prints `refactorX is running: https://<codespace-name>-8080.app.github.dev`, open that URL (or **Ports** tab → globe icon for port 8080), then use **Try the demo** or sign in with the token.
4. Get the sign-in token in the codespace terminal: `bash deploy/codespace.sh token`.

Commands: `bash deploy/codespace.sh logs` (application logs), `down` (stop; data kept), `up` (start again), `reset` (stop and delete all data). `CRP_PROFILE=lite bash deploy/codespace.sh up` runs the Render free configuration (512 MB, 0.1 CPU) instead.

Sharing: port 8080 is **private** by default. To let someone else test, right-click the port → *Port Visibility → Public*, give them the token, and switch it back afterwards. Save quota by stopping the codespace when done (github.com → **Codespaces** → ⋯ → *Stop codespace*); data stays in its Docker volumes until the codespace is deleted (GitHub deletes stopped codespaces after 30 days of inactivity by default). Updates: `git pull` then `bash deploy/codespace.sh up`.

## Later, paid and always on: Render standard profile

`deploy/render-standard.yaml` runs the standard profile (API + Temporal worker + Temporal dev server with SQLite on a 10 GB disk, parallel-capable analyzers with larger heaps) on plan `1c-2g` with a paid PostgreSQL plan. Create it with **New → Blueprint**, Blueprint path `deploy/render-standard.yaml`; Render shows the prices before creating anything. Moving from the free trial: data is not migrated automatically — rescan the projects you need.

## Running the container elsewhere

The image works on any Docker host that terminates TLS: set `CRP_ACCESS_TOKEN`, `CRP_DATABASE_URL` or `DATABASE_URL`, `CRP_PUBLIC_HOST` (public host name; Render provides `RENDER_EXTERNAL_HOSTNAME`), optionally `CRP_PROFILE=lite` (no disk needed) or a persistent volume at `/data` (standard), `PORT`, `CRP_ALLOWED_WEB_ORIGINS`, `CRP_EXTRA_PUBLIC_HOSTS`, `CRP_TRIVY_DB_AUTO_REFRESH=0`, `CRP_INTAKE_*`/`CRP_ENGINE_*`. `deploy/docker-compose.yml` (+ `deploy/docker-compose.lite.yml`) is a working example.

## Known limits

- One instance only; the standard profile's Temporal is the single-node dev server with SQLite; the lite profile has no workflow history UI (progress is in scan events and logs).
- Engine binaries are pinned for Linux x86_64 (Render, Codespaces, CI) and macOS arm64 (development).
- The image is built by Render, Codespaces and CI, not on the development Mac (insufficient disk for a Docker VM).
- The Render deployment itself has not yet been created from this repository by the owner; CI reproduces its limits but not Render's network and wake-up behaviour.
