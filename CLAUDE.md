# CLAUDE.md

Context for Claude Code working in this repo. Read this before making changes.

## What this is

A lightweight personal research assistant for macOS, Linux, and Windows.
Once a day it:

1. Pulls newly published papers from arXiv, OpenAlex, Semantic Scholar, and Google Scholar alerts.
2. Scores them against a profile built from the user's own ORCID publication record plus a hand-written `profile.md`.
3. Summarizes the top handful.
4. Picks **one** paper as "read this today" and explains why.
5. Delivers it as a desktop notification banner and an email to the user.

Single-user, local-first. Not a service, no accounts, no multi-tenancy.

macOS is the primary development target; Linux and Windows are supported and
must stay working. Platform differences are confined to four modules —
`config.py` (paths), `keychain.py` (secrets), `scheduler.py` (daily run), and
`deliver/notify.py` (banner). Nothing else in the codebase should branch on
the OS or hardcode a platform path.

## Non-goals

- No web backend, no hosting, no database server.
- No full-text PDF ingestion in v1 — abstracts and metadata only.
- No learned recommender. Scoring is lexical similarity + citation graph + LLM rerank.
- No Electron. No torch-sized dependencies without explicit approval.

## Design principles

- **Small.** A new heavyweight dependency needs approval before it goes in.
- **Fails quietly, logs loudly.** A dead API degrades the digest; it never crashes the run.
- **Cheap.** One LLM pass per day over a shortlist, never over every candidate.
- **Inspectable.** Every run writes plain Markdown the user can read without the app.
- **Idempotent.** Re-running a day overwrites that day's digest; it doesn't duplicate or re-recommend.

## Architecture

```
fetch → dedupe → score (no LLM) → shortlist → summarize (LLM) → pick → render → deliver
```

```
recto/
  config.toml              # topics, sources, schedule, model, delivery
  profile.md               # prose description of research interests (user-editable)
  src/
    sources/               # every module returns list[Paper]
      arxiv.py
      openalex.py
      semanticscholar.py
      scholar_alerts.py    # parses Google Scholar alert emails (see below)
    profile/
      orcid.py             # pulls the user's own works
      build.py             # profile.md + ORCID corpus → scoring index
    dedupe.py
    score.py
    summarize.py
    pick.py
    render.py              # Markdown + HTML
    deliver/
      notify.py            # desktop banner (osascript / notify-send / PowerShell)
      email.py             # SMTP
    store.py               # SQLite
    keychain.py            # OS secret store, one backend per platform
    scheduler.py           # daily schedule: launchd / systemd / Task Scheduler
    cli.py
  digests/                 # YYYY-MM-DD.md
```

### Core data model

`Paper`: `id`, `source`, `title`, `authors`, `abstract`, `published_at`, `url`, `doi`, `venue`, `categories`, `score`, `score_reasons`, `summary`, `seen_at`, `state` (`new` | `shown` | `saved` | `dismissed`).

Every source module normalizes into this. Keep it stable.

### Storage

SQLite at `<data dir>/db.sqlite`. Tables: `papers`, `runs`, `profile_terms`. Papers are kept forever — they're tiny, and this is how the tool avoids re-recommending.

`config.data_dir()` and `config.logs_dir()` are the only places that know the per-OS locations: `~/Library/Application Support/recto` and `~/Library/Logs/recto` on macOS, XDG (`~/.local/share/recto`, `~/.local/state/recto/logs`) on Linux, `%LOCALAPPDATA%\recto` on Windows. `RECTO_DATA_DIR` / `RECTO_LOGS_DIR` override them. Call those functions; never rebuild the path.

### Secrets

`keychain.py` keeps its macOS-era name but dispatches per platform: `security` on macOS, `secret-tool` (libsecret) on Linux, a DPAPI-encrypted file on Windows, and a `0600` JSON file when none of those is reachable. A `RECTO_<SERVICE>_SECRET` environment variable takes priority everywhere, which is how a headless box runs with no keyring. Callers only ever touch `get_secret` / `set_secret` / `delete_secret`.

### Scheduling

`scheduler.install_agent()` dispatches to one backend per OS, none of them a long-lived daemon:

- **macOS** — launchd user agent at `~/Library/LaunchAgents/com.<user>.recto.plist`, `StartCalendarInterval` + `RunAtLoad`.
- **Linux** — systemd user timer at `~/.config/systemd/user/recto.{service,timer}`, `OnCalendar` + `Persistent=true`. Needs `loginctl enable-linger` to fire without a login session.
- **Windows** — Task Scheduler task registered via `schtasks /Create /XML` (the XML file must be UTF-16), `StartWhenAvailable` + a logon trigger.

If the machine was asleep or off, every backend fires late — the run must tolerate "it's actually 3pm now." The run is idempotent, so the extra login/boot trigger is harmless.

## Sources

**arXiv** — official Atom API. Filter by category from `config.toml`, then by date. Rate limit: one request per 3 seconds, and set a real User-Agent with a contact email.

