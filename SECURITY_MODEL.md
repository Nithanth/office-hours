# Office Hours security model

Scope: `Nithanth/office-hours` (single repo). There is no infrastructure-as-code: prod runs on **Railway** (Dockerfile build, managed Postgres) and is configured in the Railway dashboard, so the deploy configuration here comes from `Dockerfile` and `src/config.py` only. Drafted from the code and refined with the owner's answers (marked **[owner]**). Security scans read this file, so keep it current.

## System overview and trust boundaries

A FastAPI + FastHTML web app for reading research papers: search (Exa / Semantic Scholar), select papers, then generate reports, notes, blogs and charts with LLMs (OpenRouter tiers, Gemini). Users can also save a library, track topics, chat with papers, and sync to Notion.

- **Prod access [owner]:** today mainly a personal tool, about to be shared with other users. Public internet, but **Google sign-in is required** (`REQUIRE_LOGIN` on). Without an `oh_auth` cookie, the only reachable paths are `/`, `/auth/*`, `/static/*`, `/healthz`, `/metrics`, `/api/me` and `/favicon.ico` (`src/middleware/auth_gate.py`). Anyone with a Google account can sign in; there is no email allowlist in the code.
- Boundary 1: browser to app (cookies: `oh_auth` signed user id, `oh_sid` signed anonymous session, `oh_oauth_state`).
- Boundary 2: app to third parties (Exa, OpenRouter, Gemini, Semantic Scholar, Unpaywall, arXiv/publisher PDF hosts, Notion API, Google OAuth).
- Boundary 3: app to Postgres (Railway) and local disk caches under `/app`.
- Untrusted content entering the app: paper text, HTML and PDFs fetched from arbitrary URLs, LLM output, and Vega specs (rendered in a sandboxed `/vega-frame`).

## Components

| Component | Code |
|---|---|
| **Web UI** | `src/pages/`, `src/templates/`, `static/js/`, `src/utils/html_builder.py`, `src/utils/md_render.py` |
| **API routes** | `src/api/routes/`, `src/api/routers/` (reports, library, topics, chat, search, share, settings, admin) |
| **Auth & sessions** | `src/services/auth/`, `src/middleware/`, `src/services/chat/chat_session.py`, `src/api/routes/auth.py` |
| **Persistence** | `src/services/persistence/`, `migrations/` |
| **Integrations** | `src/services/notion/`, `src/api/routes/integrations.py`, `src/services/core/` (OpenRouter BYOK, Exa, model prefs) |
| **Paper ingestion & LLM pipeline** | `src/services/paper/`, `src/services/report/`, `src/services/notes/`, `src/services/query/`, `src/services/topics/`, `src/prompts/` |
| **Build & deploy** | `Dockerfile`, `docker-compose.yml`, `pyproject.toml`, `uv.lock`, `requirements.lock`, `src/config.py` |

## Entry points and who can reach them

- Google OAuth login/callback (`/auth/google/*`) and the `next` redirect: anyone.
- Every other page and API (about 86 routes): any signed-in Google user.
- `/share/{token}`: public read-only topic share. It is not in the auth-gate public list, so with `REQUIRE_LOGIN` on it still needs sign-in (inferred from the code).
- `/admin/*`: bearer `ADMIN_API_KEY`, fails closed in prod (`src/api/routes/admin.py`).
- `/metrics`, `/healthz`: unauthenticated.
- Notion OAuth connect/callback and destination selection (`src/api/routes/integrations.py`).

## Assets and sensitivity [owner: all roughly equal]

1. **Users' stored secrets:** OpenRouter BYOK keys (`user_preferences.openrouter_key_encrypted`, Fernet via `OH_SECRET_ENCRYPTION_KEY`) and Notion OAuth tokens (`user_integrations`).
2. **Server-side secrets and spend:** `EXA_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, `SEMANTIC_SCHOLAR_API_KEY`, `OH_SESSION_SECRET`, `OH_SECRET_ENCRYPTION_KEY`, `ADMIN_API_KEY`, Google/Notion OAuth client secrets.
3. **Per-user data isolation:** reports, saved papers, topics, chats and preferences, keyed by `owner_id` (auth `user_id`, or `sess:{oh_sid}` before sign-in; claimed on login by `claim_anonymous_session`).

## Attacker profiles

- **Any signed-in Google user (primary):** can call every route and try to reach other users' data (IDOR on `topic_id`, `report_id`, `paper_id`), other users' stored keys or tokens, SSRF through paper-URL fetching, or XSS against other users through shared content.
- **Unauthenticated internet attacker:** can reach only the public paths above: OAuth flow abuse (open redirect via `next`, state/CSRF), `/metrics` information leaks, and cross-site requests against signed-in users (CSRF; cookies are `SameSite=Lax`).
- **Hostile content source:** a malicious paper page, PDF, or a publisher that serves crafted HTML/PDF to the ingestion pipeline (parser bugs, SSRF via redirects, stored XSS through rendered markdown).
- **Supply chain:** dependencies in `uv.lock` / `requirements.lock`, plus CDN scripts allowed by the CSP (jsdelivr, unpkg, plot.ly).

## Existing controls

- Signed cookies (itsdangerous), HttpOnly, Secure in prod, SameSite=Lax; the app refuses to start in non-dev with the default `OH_SESSION_SECRET` (`src/config.py`).
- OAuth state is signed with a 10-minute TTL; the `next` value must start with `/` (`//host` is not rejected; see open questions).
- Security headers and CSP, X-Frame-Options DENY, HSTS, HTTPS redirect in prod (`src/middleware/security.py`). The CSP allows `'unsafe-inline'` scripts.
- CORS allowlist from `AUTH_REDIRECT_BASE_URL`, with `allow_credentials=True`; it falls back to `*` if that is unset.
- SlowAPI rate limiting (about 14 decorated routes); anonymous-session limits in `src/config.py`.
- Fernet encryption for BYOK keys; owner-scoped repository queries (`owner_id=` arguments in `repositories.py`).
- Vega specs run in a sandboxed iframe (`/vega-frame`) with its own CSP, so global CSP has no `unsafe-eval`.
- Container runs as non-root `officehours`; migrations run at boot.

## Out of scope / accepted risk [owner]

Nothing is out of scope. The owner wants to open the tool to other people and minimise risk, so these are **in scope as real issues**:
- **LLM cost abuse / rate-limit bypass:** signed-in users burning server-side LLM/Exa/Gemini quota, getting around SlowAPI or anonymous-session limits, or causing unbounded fetch/embedding/LLM work.
- **Prompt injection via paper content:** paper text steering LLM output (summaries, chat, Notion comment replies), including injection that leaks other users' context or writes to Notion.
- **Dev/test-only code paths:** settings that make prod unsafe if `ENV` is wrong or unset (it defaults to `dev`), plus dev CORS origins, the admin key being optional outside prod, `scripts/`, `dev.sh` and `docker-compose.yml`.

## Open questions

- Is `AUTH_REDIRECT_BASE_URL` set in Railway? If not, CORS falls back to `*` with credentials.
- Should `/metrics` be public in prod?
- Is any email allowlist enforced outside the code (e.g., the Google OAuth consent screen set to "internal"/test users)?
