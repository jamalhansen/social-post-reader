from typing import List
import typer

from . import config
from .fetcher import SocialPost, fetch_bluesky_posts, fetch_mastodon_posts


class SocialReaderError(Exception):
    """Base typed error for social-post-reader."""


class ProviderSetupError(SocialReaderError):
    """Raised when provider resolution fails."""


_VALID_SOURCES = {"bluesky", "mastodon"}


def _parse_sources(sources_str: str) -> List[str]:
    parts = [s.strip().lower() for s in sources_str.split(",") if s.strip()]
    unknown = set(parts) - _VALID_SOURCES
    if unknown:
        typer.echo(f"Unknown sources: {unknown}. Valid: {_VALID_SOURCES}", err=True)
        raise typer.Exit(1)
    return parts


def _fetch_all_posts(sources: List[str]) -> List[SocialPost]:
    posts: List[SocialPost] = []

    if "bluesky" in sources:
        typer.echo(f"Fetching Bluesky posts for {len(config.KEYWORDS)} keywords...")
        bsky = fetch_bluesky_posts(
            keywords=config.KEYWORDS,
            handle=config.BLUESKY_HANDLE,
            app_password=config.BLUESKY_APP_PASSWORD,
            include_link_posts=config.INCLUDE_LINK_POSTS,
        )
        typer.echo(f"  → {len(bsky)} posts fetched from Bluesky")
        posts.extend(bsky)

    if "mastodon" in sources:
        typer.echo(f"Fetching Mastodon posts for {len(config.KEYWORDS)} keywords...")
        masto = fetch_mastodon_posts(
            keywords=config.KEYWORDS,
            instances=config.MASTODON_INSTANCES,
            include_link_posts=config.INCLUDE_LINK_POSTS,
        )
        typer.echo(f"  → {len(masto)} posts fetched from Mastodon")
        posts.extend(masto)

    return posts
