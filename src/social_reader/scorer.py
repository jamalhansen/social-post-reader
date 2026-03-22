"""Score social posts as reply candidates and generate a personal angle.


Each post is sent to an LLM with:
  - A profile describing the user's background, active writing, and expertise
  - The post text and metadata

The model returns a JSON object:
  {
    "score": 0.0-1.0,  // how strong a reply candidate this is
    "angle": "..."      // one-sentence prompt: what the user could add
  }

score interpretation:
  0.0 – 0.4  Not relevant or nothing to add
  0.4 – 0.6  Mildly interesting
  0.6 – 0.8  Good candidate
  0.8 – 1.0  Strong candidate — clear overlap and real angle

The angle is a prompt, not a script. It sketches the reply, it doesn't write it.
"""

import json
import logging
import time
from dataclasses import dataclass

from .fetcher import SocialPost
from local_first_common.tracking import timed_run

logger = logging.getLogger(__name__)

_SYSTEM_TEMPLATE = """\
You score social media posts as reply candidates for a specific person.

Here is their profile:
{profile}

For each post, decide:
1. How strong a reply candidate is this? (score 0.0–1.0)
   - High scores: the person has genuine expertise or experience directly relevant to the post's topic
   - Medium scores: loosely related, but they'd have to stretch to contribute meaningfully
   - Low scores: no clear connection or nothing to add beyond agreement
2. What angle could they bring? One sentence only. A thought-starter, not a draft reply.
   If score is below 0.4, set angle to an empty string.

Reply with ONLY valid JSON. No markdown fences. No extra keys.
Format:
{{"score": 0.75, "angle": "..."}}
"""

_USER_TEMPLATE = """\
Platform: {platform}
Author: {author} ({handle})
Reply count: {reply_count}
Post:
{text}
"""


@dataclass
class ScoredPost:
    """A social post with a relevance score and suggested reply angle."""

    post: SocialPost
    score: float
    angle: str


def score_post(
    post: SocialPost,
    profile: str,
    provider,
) -> ScoredPost:
    """Score a single post and generate an angle using the given provider.

    Args:
        post: The social post to evaluate.
        profile: Free-form description of the user's background and writing.
        provider: A local-first-common BaseProvider instance.

    Returns:
        ScoredPost with score and angle, or score=0.0/angle="" on LLM failure.
    """
    system = _SYSTEM_TEMPLATE.format(profile=profile.strip())
    user = _USER_TEMPLATE.format(
        platform=post.platform,
        author=post.author_display_name or post.author_handle,
        handle=post.author_handle,
        reply_count=post.reply_count,
        text=post.text[:1000],  # cap to avoid blowing context on long posts
    )

    for attempt in range(4):
        try:
            raw = provider.complete(system, user)
            data = _parse_response(raw)
            score = float(data.get("score", 0.0))
            score = max(0.0, min(1.0, score))
            angle = str(data.get("angle", "")).strip()
            if score < 0.4:
                angle = ""
            return ScoredPost(post=post, score=score, angle=angle)
        except Exception as e:
            msg = str(e)
            if "429" in msg and attempt < 3:
                wait = 2 ** attempt  # 1s, 2s, 4s
                logger.warning("Rate limited — waiting %ds before retry (attempt %d/3)", wait, attempt + 1)
                time.sleep(wait)
                continue
            logger.warning("Scoring failed for post by %s: %s", post.author_handle, e)
            return ScoredPost(post=post, score=0.0, angle="")
    return ScoredPost(post=post, score=0.0, angle="")


def _parse_response(raw: str) -> dict:
    """Parse the LLM response, stripping markdown code fences if present."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop first and last lines (``` fences)
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(inner).strip()
    return json.loads(text)


def score_posts(
    posts: list[SocialPost],
    profile: str,
    provider,
    threshold: float = 0.5,
    verbose: bool = False,
) -> list[ScoredPost]:
    """Score a list of posts and return those above the threshold, sorted by score.

    Args:
        posts: Posts to evaluate.
        profile: User profile for angle generation.
        provider: BaseProvider instance.
        threshold: Minimum score to include in results.
        verbose: Log score for every post, not just those above threshold.

    Returns:
        Filtered and sorted list of ScoredPost objects.
    """
    results: list[ScoredPost] = []
    with timed_run("social-post-reader", getattr(provider, "model", None)) as _run:
        for post in posts:
            scored = score_post(post, profile, provider)
            if verbose:
                logger.info(
                    "[%.2f] @%s — %s",
                    scored.score,
                    post.author_handle,
                    post.text[:60],
                )
            if scored.score >= threshold:
                results.append(scored)
        _run.item_count = len(posts)
        _run.input_tokens = getattr(provider, "input_tokens", None) or None
        _run.output_tokens = getattr(provider, "output_tokens", None) or None
    results.sort(key=lambda s: s.score, reverse=True)
    return results


def format_digest(scored_posts: list[ScoredPost], date_str: str, max_posts: int = 5) -> str:
    """Format scored posts as a Markdown reply-candidates digest block.

    Args:
        scored_posts: Scored and sorted posts.
        date_str: Date string for the digest header (e.g. "2026-03-17").
        max_posts: Maximum number of candidates to include.

    Returns:
        Markdown string ready to append to a daily note.
    """
    candidates = scored_posts[:max_posts]
    if not candidates:
        return f"## Reply Candidates — {date_str}\n\n_No candidates found today._\n"

    lines = [f"## Reply Candidates — {date_str}", ""]
    for i, sc in enumerate(candidates, 1):
        p = sc.post
        author = p.author_display_name or p.author_handle
        handle = f"@{p.author_handle}"
        excerpt = p.text[:120].rstrip()
        if len(p.text) > 120:
            excerpt += "…"

        lines.append(f"{i}. {handle} ({author}) — [{p.platform}]({p.post_url})")
        lines.append(f'   "{excerpt}"')
        if sc.angle:
            lines.append(f"   → Your angle: {sc.angle}")
        lines.append("")

    return "\n".join(lines)
