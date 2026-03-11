"""
InstaFlow — Package Entry Point

Run the API:        uvicorn instaflow.api.main:app --reload
Run a worker:       celery -A instaflow.workers.celery_app worker -l info
Run beat:           celery -A instaflow.workers.celery_app beat -l info
Run flower:         celery -A instaflow.workers.celery_app flower
"""
