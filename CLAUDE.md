# CLAUDE.md

Context for Claude Code working in this repo. Read this before making changes.

## What this is

A lightweight macOS personal research assistant. Once a day it:

1. Pulls newly published papers from arXiv, OpenAlex, Semantic Scholar, and Google Scholar alerts.
2. Scores them against a profile built from the user's own ORCID publication record plus a hand-written `profile.md`.
3. Summarizes the top handful.
4. Picks **one** paper as "read this today" and explains why.
5. Delivers it as a macOS notification banner and an email to the user.

Single-user, local-first. Not a service, no accounts, no multi-tenancy.

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
      notify.py            # macOS banner
      email.py             # SMTP
    store.py               # SQLite
    cli.py
  digests/                 # YYYY-MM-DD.md
```

### Core data model

`Paper`: `id`, `source`, `title`, `authors`, `abstract`, `published_at`, `url`, `doi`, `venue`, `categories`, `score`, `score_reasons`, `summary`, `seen_at`, `state` (`new` | `shown` | `saved` | `dismissed`).

Every source module normalizes into this. Keep it stable.

### Storage

SQLite at `~/Library/Application Support/recto/db.sqlite`. Tables: `papers`, `runs`, `profile_terms`. Papers are kept forever — they're tiny, and this is how the tool avoids re-recommending.

### Scheduling

`launchd` user agent at `~/Library/LaunchAgents/com.<user>.recto.plist`, `StartCalendarInterval`, no long-lived daemon. If the Mac was asleep, launchd fires on wake — the run must tolerate "it's actually 3pm now."

## Sources

**arXiv** — official Atom API. Filter by category from `config.toml`, then by date. Rate limit: one request per 3 seconds, and set a real User-Agent with a contact email.

**OpenAlex** — free, no key, but use the polite pool by putting a contact email in the User-Agent and `mailto` param. Primary use beyond keyword search: **citation-graph channels** — works citing the user's own papers, and new works citing the same references the user cites. These are usually higher-precision than keyword matching.

**Semantic Scholar** — Graph API. Free tier is heavily rate-limited without a key; request one and store it in Keychain. Overlaps OpenAlex, so treat it as a secondary/enrichment source (abstracts, TLDRs) rather than a primary crawl. If it 429s, skip it silently.

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

- Anthropic Messages API via the official Python SDK. Key from Keychain or env, **never** in `config.toml` or the repo.
- The model string is a config value, not a hardcoded constant. Check current model names at https://docs.claude.com/en/docs/about-claude/models before setting the default.
- One batched request per digest, not one per paper.
- Summaries: 2–3 sentences, plain language, must state what's new relative to prior work. No marketing tone.
- The paper-of-the-day rationale must reference the user's own work explicitly ("this extends the method in your 2024 paper on X").
- If the API is unreachable, still deliver a digest with titles and abstracts, marked degraded.

## Delivery

Both channels fire on every successful run; each can be disabled in config.

**Notification** — macOS banner with the paper-of-the-day title. Clicking opens today's digest. Use a signed helper or `terminal-notifier`; note that a plain `osascript` notification cannot carry a click action reliably.

**Email** — HTML digest sent over SMTP to the user's own address. Credentials in Keychain, never in config. Assume an app-specific password. Subject line carries the paper of the day so it's readable from the lock screen. The email is self-contained: no images, no tracking, links only.

## Conventions

- Python 3.11+, `uv` for dependencies.
- Type hints everywhere; `ruff` for lint and format.
- All network calls go through one wrapper with timeout, backoff, and per-source rate limiting.
- No secrets, no absolute user paths, no `digests/` contents committed.
- Source parsers get fixture-based tests from recorded API responses. Tests never hit the network.

## Commands

```bash
uv run recto bootstrap        # ORCID → profile index, scaffold profile.md
uv run recto run              # full pipeline, deliver
uv run recto run --dry-run    # fetch + score, skip LLM and delivery
uv run recto fetch            # sources only, print counts per source
uv run recto show             # print latest digest to stdout
uv run recto why <paper_id>   # print score breakdown for one paper
uv run recto install-agent    # write and load the launchd plist
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
