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

import requests
from local_first_common.text import is_english

logger = logging.getLogger(__name__)

_BSKY_SEARCH_URL = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"
_BSKY_AUTH_URL = "https://bsky.social/xrpc/com.atproto.server.createSession"


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


def _bsky_get_token(handle: str, app_password: str) -> str | None:
    """Authenticate with Bluesky and return an access token, or None on failure."""
    try:
        resp = requests.post(
            _BSKY_AUTH_URL,
            json={"identifier": handle, "password": app_password},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("accessJwt")
    except requests.RequestException as e:
        logger.warning("Bluesky auth failed: %s", e)
        return None


def _bsky_has_external_link(post: dict) -> bool:
    """Return True if the post has an embedded external link card."""
    embed = post.get("embed") or {}
    return bool(embed.get("external", {}).get("uri"))


def _bsky_post_url(post: dict) -> str:
    """Build a web URL for a Bluesky post from its record."""
    author = (post.get("author") or {}).get("handle", "")
    uri = post.get("uri", "")  # at://did:plc:.../app.bsky.feed.post/<rkey>
    rkey = uri.split("/")[-1] if "/" in uri else ""
    if author and rkey:
        return f"https://bsky.app/profile/{author}/post/{rkey}"
    return ""


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

    token: str | None = None
    if handle and app_password:  # nosec B107
        token = _bsky_get_token(handle, app_password)
        if token:
            logger.debug("Bluesky: authenticated as %s", handle)
        else:
            logger.warning("Bluesky: auth failed for %s — proceeding unauthenticated", handle)

    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    seen_uris: set[str] = set()
    posts: list[SocialPost] = []

    for keyword in keywords:
        try:
            resp = requests.get(
                _BSKY_SEARCH_URL,
                params={"q": keyword, "limit": limit_per_keyword},
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.warning("Bluesky fetch failed for %r: %s", keyword, e)
            continue

        for post in data.get("posts", []):
            uri = post.get("uri", "")
            if not uri or uri in seen_uris:
                continue

            has_link = _bsky_has_external_link(post)
            if has_link and not include_link_posts:
                continue

            seen_uris.add(uri)
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
                    post_url=_bsky_post_url(post),
                    reply_count=post.get("replyCount", 0),
                    like_count=post.get("likeCount", 0),
                    created_at=record.get("createdAt", ""),
                    has_external_link=has_link,
                    tags=facet_tags,
                )
            )

    return posts


def _keyword_to_hashtag(keyword: str) -> str:
    """Strip spaces and hyphens to form a valid Mastodon hashtag."""
    return keyword.replace(" ", "").replace("-", "")


def _mastodon_has_external_link(status: dict) -> bool:
    """Return True if the status has a link preview card."""
    return bool(status.get("card"))


def _mastodon_post_url(status: dict) -> str:
    return status.get("url", "")


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

    instances = instances or ["mastodon.social"]
    seen_urls: set[str] = set()
    posts: list[SocialPost] = []

    for instance in instances:
        for keyword in keywords:
            hashtag = _keyword_to_hashtag(keyword)
            url = f"https://{instance}/api/v1/timelines/tag/{hashtag}"
            try:
                resp = requests.get(url, params={"limit": limit_per_tag}, timeout=10)
                resp.raise_for_status()
                statuses = resp.json()
            except requests.RequestException as e:
                logger.warning("Mastodon fetch failed for %s #%s: %s", instance, hashtag, e)
                continue

            for status in statuses:
                post_url = _mastodon_post_url(status)
                if not post_url or post_url in seen_urls:
                    continue

                has_link = _mastodon_has_external_link(status)
                if has_link and not include_link_posts:
                    continue

                # Strip HTML from content
                content_html = status.get("content", "")
                text = _strip_html(content_html)

                if not text:
                    continue

                seen_urls.add(post_url)
                account = status.get("account") or {}
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


def _strip_html(html: str) -> str:
    """Remove HTML tags and decode common entities from a string."""
    import re

    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&apos;", "'")
    text = re.sub(r"\s+", " ", text).strip()
    return text
