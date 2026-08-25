"""config.toml loading/writing and filesystem locations.

config.toml is only ever *written* by `recto bootstrap`, via the template
string below — never by a TOML-writer library. It is read with stdlib
tomllib. Secrets never live here; see keychain.py.

This module is also the single place that knows where recto's data lives on
each OS, so the rest of the codebase never hardcodes a platform path:

  macOS    ~/Library/Application Support/recto,  ~/Library/Logs/recto
  Linux    $XDG_DATA_HOME/recto  (~/.local/share/recto),
           $XDG_STATE_HOME/recto/logs  (~/.local/state/recto/logs)
  Windows  %LOCALAPPDATA%\\recto,  %LOCALAPPDATA%\\recto\\logs

RECTO_DATA_DIR overrides the data location everywhere (useful for tests, a
synced folder, or a portable install); RECTO_LOGS_DIR does the same for logs.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import TypeVar

DEFAULT_ARXIV_CATEGORIES = ["cs.AI", "cs.LG", "stat.ML", "cs.MA"]
DEFAULT_LLM_MODEL = "anthropic/claude-sonnet-5"

IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")


def home_dir() -> Path:
    env = os.environ.get("RECTO_HOME")
    return Path(env).expanduser().resolve() if env else Path.cwd()


def config_path(home: Path | None = None) -> Path:
    return (home or home_dir()) / "config.toml"


def env_dir(var: str) -> Path | None:
    """Path from an environment variable, or None when it is unset/empty."""
    value = os.environ.get(var)
    return Path(value).expanduser() if value else None


def data_dir() -> Path:
    """Per-user data directory (SQLite db, profile index, file secret store)."""
    d = env_dir("RECTO_DATA_DIR")
    if d is None:
        if IS_MACOS:
            d = Path.home() / "Library" / "Application Support" / "recto"
        elif IS_WINDOWS:
            base = env_dir("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
            d = base / "recto"
        else:
            base = env_dir("XDG_DATA_HOME") or Path.home() / ".local" / "share"
            d = base / "recto"
    d.mkdir(parents=True, exist_ok=True)
    return d


# Kept as an alias: this was the macOS-only name before cross-platform support.
app_support_dir = data_dir


def db_path() -> Path:
    return data_dir() / "db.sqlite"


def profile_index_path() -> Path:
    return data_dir() / "profile_index.pkl"


def logs_dir() -> Path:
    d = env_dir("RECTO_LOGS_DIR")
    if d is None:
        if IS_MACOS:
            d = Path.home() / "Library" / "Logs" / "recto"
        elif IS_WINDOWS:
            d = data_dir() / "logs"
        else:
            base = env_dir("XDG_STATE_HOME") or Path.home() / ".local" / "state"
            d = base / "recto" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def digests_dir(home: Path | None = None) -> Path:
    d = (home or home_dir()) / "digests"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass(slots=True)
class GeneralConfig:
    contact_email: str = ""


@dataclass(slots=True)
class ProfileConfig:
    orcid_id: str = ""
    profile_md_path: str = "profile.md"
    # Shorter half-life = recent ORCID works count disproportionately more
    # than old ones when building the profile vector.
    recency_half_life_days: int = 365
    # profile.md's weight = this multiplier * the combined weight of your
    # whole ORCID corpus, so it stays dominant no matter how many ORCID
    # works you have.
    profile_md_dominance: float = 2.0


@dataclass(slots=True)
class TopicsConfig:
    arxiv_categories: list[str] = field(default_factory=lambda: list(DEFAULT_ARXIV_CATEGORIES))
    keywords: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ArxivSourceConfig:
    enabled: bool = True
    max_results_per_query: int = 200
    lookback_days: int = 2
    min_request_interval_seconds: float = 3.0


@dataclass(slots=True)
class OpenAlexSourceConfig:
    enabled: bool = True
    max_results: int = 200
    lookback_days: int = 14
    citation_graph_enabled: bool = True
    citation_graph_max_seed_works: int = 25


@dataclass(slots=True)
class SemanticScholarSourceConfig:
    enabled: bool = True
    api_key_in_keychain: bool = False
    base_delay_seconds: float = 1.0
    max_retries: int = 2


@dataclass(slots=True)
class ScholarAlertsSourceConfig:
    enabled: bool = False
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    mail_folder: str = "Scholar Alerts"
    mark_as_read: bool = False
    lookback_days: int = 2


@dataclass(slots=True)
class SourcesConfig:
    arxiv: ArxivSourceConfig = field(default_factory=ArxivSourceConfig)
    openalex: OpenAlexSourceConfig = field(default_factory=OpenAlexSourceConfig)
    semanticscholar: SemanticScholarSourceConfig = field(
        default_factory=SemanticScholarSourceConfig
    )
    scholar_alerts: ScholarAlertsSourceConfig = field(default_factory=ScholarAlertsSourceConfig)


@dataclass(slots=True)
class ScoringConfig:
    # Safety bound only, applied AFTER scoring (keeps top-scoring candidates
    # if the never-shown backlog gets huge) — not a recency pre-filter.
    candidate_cap: int = 1000
    shortlist_size: int = 15
    recent_slots: int = 2
    tfidf_max_features: int = 20000
    weight_tfidf: float = 1.0
    weight_citation_graph: float = 0.5
    weight_coauthor: float = 0.3
    weight_keyword: float = 0.2
    weight_feedback: float = 0.4


@dataclass(slots=True)
class LLMConfig:
    provider: str = "openrouter"
    model: str = DEFAULT_LLM_MODEL
    api_base: str = "https://openrouter.ai/api/v1/chat/completions"
    timeout_seconds: float = 60.0
    max_retries: int = 2


@dataclass(slots=True)
class EmailDeliveryConfig:
    smtp_host: str = "smtp.office365.com"
    smtp_port: int = 587
    smtp_starttls: bool = True
    from_address: str = ""
    to_address: str = ""
    # Some hybrid Exchange/AD tenants require a DOMAIN\username down-level
    # logon name for SMTP AUTH, distinct from the From/To email address.
    # Empty means "use from_address" (the common case).
    smtp_username: str = ""
    subject_prefix: str = "[recto]"


@dataclass(slots=True)
class DeliveryConfig:
    notification: bool = False
    email: bool = True
    email_cfg: EmailDeliveryConfig = field(default_factory=EmailDeliveryConfig)


@dataclass(slots=True)
class ScheduleConfig:
    hour: int = 8
    minute: int = 0


@dataclass(slots=True)
class Config:
    general: GeneralConfig = field(default_factory=GeneralConfig)
    profile: ProfileConfig = field(default_factory=ProfileConfig)
    topics: TopicsConfig = field(default_factory=TopicsConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    delivery: DeliveryConfig = field(default_factory=DeliveryConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)


T = TypeVar("T")


def _from_dict(cls: type[T], raw: dict) -> T:
    obj = cls()
    for f in fields(cls):
        if f.name in raw:
            setattr(obj, f.name, raw[f.name])
    return obj


def load_config(home: Path | None = None) -> Config:
    path = config_path(home)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run `recto bootstrap` first.")
    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    sources_raw = raw.get("sources", {})
    delivery_raw = raw.get("delivery", {})
    smtp_raw = delivery_raw.get("smtp", {})

    return Config(
        general=_from_dict(GeneralConfig, raw.get("general", {})),
        profile=_from_dict(ProfileConfig, raw.get("profile", {})),
        topics=_from_dict(TopicsConfig, raw.get("topics", {})),
        sources=SourcesConfig(
            arxiv=_from_dict(ArxivSourceConfig, sources_raw.get("arxiv", {})),
            openalex=_from_dict(OpenAlexSourceConfig, sources_raw.get("openalex", {})),
            semanticscholar=_from_dict(
                SemanticScholarSourceConfig, sources_raw.get("semanticscholar", {})
            ),
            scholar_alerts=_from_dict(
                ScholarAlertsSourceConfig, sources_raw.get("scholar_alerts", {})
            ),
        ),
        scoring=_from_dict(ScoringConfig, raw.get("scoring", {})),
        llm=_from_dict(LLMConfig, raw.get("llm", {})),
        delivery=DeliveryConfig(
            notification=delivery_raw.get("notification", False),
            email=delivery_raw.get("email", True),
            email_cfg=_from_dict(EmailDeliveryConfig, smtp_raw),
        ),
        schedule=_from_dict(ScheduleConfig, raw.get("schedule", {})),
    )


CONFIG_TEMPLATE = """\
[general]
contact_email = "{contact_email}"

