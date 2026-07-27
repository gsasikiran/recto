# recto

![platform](https://img.shields.io/badge/platform-macOS-lightgrey?logo=apple)
![python](https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white)
![tests](https://img.shields.io/badge/tests-89%20passing-brightgreen)
![coverage](https://img.shields.io/badge/coverage-71%25-yellow)
![lint](https://img.shields.io/badge/lint-ruff-261230)
![license](https://img.shields.io/badge/license-MIT-blue)

A lightweight macOS personal research assistant. Once a day it pulls newly
published papers from arXiv, OpenAlex, Semantic Scholar, and Google Scholar
alerts, scores them against a profile built from your ORCID publication
record and a hand-written `profile.md`, summarizes the top handful, picks
**one** paper as "read this today," and delivers it as a macOS notification
and an email.

Single-user, local-first. No web backend, no accounts, no database server.

## How it works

```
fetch → dedupe → score (no LLM) → shortlist → summarize (LLM) → pick → render → deliver
```

- **Fetch** — arXiv (official Atom API), OpenAlex (keyword search + citation
  graph), Semantic Scholar (enrichment), and Google Scholar Alerts (parsed
  from a mail folder over IMAP — no scraping).
- **Score** — BM25/TF-IDF similarity against your profile corpus, plus
  boosts for citation-graph hits, co-author matches, and configured
  keywords. No LLM calls at this stage.
- **Summarize & pick** — one batched Claude API call over the shortlist:
  2–3 sentence summaries plus a single paper-of-the-day pick with a
  rationale tied to your own prior work.
- **Deliver** — a macOS notification banner and an HTML email, both
  optional and independently configurable.

Every run writes a plain Markdown digest to `digests/YYYY-MM-DD.md` that you
can read without the app. Re-running a day overwrites that day's digest —
it never duplicates or re-recommends.

## Requirements

- macOS
- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- An Anthropic API key (via OpenRouter or direct, see `config.toml`)

## Setup

```bash
uv sync
cp config.example.toml config.toml
cp profile.md.example profile.md
```

`config.toml` and `profile.md` are both gitignored — the former holds your
SMTP host/username, the latter your actual research interests, and neither
belongs in a public repo. `config.example.toml` and `profile.md.example` are
the checked-in templates for what's expected. Fill in `config.toml`:

- `[general].contact_email` — used in API User-Agent headers (arXiv,
  OpenAlex require this).
- `[profile].orcid_id` — your ORCID iD, used to bootstrap your interests
  from your own publication record.
- `[topics]` — arXiv categories and keywords to track.
- `[delivery]` — enable/disable notification and email, and set SMTP
  details for email.
- `[llm]` — model and provider for the summarize/rerank pass.

Edit your local `profile.md` with prose about your current research
interests, including things you haven't published on yet — it overrides
ORCID where they conflict.

Secrets (API keys, SMTP password) go in the macOS Keychain or environment,
**never** in `config.toml`.

## Usage

```bash
uv run recto bootstrap        # ORCID → profile index, scaffold profile.md
uv run recto run              # full pipeline, deliver
uv run recto run --dry-run    # fetch + score, skip LLM and delivery
uv run recto fetch            # sources only, print counts per source
uv run recto show             # print latest digest to stdout
uv run recto why <paper_id>   # print score breakdown for one paper
uv run recto install-agent    # write and load the launchd plist for daily runs
```

`install-agent` sets up a `launchd` user agent (`StartCalendarInterval`, no
long-lived daemon) that runs `recto run` once a day at the time set in
`config.toml`'s `[schedule]`.

## Development

```bash
uv run pytest                                        # source parsers are tested against recorded fixtures, no network calls
uv run pytest --cov=src/recto --cov-report=term-missing  # coverage report
uv run ruff check .
uv run ruff format .
```

Badges above (tests/coverage) are static snapshots, not auto-updating — there's
no CI configured for this single-user project. Re-run the commands above and
update them by hand if they drift.

## Project layout

```
config.toml          # topics, sources, schedule, model, delivery
profile.md            # prose description of research interests (user-editable)
src/recto/
  sources/            # arxiv, openalex, semanticscholar, scholar_alerts — each returns list[Paper]
  profile/            # orcid.py, build.py — profile.md + ORCID corpus → scoring index
  dedupe.py
  score.py
  summarize.py
  pick.py
  render.py           # Markdown + HTML
  deliver/            # notify.py (macOS banner), email.py (SMTP)
  store.py            # SQLite persistence
  cli.py
digests/               # YYYY-MM-DD.md, not committed
```

Papers are stored forever in SQLite at
`~/Library/Application Support/recto/db.sqlite` — that's how re-recommending
the same paper is avoided.
