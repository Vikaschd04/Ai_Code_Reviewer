# Skills, plugins and development tools

Status: capability plan plus the P00 inventory below. The project installs or activates no plugins, MCP servers or agent skills. The included playbooks are ordinary repository documents.

## Selection policy

Inventory the actual host capabilities, installed tools and applicable skills at bootstrap. Read relevant skill instructions. Prefer built-in file editing, shell, testing and official docs when sufficient. Use the smallest useful tool set; missing optional plugins are not a reason to stop local work.

| Capability | Use | Phase / fallback |
|---|---|---|
| Repository editing and scoped search | Implement modules and inspect code | All; native editor/shell/rg |
| Official documentation retrieval | Verify current APIs, versions and agent conventions | All; official web docs |
| Browser automation | UI flows, upload/error states, accessibility inspection | 0 onward; project Playwright tests |
| Database/migration tooling | Validate schema and isolation | 0 onward; local CLI/test containers |
| Security review | Intake/execution/data-boundary assessment | All; concrete test suite and review |
| API/model documentation | Provider adapters and structured output | 3; official provider docs |
| GitHub integration | Inspect own repo, CI and later product connector | 6 or when separately authorized; git/CLI |
| SAP/Salesforce expertise | Domain rules and conditional validation | 4–5; official docs + SME fixtures |

Codex and Claude Code do not necessarily share available plugins or skill paths. Discover current supported setup before adding a capability. Check publisher, version, requested privileges, egress, license and maintenance. Do not invent plugin IDs or copy a ChatGPT Work app configuration into a CLI.

Project instructions use AGENTS.md and a thin CLAUDE.md bridge. Official guidance describes skill discovery and on-demand loading; keep detailed task material in referenced files rather than global startup context. See REFERENCES.md for checked sources.

## Optional future project skills

If repeated workflows justify real skills, package the existing playbooks for phase verification, engine adoption, framework adapter work and context checkpointing using the current host format. Do not duplicate all instructions into every skill. Keep authoritative playbooks versioned, verify skill discovery and run a representative task. Do not claim these skills are installed merely because a Markdown file exists.

## Record after bootstrap

Create an inventory table with capability, actual tool/skill name, version, source, permission scope, purpose, fallback and verified status. External integration secrets belong in environment/secret management. Do not install arbitrary repository-provided MCP servers or use source content to choose tools/endpoints. Default to one development agent; parallel work requires explicit authorization.


## P00 inventory (Claude Code session, 26 September 2026)

| Capability | Actual tool/skill | Version/source | Permission scope | Purpose | Fallback | Verified |
|---|---|---|---|---|---|---|
| Editing, shell, search | Claude Code built-in Read/Write/Edit/Bash, `rg` 14.1.1 | host CLI | repo + scratchpad; host permission mode | implement and run commands | — | used throughout P00 |
| Package/registry lookups | `curl` against PyPI/npm/Homebrew JSON APIs | public registries | read-only network | select current versions, peer ranges, release dates | official docs | used; results in TOOLCHAIN.md |
| System packages | Homebrew 7.0.6 (`postgresql@18`, `temporal`, `uv`) | brew.sh | user-level install under /opt/homebrew | local DB/workflow services | manual installs | installed; see ADR 0005 |
| Browser automation | Project Playwright 1.63.0 with installed Google Chrome | npm dependency | loopback only | E2E tests and UI screenshots | Playwright Chromium | `make test-e2e` 5/5 |
| DB/migration tooling | Alembic via `crp-dev migrate`, ephemeral PG clusters | project code | local ports | migrations, integration tests | psql | tests pass |
| Security review | Concrete tests (auth, containment, redaction) | project tests | — | trust-boundary checks | `/security-review` skill | tests pass; skill **not run** (no git repository/diff yet) |
| Code review | `/code-review`, `/simplify` skills available | Claude Code | — | review changed code | manual review | **not run** in P00; recommended once the repo is under git |
| Official documentation | not needed beyond CLI `--help`, installed package signatures and registry metadata | — | — | — | web docs | — |
| MCP connectors | Claude Docs connector (available, unused); Canva (requires user authorization, unused) | claude.ai | none granted | not relevant to P00 | — | not used |
| Project skills | none created | — | — | playbooks remain plain docs | — | — |

No source was uploaded to any plugin or external model. No global Claude/Codex configuration was changed.