[profile]
orcid_id = "{orcid_id}"
profile_md_path = "profile.md"
recency_half_life_days = 365
profile_md_dominance = 2.0

[topics]
arxiv_categories = {arxiv_categories}
keywords = []

[sources.arxiv]
enabled = true
max_results_per_query = 200
lookback_days = 2
min_request_interval_seconds = 3.0

[sources.openalex]
enabled = true
max_results = 200
lookback_days = 14
citation_graph_enabled = true
citation_graph_max_seed_works = 25

[sources.semanticscholar]
enabled = true
api_key_in_keychain = false
base_delay_seconds = 1.0
max_retries = 2

[sources.scholar_alerts]
enabled = false
imap_host = ""
imap_port = 993
imap_username = ""
mail_folder = "Scholar Alerts"
mark_as_read = false
lookback_days = 2

[scoring]
candidate_cap = 1000
shortlist_size = 15
recent_slots = 2
tfidf_max_features = 20000
weight_tfidf = 1.0
weight_citation_graph = 0.5
weight_coauthor = 0.3
weight_keyword = 0.2
weight_feedback = 0.4

[llm]
provider = "openrouter"
model = "{llm_model}"
api_base = "https://openrouter.ai/api/v1/chat/completions"
timeout_seconds = 60
max_retries = 2

[delivery]
notification = false
email = true

[delivery.smtp]
smtp_host = "smtp.office365.com"
smtp_port = 587
smtp_starttls = true
from_address = "{email_address}"
to_address = "{email_address}"
smtp_username = ""
subject_prefix = "[recto]"

[schedule]
hour = {schedule_hour}
minute = {schedule_minute}
"""


def render_config_toml(
    *,
    contact_email: str,
    orcid_id: str,
    email_address: str,
    arxiv_categories: list[str] | None = None,
    llm_model: str = DEFAULT_LLM_MODEL,
    schedule_hour: int = 8,
    schedule_minute: int = 0,
) -> str:
    cats = arxiv_categories or DEFAULT_ARXIV_CATEGORIES
    cats_toml = "[" + ", ".join(f'"{c}"' for c in cats) + "]"
    return CONFIG_TEMPLATE.format(
        contact_email=contact_email,
        orcid_id=orcid_id,
        arxiv_categories=cats_toml,
        email_address=email_address,
        llm_model=llm_model,
        schedule_hour=schedule_hour,
        schedule_minute=schedule_minute,
    )


def write_default_config(
    *,
    home: Path | None = None,
    contact_email: str,
    orcid_id: str,
    email_address: str,
    arxiv_categories: list[str] | None = None,
) -> Path:
    path = config_path(home)
    if path.exists():
        return path
    path.write_text(
        render_config_toml(
            contact_email=contact_email,
            orcid_id=orcid_id,
            email_address=email_address,
            arxiv_categories=arxiv_categories,
        ),
        encoding="utf-8",
    )
    return path
