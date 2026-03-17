"""Social Post Reader — daily digest of reply candidates from Bluesky and Mastodon.

Fetches text-heavy posts matching your configured keywords, scores them for
reply value using an LLM, generates a one-sentence "your angle" for each
candidate, and appends a digest to today's Obsidian daily note.

Usage:
    uv run social_post_reader.py run
    uv run social_post_reader.py run --sources bluesky,mastodon --dry-run
    uv run social_post_reader.py review
    uv run social_post_reader.py status
"""

import logging
import os
from datetime import date
from typing import Annotated

import typer
from local_first_common.obsidian import append_to_daily_note, get_daily_note_path
from local_first_common.providers import PROVIDERS

import config
import store as db_store
from fetcher import SocialPost, fetch_bluesky_posts, fetch_mastodon_posts, filter_posts
from scorer import format_digest, score_posts

app = typer.Typer(help="Daily digest of social posts worth replying to.")

_VALID_SOURCES = {"bluesky", "mastodon"}


def _get_provider(provider_name: str, model: str | None):
    """Instantiate and return the requested provider."""
    if provider_name not in PROVIDERS:
        typer.echo(f"Unknown provider: {provider_name!r}. Valid: {list(PROVIDERS.keys())}", err=True)
        raise typer.Exit(1)
    return PROVIDERS[provider_name](model=model)


def _parse_sources(sources_str: str) -> list[str]:
    parts = [s.strip().lower() for s in sources_str.split(",") if s.strip()]
    unknown = set(parts) - _VALID_SOURCES
    if unknown:
        typer.echo(f"Unknown sources: {unknown}. Valid: {_VALID_SOURCES}", err=True)
        raise typer.Exit(1)
    return parts


def _fetch_all_posts(sources: list[str]) -> list[SocialPost]:
    posts: list[SocialPost] = []

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


