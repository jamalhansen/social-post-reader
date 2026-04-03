"""Fetchers for Bluesky and Mastodon social posts.

Unlike the content-discovery-agent, which uses social platforms to find article
links, this module treats posts *themselves* as the content. It fetches text-heavy
posts (minimal or no external links) that are candidates for thoughtful replies.

Bluesky auth:
    Set BLUESKY_HANDLE and BLUESKY_APP_PASSWORD env vars for authenticated search.
    Unauthenticated search works but may hit rate limits on public.api.bsky.app.

Mastodon:
    Uses public hashtag timelines — no auth required.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from local_first_common.text import is_english, strip_html
from local_first_common.social import bluesky, mastodon

logger = logging.getLogger(__name__)


@dataclass
class SocialPost:
    """A single social media post that is a reply candidate."""

    platform: str  # "bluesky" | "mastodon"
    author_handle: str
    author_display_name: str
    text: str
    post_url: str
    reply_count: int
    like_count: int
    created_at: str  # ISO 8601 string
    has_external_link: bool = False
    tags: list[str] = field(default_factory=list)
    search_term: str | None = None


def fetch_bluesky_posts(
    keywords: list[str],
    handle: str = "",
    app_password: str = "",
    limit_per_keyword: int = 25,
    include_link_posts: bool = False,
) -> list[SocialPost]:
    """Search Bluesky for text-heavy posts matching the given keywords.

    Prioritises posts where the text itself is the substance (no external link
    card). Set include_link_posts=True to include link-sharing posts as well.

    Args:
        keywords: Search terms to query the Bluesky API.
        handle: Optional Bluesky handle for authenticated search.
        app_password: Optional App Password for authenticated search.
        limit_per_keyword: Max posts to fetch per keyword.
        include_link_posts: Include posts that embed external link cards.

    Returns:
        Deduplicated list of SocialPost objects.
    """
    if not keywords:
        return []

    token = None
    if handle and app_password:
        token = bluesky.get_auth_token(handle, app_password)

    raw_posts = []
    for keyword in keywords:
        for post in bluesky.fetch_posts([keyword], token=token, limit=limit_per_keyword):
            # Attach keyword metadata
            post["_keyword"] = keyword
            raw_posts.append(post)

    posts: list[SocialPost] = []

    for post in raw_posts:
        has_link = bluesky.has_external_link(post)
        if has_link and not include_link_posts:
            continue

        author = post.get("author") or {}
        record = post.get("record") or {}

        # Extract hashtag facets from richtext
        facet_tags: list[str] = []
        for facet in record.get("facets", []):
            for feature in facet.get("features", []):
                if feature.get("$type") == "app.bsky.richtext.facet#tag":
                    tag = feature.get("tag", "")
                    if tag:
                        facet_tags.append(tag.lower())

        posts.append(
            SocialPost(
                platform="bluesky",
                author_handle=author.get("handle", ""),
                author_display_name=author.get("displayName", ""),
                text=(record.get("text") or "").strip(),
                post_url=bluesky.get_post_url(post),
                reply_count=post.get("replyCount", 0),
                like_count=post.get("likeCount", 0),
                created_at=record.get("createdAt", ""),
                has_external_link=has_link,
                tags=facet_tags,
                search_term=post.get("_keyword"),
            )
        )

    return posts


def fetch_mastodon_posts(
    keywords: list[str],
    instances: list[str] | None = None,
    limit_per_tag: int = 40,
    include_link_posts: bool = False,
) -> list[SocialPost]:
    """Fetch text-heavy posts from Mastodon hashtag timelines.

    Uses the public /api/v1/timelines/tag/:hashtag endpoint — no auth required.
    Multi-word keywords are collapsed into single-word hashtags (spaces removed).

    Args:
        keywords: Keywords to use as hashtags.
        instances: Mastodon instances to query. Defaults to ["mastodon.social"].
        limit_per_tag: Max statuses to fetch per instance/hashtag combination.
        include_link_posts: Include posts that have a link preview card.

    Returns:
        Deduplicated list of SocialPost objects.
    """
    if not keywords:
        return []

    raw_statuses = []
    for keyword in keywords:
        for status in mastodon.fetch_posts([keyword], instances=instances, limit=limit_per_tag):
            # Attach keyword metadata
            status["_keyword"] = keyword
            raw_statuses.append(status)

    posts: list[SocialPost] = []

    for status in raw_statuses:
        post_url = status.get("url", "")
        if not post_url:
            continue

        has_link = bool(status.get("card"))
        if has_link and not include_link_posts:
            continue

        # Strip HTML from content
        content_html = status.get("content", "")
        text = strip_html(content_html)

        if not text:
            continue

        account = status.get("account") or {}
        instance = status.get("_instance", "unknown")
        tags = [t.get("name", "").lower() for t in status.get("tags", [])]

        posts.append(
            SocialPost(
                platform="mastodon",
                author_handle=f"{account.get('acct', '')}@{instance}",
                author_display_name=account.get("display_name", ""),
                text=text,
                post_url=post_url,
                reply_count=status.get("replies_count", 0),
                like_count=status.get("favourites_count", 0),
                created_at=status.get("created_at", ""),
                has_external_link=has_link,
                tags=tags,
                search_term=status.get("_keyword"),
            )
        )

    return posts


def filter_posts(
    posts: list[SocialPost],
    since_hours: int = 48,
    min_words: int = 10,
    english_only: bool = True,
) -> list[SocialPost]:
    """Drop posts that are too old, too short, or not in English.

    Args:
        posts: Posts to filter.
        since_hours: Drop posts older than this many hours. 0 = no age filter.
        min_words: Drop posts with fewer words than this.
        english_only: Drop posts not detected as English (default True).

    Returns:
        Filtered list. Posts with unparseable timestamps are kept (fail open).
        Language detection also fails open — ambiguous posts are kept.
    """
    now = datetime.now(tz=timezone.utc)
    out: list[SocialPost] = []
    for post in posts:
        if min_words and len(post.text.split()) < min_words:
            continue
        if since_hours and post.created_at:
            try:
                created = datetime.fromisoformat(
                    post.created_at.replace("Z", "+00:00")
                )
                age_hours = (now - created).total_seconds() / 3600
                if age_hours > since_hours:
                    continue
            except ValueError:
                pass  # unparseable timestamp — keep the post
        if english_only and not is_english(post.text):
            continue
        out.append(post)
    return out
