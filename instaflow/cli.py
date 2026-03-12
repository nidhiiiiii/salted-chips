"""
InstaFlow CLI — Command Line Interface

Submit reels for processing, check status, and manage the platform.

Usage::

    # Submit a single reel
    python -m instaflow.cli submit --url "https://instagram.com/reel/ABC123/"

    # Submit multiple reels from a file
    python -m instaflow.cli submit --file reels.txt

    # Check reel status
    python -m instaflow.cli status --id 1

    # List all reels
    python -m instaflow.cli list

    # Check account health
    python -m instaflow.cli health
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from typing import Optional

import click

from instaflow.config.logging import get_logger

logger = get_logger(__name__)


def _run_async(coro):
    """Helper to run async code in CLI commands."""
    return asyncio.run(coro)


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
@click.pass_context
def cli(ctx, verbose: bool):
    """InstaFlow Automation Engine CLI."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    if verbose:
        logger.setLevel("DEBUG")


@cli.command()
@click.option("--url", "-u", multiple=True, help="Instagram reel URL (can be repeated)")
@click.option("--file", "-f", "filename", type=click.File("r"), help="File containing reel URLs (one per line)")
@click.option("--account-id", "-a", default=1, help="Account ID to use (default: 1)")
@click.pass_context
def submit(ctx, url: tuple[str, ...], filename: Optional[click.File], account_id: int):
    """Submit reel URLs for processing."""
    from instaflow.storage.database import get_db_session
    from instaflow.storage.models import Reel

    # Collect URLs
    urls = list(url)
    if filename:
        file_urls = [line.strip() for line in filename if line.strip() and not line.startswith("#")]
        urls.extend(file_urls)

    if not urls:
        click.echo("❌ Error: No URLs provided. Use --url or --file.")
        sys.exit(1)

    click.echo(f"📥 Submitting {len(urls)} reel(s) for account {account_id}...")

    # Submit to database
    submitted = _run_async(_submit_reels(urls, account_id))

    if submitted:
        click.echo(f"✅ Successfully submitted {len(submitted)} reel(s):")
        for reel in submitted:
            click.echo(f"   • {reel['url']} (ID: {reel['id']})")
    else:
        click.echo("❌ Failed to submit reels.")
        sys.exit(1)


async def _submit_reels(urls: list[str], account_id: int) -> list[dict]:
    """Submit reels to the database and queue Celery tasks."""
    from instaflow.storage.database import get_db_session
    from instaflow.storage.models import Reel
    from instaflow.workers.task_reel import process_reel
    from sqlalchemy import select

    submitted = []

    async with get_db_session() as db:
        for url in urls:
            # Check if URL already exists
            result = await db.execute(select(Reel).where(Reel.url == url))
            existing = result.scalar_one_or_none()

            if existing:
                click.echo(f"⚠️  Skipping duplicate URL: {url[:60]}...")
                continue

            # Create new reel record
            reel = Reel(
                url=url,
                job_status="pending",
            )
            db.add(reel)
            await db.flush()

            # Queue Celery task
            process_reel.apply_async(
                kwargs={"reel_url": url, "account_id": account_id},
            )

            submitted.append({"id": reel.id, "url": url})

        await db.commit()

    return submitted


@cli.command()
@click.option("--id", "-i", "reel_id", type=int, help="Reel ID to check")
@click.option("--url", "-u", help="Reel URL to check")
@click.pass_context
def status(ctx, reel_id: Optional[int], url: Optional[str]):
    """Check status of a reel."""
    from instaflow.storage.database import get_db_session
    from instaflow.storage.models import Reel
    from sqlalchemy import select

    if not reel_id and not url:
        click.echo("❌ Error: Provide either --id or --url.")
        sys.exit(1)

    reel = _run_async(_get_reel(reel_id, url))

    if not reel:
        click.echo("❌ Reel not found.")
        sys.exit(1)

    click.echo(f"\n📍 Reel #{reel.id}")
    click.echo(f"   URL: {reel.url}")
    click.echo(f"   Status: {reel.job_status}")
    click.echo(f"   Follow: {reel.follow_status or 'N/A'}")
    click.echo(f"   Creator: {reel.creator_username or 'Pending'}")

    if reel.comment_text:
        click.echo(f"   Comment: {reel.comment_text}")

    if reel.submitted_at:
        click.echo(f"   Submitted: {reel.submitted_at.strftime('%Y-%m-%d %H:%M')}")

    if reel.comment_posted_at:
        click.echo(f"   Completed: {reel.comment_posted_at.strftime('%Y-%m-%d %H:%M')}")

    click.echo()


async def _get_reel(reel_id: Optional[int], url: Optional[str]) -> Optional[Reel]:
    """Fetch a reel from the database."""
    from instaflow.storage.database import get_db_session
    from instaflow.storage.models import Reel
    from sqlalchemy import select

    async with get_db_session() as db:
        if reel_id:
            result = await db.execute(select(Reel).where(Reel.id == reel_id))
        else:
            result = await db.execute(select(Reel).where(Reel.url == url))

        return result.scalar_one_or_none()