**OpenAlex** — free, no key, but use the polite pool by putting a contact email in the User-Agent and `mailto` param. Primary use beyond keyword search: **citation-graph channels** — works citing the user's own papers, and new works citing the same references the user cites. These are usually higher-precision than keyword matching.

**Semantic Scholar** — Graph API. Free tier is heavily rate-limited without a key; request one and store it via `keychain.py`. Overlaps OpenAlex, so treat it as a secondary/enrichment source (abstracts, TLDRs) rather than a primary crawl. If it 429s, skip it silently.

**Google Scholar** — there is no official API, and scraping it violates their terms and gets IP-blocked fast. Do **not** write a scraper. The supported path:

- The user creates Google Scholar Alerts (for their own topics, and "citations to my articles") and routes them to a dedicated mail folder.
- `scholar_alerts.py` reads that folder over IMAP and parses the alert emails into `Paper` objects.
- Titles and links are reliable from these emails; abstracts are truncated, so re-resolve each item against OpenAlex/Semantic Scholar by title to fill in metadata.
- Optional alternative behind a config flag: a paid SERP provider with a Google Scholar endpoint. Off by default — it costs money and adds a key.

## Relevance model

Profile is built from two inputs, merged:

1. **ORCID** (`orcid.py`) — pull the user's works via the public ORCID API using their ORCID iD, then resolve each to OpenAlex for abstracts, concepts, and reference lists. This is the bootstrap: no manual topic list required. Recent papers weighted higher than old ones.
2. **`profile.md`** — hand-written prose about current interests, including things the user hasn't published on yet. Overrides ORCID where they conflict, since it reflects where the user is going rather than where they've been.

Scoring runs in two stages:

- **Prefilter, no LLM:** BM25/TF-IDF similarity against the profile corpus, plus boosts for citation-graph hits, co-author matches, and configured keywords. Cut ~200 candidates to ~15. Use scikit-learn; a local sentence-embedding model is allowed only behind an opt-in flag, because it drags in torch.
- **LLM rerank:** summarize the shortlist, rank, pick one. Structured JSON output only — never parse prose.

Feedback: `saved` weights terms up, `dismissed` down. Record the signal from day one even if v1 barely uses it.

## LLM usage

- Anthropic Messages API via the official Python SDK. Key from the OS secret store or env, **never** in `config.toml` or the repo.
- The model string is a config value, not a hardcoded constant. Check current model names at https://docs.claude.com/en/docs/about-claude/models before setting the default.
- One batched request per digest, not one per paper.
- Summaries: 2–3 sentences, plain language, must state what's new relative to prior work. No marketing tone.
- The paper-of-the-day rationale must reference the user's own work explicitly ("this extends the method in your 2024 paper on X").
- If the API is unreachable, still deliver a digest with titles and abstracts, marked degraded.

## Delivery

Both channels fire on every successful run; each can be disabled in config.

**Notification** — desktop banner with the paper-of-the-day title: `osascript` on macOS, `notify-send` on Linux, a PowerShell balloon tip on Windows. None of these carries a click action reliably (on macOS that needs a signed helper or `terminal-notifier`), so it's a banner only. A missing backend logs and skips; it never fails the run.

**Email** — HTML digest sent over SMTP to the user's own address. Credentials in the OS secret store, never in config. The SSL context is built from the live System Roots keychain on macOS only (the stdlib default there lags the OS trust store); Linux and Windows use `ssl.create_default_context()`. Assume an app-specific password. Subject line carries the paper of the day so it's readable from the lock screen. The email is self-contained: no images, no tracking, links only.

## Conventions

- Python 3.11+, `uv` for dependencies.
- Type hints everywhere; `ruff` for lint and format.
- Cross-platform hygiene: always pass `encoding="utf-8"` to `read_text` / `write_text` / `FileHandler` (Windows defaults to the locale codepage), build paths with `pathlib`, and branch on `config.IS_MACOS` / `IS_LINUX` / `IS_WINDOWS` rather than reading `sys.platform` directly — tests monkeypatch those flags to exercise every OS branch from one machine.
- All network calls go through one wrapper with timeout, backoff, and per-source rate limiting.
- No secrets, no absolute user paths, no `digests/` contents committed.
- Source parsers get fixture-based tests from recorded API responses. Tests never hit the network.
- Tests never touch the real data dir or the real keychain: set `RECTO_DATA_DIR` and stub the platform CLIs. The whole suite must pass on all three OSes.

## Commands

```bash
uv run recto bootstrap        # ORCID → profile index, scaffold profile.md
uv run recto run              # full pipeline, deliver
uv run recto run --dry-run    # fetch + score, skip LLM and delivery
uv run recto fetch            # sources only, print counts per source
uv run recto show             # print latest digest to stdout
uv run recto why <paper_id>   # print score breakdown for one paper
uv run recto install-agent    # register the daily schedule with the OS
```

## Working agreement

- Ask before adding a dependency, a new source, or a background process.
- Prefer editing an existing module over adding a new one.
- When changing scoring, show a before/after on a real day's candidates — don't describe the change abstractly.

## Still unset

- ORCID iD.
- Delivery email address and SMTP host.
- Scheduled run time.
- Whether Semantic Scholar API key has been requested.
