"""
Celery Application Configuration — Module 4.1

Initializes the Celery app with Redis broker, result backend,
queue routing, and Beat schedule for periodic tasks.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from instaflow.config.settings import get_settings

settings = get_settings()

# ── Celery App ─────────────────────────────────────────────────────────
app = Celery("instaflow")

broker_url = settings.celery_broker_url or settings.redis_url
result_backend = settings.celery_result_backend or settings.redis_url

app.conf.update(
    broker_url=broker_url,
    result_backend=result_backend,

    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,

    # Worker settings
    worker_concurrency=settings.celery_concurrency,
    worker_prefetch_multiplier=1,  # One task at a time per worker process
    worker_max_tasks_per_child=50,  # Restart workers periodically to prevent leaks

    # Retry defaults
    task_acks_late=True,
    task_reject_on_worker_lost=True,

    # Result expiry
    result_expires=86400,  # 24h

    # Queue routing
    task_default_queue="default",
    task_routes={
        "instaflow.workers.task_reel.process_reel": {"queue": "default"},
        "instaflow.workers.task_dm.watch_dm": {"queue": "default"},
        "instaflow.workers.task_extract.extract_link": {"queue": "critical"},
        "instaflow.workers.task_maintenance.health_check": {"queue": "low"},
        "instaflow.workers.task_maintenance.export_excel": {"queue": "low"},
        "instaflow.workers.task_maintenance.recover_proxies": {"queue": "low"},
        "instaflow.workers.task_maintenance.check_follow_backs": {"queue": "low"},
    },

    # Dead letter queue
    task_queue_max_priority=10,
)

# ── Beat Schedule (periodic tasks) ────────────────────────────────────
app.conf.beat_schedule = {
    "health-check-every-30-min": {
        "task": "instaflow.workers.task_maintenance.health_check",
        "schedule": crontab(minute="*/30"),
        "kwargs": {"account_id": 1},  # Override in production
    },
    "export-excel-every-hour": {
        "task": "instaflow.workers.task_maintenance.export_excel",
        "schedule": crontab(minute=0),
    },
    "recover-proxies-every-45-min": {
        "task": "instaflow.workers.task_maintenance.recover_proxies",
        "schedule": crontab(minute="*/45"),
    },
    "check-follow-backs-every-6h": {
        "task": "instaflow.workers.task_maintenance.check_follow_backs",
        "schedule": crontab(hour="*/6", minute=0),
        "kwargs": {"account_id": 1},
    },
}

# ── Auto-discover tasks ───────────────────────────────────────────────
app.autodiscover_tasks(["instaflow.workers"])