@cli.command("list")
@click.option("--limit", "-l", default=10, help="Number of reels to show")
@click.option("--status", "-s", type=click.Choice(["pending", "completed", "failed"]), help="Filter by status")
@click.pass_context
def list_reels(ctx, limit: int, status: Optional[str]):
    """List recent reels."""
    from instaflow.storage.database import get_db_session
    from instaflow.storage.models import Reel
    from sqlalchemy import select

    reels = _run_async(_list_reels(limit, status))

    if not reels:
        click.echo("No reels found.")
        return

    click.echo(f"\n📋 Recent Reels (showing {len(reels)})\n")
    click.echo(f"{'ID':<6} {'Status':<15} {'Follow':<18} {'Creator':<20} {'URL'}")
    click.echo("─" * 80)

    for reel in reels:
        creator = reel.creator_username or "Pending"
        url_short = reel.url[:40] + "..." if len(reel.url) > 40 else reel.url
        click.echo(f"{reel.id:<6} {reel.job_status:<15} {reel.follow_status or 'N/A':<18} {creator:<20} {url_short}")

    click.echo()


async def _list_reels(limit: int, status: Optional[str]) -> list[Reel]:
    """Fetch recent reels from the database."""
    from instaflow.storage.database import get_db_session
    from instaflow.storage.models import Reel
    from sqlalchemy import select

    async with get_db_session() as db:
        stmt = select(Reel).order_by(Reel.submitted_at.desc()).limit(limit)

        if status:
            stmt = select(Reel).where(Reel.job_status == status).order_by(Reel.submitted_at.desc()).limit(limit)

        result = await db.execute(stmt)
        return result.scalars().all()


@cli.command()
@click.option("--account-id", "-a", default=1, help="Account ID to check (default: 1)")
@click.pass_context
def health(ctx, account_id: int):
    """Check account health status."""
    from instaflow.storage.database import get_db_session
    from instaflow.storage.models import Account
    from sqlalchemy import select

    account = _run_async(_get_account_health(account_id))

    if not account:
        click.echo("❌ Account not found.")
        sys.exit(1)

    status_emoji = "✅" if account.status == "active" else "⚠️" if account.status == "quarantine" else "❌"

    click.echo(f"\n{status_emoji} Account: @{account.ig_username}")
    click.echo(f"   Health Score: {account.health_score}/100")
    click.echo(f"   Status: {account.status}")
    click.echo(f"   Country: {account.country}")
    click.echo(f"   Created: {account.created_at.strftime('%Y-%m-%d') if account.created_at else 'N/A'}")

    # Interpret health score
    if account.health_score >= 70:
        click.echo("   → Normal operation mode")
    elif account.health_score >= 40:
        click.echo("   → Conservative mode (rate limits halved)")
    else:
        click.echo("   → QUARANTINED (all activity paused)")

    click.echo()


async def _get_account_health(account_id: int) -> Optional[Account]:
    """Fetch account from the database."""
    from instaflow.storage.database import get_db_session
    from instaflow.storage.models import Account
    from sqlalchemy import select

    async with get_db_session() as db:
        result = await db.execute(select(Account).where(Account.id == account_id))
        return result.scalar_one_or_none()


@cli.command()
@click.pass_context
def stats(ctx):
    """Show platform statistics."""
    from instaflow.storage.database import get_db_session
    from instaflow.storage.models import Account, ExtractedLink, Follow, Reel
    from sqlalchemy import func, select

    stats = _run_async(_get_stats())

    click.echo(f"\n📊 InstaFlow Platform Statistics\n")
    click.echo(f"{'Reels Total':<20} {stats['reels_total']:<10}")
    click.echo(f"{'Reels Completed':<20} {stats['reels_completed']:<10}")
    click.echo(f"{'Reels Pending':<20} {stats['reels_pending']:<10}")
    click.echo(f"{'Reels Failed':<20} {stats['reels_failed']:<10}")
    click.echo(f"{'Follows Made':<20} {stats['follows_total']:<10}")
    click.echo(f"{'Links Extracted':<20} {stats['links_total']:<10}")
    click.echo(f"{'Active Accounts':<20} {stats['active_accounts']:<10}")
    click.echo(f"{'Avg Health Score':<20} {stats['avg_health']:<10}")
    click.echo()


async def _get_stats() -> dict:
    """Fetch platform statistics from the database."""
    from instaflow.storage.database import get_db_session
    from instaflow.storage.models import Account, ExtractedLink, Follow, Reel
    from sqlalchemy import func, select

    async with get_db_session() as db:
        # Reels
        reels_total = await db.execute(select(func.count(Reel.id)))
        reels_completed = await db.execute(
            select(func.count(Reel.id)).where(Reel.job_status == "completed")
        )
        reels_pending = await db.execute(
            select(func.count(Reel.id)).where(Reel.job_status == "pending")
        )
        reels_failed = await db.execute(
            select(func.count(Reel.id)).where(Reel.job_status == "failed")
        )

        # Follows
        follows_total = await db.execute(select(func.count(Follow.id)))

        # Links
        links_total = await db.execute(select(func.count(ExtractedLink.id)))

        # Accounts
        active_accounts = await db.execute(
            select(func.count(Account.id)).where(Account.status == "active")
        )
        avg_health = await db.execute(
            select(func.avg(Account.health_score)).where(Account.status == "active")
        )

        return {
            "reels_total": reels_total.scalar() or 0,
            "reels_completed": reels_completed.scalar() or 0,
            "reels_pending": reels_pending.scalar() or 0,
            "reels_failed": reels_failed.scalar() or 0,
            "follows_total": follows_total.scalar() or 0,
            "links_total": links_total.scalar() or 0,
            "active_accounts": active_accounts.scalar() or 0,
            "avg_health": round(avg_health.scalar() or 0, 1),
        }


def main():
    """Entry point for CLI."""
    cli(obj={})


if __name__ == "__main__":
    main()