@app.command()
def run(
    sources: Annotated[
        str,
        typer.Option("--sources", "-s", help="Comma-separated: bluesky, mastodon"),
    ] = "bluesky,mastodon",
    provider: Annotated[
        str,
        typer.Option("--provider", "-p", help="LLM provider for scoring"),
    ] = config.DEFAULT_PROVIDER,
    model: Annotated[
        str | None,
        typer.Option("--model", "-m", help="Override provider's default model"),
    ] = None,
    threshold: Annotated[
        float,
        typer.Option("--threshold", "-t", help="Minimum score to include (0.0–1.0)"),
    ] = config.DEFAULT_THRESHOLD,
    max_candidates: Annotated[
        int,
        typer.Option("--max", help="Maximum candidates in the digest"),
    ] = config.DEFAULT_MAX_CANDIDATES,
    since_hours: Annotated[
        int,
        typer.Option("--since-hours", help="Ignore posts older than N hours (0 = no limit)"),
    ] = config.DEFAULT_SINCE_HOURS,
    score_limit: Annotated[
        int,
        typer.Option("--limit", "-l", help="Max posts to send to the LLM for scoring"),
    ] = config.DEFAULT_SCORE_LIMIT,
    store_path: Annotated[
        str,
        typer.Option("--store", help="SQLite DB path for tracking"),
    ] = config.STORE_PATH,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Print digest; write nothing"),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Show score for every post"),
    ] = False,
    no_store: Annotated[
        bool,
        typer.Option("--no-store", help="Skip writing to SQLite"),
    ] = False,
    no_obsidian: Annotated[
        bool,
        typer.Option("--no-obsidian", help="Skip appending to daily note"),
    ] = False,
) -> None:
    """Fetch posts, score them, and write a reply-candidates digest."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    source_list = _parse_sources(sources)
    llm = _get_provider(provider, model)

    today = date.today().isoformat()
    all_posts = _fetch_all_posts(source_list)

    if not all_posts:
        typer.echo("No posts fetched. Check your keywords and network.")
        raise typer.Exit(0)

    # Age + length pre-filter
    before = len(all_posts)
    all_posts = filter_posts(all_posts, since_hours=since_hours, min_words=config.DEFAULT_MIN_WORDS)
    if before != len(all_posts):
        typer.echo(f"  → {len(all_posts)} remain after age/length filter (dropped {before - len(all_posts)})")

    # Deduplicate against store if enabled
    if not no_store and not dry_run:
        db_store.init_db(store_path)
        unseen = [p for p in all_posts if not db_store.is_seen(p.post_url, store_path)]
        typer.echo(f"  → {len(unseen)} unseen (skipping {len(all_posts) - len(unseen)} already stored)")
        all_posts = unseen

    # Cap before scoring: sort by engagement (reply + like) so we send the most active posts
    if score_limit and len(all_posts) > score_limit:
        all_posts.sort(key=lambda p: p.reply_count + p.like_count, reverse=True)
        typer.echo(f"  → Capped at {score_limit} posts for scoring (sorted by engagement)")
        all_posts = all_posts[:score_limit]

    typer.echo(f"Scoring {len(all_posts)} posts...")

    scored = score_posts(all_posts, config.PROFILE, llm, threshold=threshold, verbose=verbose)
    typer.echo(f"Found {len(scored)} candidates above threshold {threshold}")

    digest = format_digest(scored, today, max_posts=max_candidates)

    if dry_run:
        typer.echo("\n" + digest)
        typer.echo("\nDone. Dry run — nothing written.")
        return

    # Write to SQLite
    if not no_store:
        for sc in scored:
            db_store.upsert_candidate(sc, today, store_path)
        typer.echo(f"Stored {min(len(scored), max_candidates)} candidates in {store_path}")

    # Append to daily note
    if not no_obsidian:
        try:
            vault_path = os.environ.get("OBSIDIAN_VAULT")
            note_path = get_daily_note_path(vault_root=vault_path)
            append_to_daily_note(digest, vault_root=vault_path)
            typer.echo(f"Appended digest to {note_path}")
        except Exception as e:
            typer.echo(f"Warning: could not write to daily note: {e}", err=True)
            typer.echo(digest)
    else:
        typer.echo("\n" + digest)

    candidates_written = min(len(scored), max_candidates)
    typer.echo(
        f"\nDone. Posts fetched: {len(all_posts)}, Scored: {len(scored)}, "
        f"Candidates written: {candidates_written}"
    )


@app.command()
def review(
    store_path: Annotated[
        str,
        typer.Option("--store", help="SQLite DB path"),
    ] = config.STORE_PATH,
    date_str: Annotated[
        str | None,
        typer.Option("--date", "-d", help="Review candidates for a specific date (YYYY-MM-DD)"),
    ] = None,
) -> None:
    """Interactively mark candidates as replied or skipped."""
    db_store.init_db(store_path)
    today = date_str or date.today().isoformat()
    candidates = db_store.get_new_candidates(today, store_path)

    if not candidates:
        typer.echo(f"No new candidates for {today}.")
        raise typer.Exit(0)

    typer.echo(f"\nReview candidates for {today} — (r)eplied / (s)kip / (q)uit\n")

    replied = skipped = 0
    for row in candidates:
        typer.echo(f"@{row['author_handle']} [{row['platform']}] — score {row['score']:.2f}")
        typer.echo(f"  {row['text'][:120]}")
        if row.get("angle"):
            typer.echo(f"  → {row['angle']}")
        typer.echo(f"  {row['post_url']}")

        choice = typer.prompt("  Action", default="s").strip().lower()
        if choice == "q":
            break
        elif choice == "r":
            db_store.mark_candidate(row["post_url"], "replied", store_path)
            replied += 1
        else:
            db_store.mark_candidate(row["post_url"], "skipped", store_path)
            skipped += 1
        typer.echo()

    typer.echo(f"Done. Replied: {replied}, Skipped: {skipped}")


@app.command()
def status(
    store_path: Annotated[
        str,
        typer.Option("--store", help="SQLite DB path"),
    ] = config.STORE_PATH,
) -> None:
    """Show a summary of stored candidates by status."""
    db_store.init_db(store_path)
    summary = db_store.get_status_summary(store_path)
    if not summary:
        typer.echo("No candidates in store yet.")
        return

    typer.echo("\nCandidate status summary:")
    for status_name, count in sorted(summary.items()):
        typer.echo(f"  {status_name:10} {count}")


if __name__ == "__main__":
    app()
