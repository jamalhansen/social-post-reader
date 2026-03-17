"""Configuration for social-post-reader.

Personal settings live in ~/.social-post-reader.toml (gitignored).
Environment variables override TOML values where noted.

Example ~/.social-post-reader.toml:

    [social]
    keywords = ["duckdb", "python", "local ai", "sqlite", "llm", "sql"]
    mastodon_instances = ["mastodon.social", "fosstodon.org"]

    [profile]
    description = \"\"\"
    I write and teach SQL, Python, and data engineering for working developers.
    I run the "Vibe Coded and Lived to Tell" blog series about building local-first
    AI tools. My day job involves data pipelines and internal tooling at a large
    financial institution. Active series: local-first AI tools, SQL for analysts.
    I'm interested in: local LLMs, DuckDB, SQLite, teaching pedagogy, developer
    experience, data engineering patterns.
    \"\"\"

    [settings]
    threshold = 0.5
    max_candidates = 5
    provider = "local"
    store = "~/.local-first/social-post-reader.db"
"""

import os
import tomllib
from pathlib import Path


_CONFIG_FILE = Path.home() / ".social-post-reader.toml"


def _load_toml() -> dict:
    if _CONFIG_FILE.exists():
        with open(_CONFIG_FILE, "rb") as f:
            return tomllib.load(f)
    return {}


_cfg = _load_toml()

# ── Social sources ────────────────────────────────────────────────────────────
_social_cfg = _cfg.get("social", {})

KEYWORDS: list[str] = _social_cfg.get(
    "keywords",
    ["duckdb", "python", "local ai", "sqlite", "llm", "sql"],
)
MASTODON_INSTANCES: list[str] = _social_cfg.get(
    "mastodon_instances", ["mastodon.social", "fosstodon.org"]
)
INCLUDE_LINK_POSTS: bool = _social_cfg.get("include_link_posts", False)

# ── Profile for angle generation ─────────────────────────────────────────────
_profile_cfg = _cfg.get("profile", {})

_FALLBACK_PROFILE = (
    "I write about SQL, Python, data engineering, and local-first AI tools. "
    "I'm interested in local LLMs, DuckDB, SQLite, teaching, and developer tooling."
)
PROFILE: str = _profile_cfg.get("description", _FALLBACK_PROFILE).strip()

# ── Settings ──────────────────────────────────────────────────────────────────
_settings = _cfg.get("settings", {})

DEFAULT_THRESHOLD: float = _settings.get("threshold", 0.5)
DEFAULT_MAX_CANDIDATES: int = _settings.get("max_candidates", 5)
DEFAULT_PROVIDER: str = os.environ.get("MODEL_PROVIDER") or _settings.get("provider", "local")

def _resolve_db_path() -> str:
    """Resolve the SQLite DB path with three-tier priority:

    1. SOCIAL_POST_READER_STORE env var
    2. [settings] store in ~/.social-post-reader.toml
    3. ~/sync/social-reader/social-post-reader.db — if the directory exists
       (place the DB in a cloud-synced folder by creating ~/sync/social-reader/)
    4. ~/.local-first/local-first.db — shared coordination DB (legacy fallback)
    """
    if os.environ.get("SOCIAL_POST_READER_STORE"):
        return os.path.expanduser(os.environ["SOCIAL_POST_READER_STORE"])
    if _settings.get("store"):
        return os.path.expanduser(_settings["store"])
    sync_dir = Path.home() / "sync" / "social-reader"
    if sync_dir.exists():
        return str(sync_dir / "social-post-reader.db")
    return str(Path.home() / ".local-first" / "local-first.db")


STORE_PATH: str = _resolve_db_path()

# ── Bluesky auth ──────────────────────────────────────────────────────────────
# Generate an App Password: Bluesky → Settings → Privacy and Security → App Passwords
BLUESKY_HANDLE: str = os.environ.get("BLUESKY_HANDLE", "")
BLUESKY_APP_PASSWORD: str = os.environ.get("BLUESKY_APP_PASSWORD", "")  # nosec B105
