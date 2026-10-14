# Deskmates

**A team of AI agents that work in a 3D sitcom-style office. Free to run, easy to extend.**

You talk to a head agent (Michael). He plans the work, hands it to specialists in parallel, and you watch it happen. The agents read your documents and databases and cite their sources. They remember things, call tools, and can run on a schedule. You can hire, restyle and remove agents from the UI.

It runs entirely in Docker on free tiers: free Gemini keys (with Groq, OpenRouter and Cerebras as fallbacks), plus local embeddings and a local reranker. No paid services needed.

![The office](docs/screenshots/office-overview.jpg)

> **Looking for the details?** See **[docs/TECHNICAL.md](docs/TECHNICAL.md)**: architecture, every API endpoint, the data model, security, configuration and how to extend it.

## What you get

| | |
|---|---|
| **A living 3D office** | Reception, sales pod, accounting, kitchen, warehouse, a glass conference room, Michael's office and an MCP server closet. Agents sit, walk, open doors and hand work to each other. |
| **A manager that delegates** | Michael plans, sends tasks to specialists in parallel, and combines the answers. Ask him who is busy and he answers from live status. |
| **Your own knowledge** | PDF (including scans), Word, Excel, CSV, web pages, folders and read-only SQL databases. Answers cite numbered sources and link to the original file. |
| **Agents you create** | Pick from 24 presets, build your own with a 3D look editor, or generate an expert from your documents. |
| **Built for free tiers** | It rotates keys and models, falls back to other providers, and shows how much quota is left today. |
| **Automation** | Scheduled tasks, webhook triggers, Slack slash commands, and delivery to Slack, webhook or email. |
| **Teams** | Optional sign-in, workspaces, invite codes and admin / member / viewer roles. |
| **Measurable quality** | An evaluation suite, a search benchmark, and 👍 / 👎 feedback that can become permanent tests. |

## Quick start

```bash
cp backend/.env.example backend/.env     # add a free Gemini key, or add keys later in Settings
docker compose up --build
```

- App: http://localhost:3000. The office is at `/studio`, the single-chat page at `/`, the cast at `/cast`.
- API: http://localhost:8010, with interactive docs at `/docs`.

The first start downloads two small search models (about 200 MB, cached in a Docker volume).

**Try it:** open the office and ask Michael *"Who is busy right now, what are they doing, and where is everyone?"* Then upload [`docs/sample-data/office-handbook.md`](docs/sample-data/office-handbook.md) in **Knowledge** and ask *"Ask Angela how many days of paid leave the handbook gives, and ask Kevin how much parking costs."*

## A tour

**Ask the manager.** He answers about the team from real status, with Markdown formatting:

![Live status](docs/screenshots/live-status.jpg)

**Watch the handoff.** Tasks travel to specialists' desks and the replies come back:

![Handoff in progress](docs/screenshots/handoff-in-progress.jpg)

**Check the sources.** Every document answer carries numbered citations. Open one to see the passage and the original file:

![Citations](docs/screenshots/citations.jpg)

**Bring your own knowledge.** Upload files, add web pages, connect a database. See exactly how each document was cut up and why:

![Knowledge](docs/screenshots/knowledge-chunking-preview.jpg)

**Build agents.** Hire a preset, design your own, or turn documents into an expert:

| | |
|---|---|
| ![Hire](docs/screenshots/hire-agent.jpg) | ![Look editor](docs/screenshots/look-editor.jpg) |
| ![Expert from knowledge](docs/screenshots/expert-from-knowledge.jpg) | ![Custom agent](docs/screenshots/hire-custom-agent.jpg) |

**Stay within free quota.** See how much of each provider's daily allowance is left:

![Health and quota](docs/screenshots/health-quota.jpg)

**Measure quality.** The search benchmark and chat feedback live in Settings:

![Quality](docs/screenshots/quality.jpg)

**Automate and share.** Schedules, triggers and backups, plus team sign-in with roles:

| | |
|---|---|
| ![Automations](docs/screenshots/automations.jpg) | ![Team](docs/screenshots/team-invites.jpg) |

More screens (every room, the cast, sign-in, the viewer role) are in the [screenshot gallery](docs/TECHNICAL.md#screenshot-gallery).

## Configuration in one minute

Everything has a safe default. Only an AI key is required. The settings you are most likely to touch:

| Variable | What it does |
|---|---|
| `GEMINI_API_KEYS` | Free Google keys, comma separated. Use keys from different projects for more daily quota. |
| `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `CEREBRAS_API_KEY` | Fallback providers when Gemini runs out. |
| `AUTH_MODE=on` | Turns on sign-in and team workspaces. |
| `ADMIN_TOKEN` | Protects admin actions when sign-in is off. |
| `CASTCALL_ENV=dev` | Leaves admin screens open for local use (already set in `docker-compose.yml`). |

All variables are listed in [`backend/.env.example`](backend/.env.example) and explained in the [configuration reference](docs/TECHNICAL.md#14-configuration-reference). The `CASTCALL_` prefix on a few variable names is historical and kept so existing setups keep working.

## Run the tests

```bash
docker compose exec backend python -m pytest -q                  # unit and API tests
docker compose --profile test up -d pg-test mysql-test           # real Postgres and MySQL
docker compose exec backend python -m pytest -m integration -q
docker compose exec backend python -m evals.retrieval            # search benchmark (local, free)
docker compose run --rm --no-deps frontend npm run lint
docker compose run --rm --no-deps frontend npm run build
```

## Before you put it on the internet

Set `AUTH_MODE=on` (or at least `ADMIN_TOKEN`), remove `CASTCALL_ENV=dev` and `ALLOW_PRIVATE_HOSTS=true` from `docker-compose.yml`, serve it over HTTPS and set `TRUST_PROXY` and `CORS_ORIGINS`. The full checklist is in [Deployment](docs/TECHNICAL.md#15-deployment).

## Honest limits

- Free tiers are small. Gemini allows about 20 requests a day per model per project, so add keys from separate accounts and providers. The quota panel is an estimate.
- The benchmarks were generated or written for this project. Treat them as regression guards, not promises about your data.
- OCR uses a vision model and has been stress-tested only on generated scans. Sign-in, schedules and Slack/email/webhook delivery are covered by automated tests with stand-ins, not live services.
- SQLite is the default store. It suits a team; plan a move to a server database for heavy multi-user use.
- Anyone who holds a trigger link can use that trigger. Treat it like a password.
