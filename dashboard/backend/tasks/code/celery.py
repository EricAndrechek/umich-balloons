import os
import logging
import sys
from celery import Celery
from celery.signals import setup_logging
from celery.schedules import crontab
from kombu import Queue

# --- Configuration ---
REDIS_QUEUE_DB = os.environ.get('REDIS_QUEUE_DB', '0') # Default Redis DB for queues
REDIS_CACHE_DB = os.environ.get('REDIS_CACHE_DB', '1') # Default Redis DB for cache
REDIS_BASE_URL = os.environ.get('REDIS_URL', 'redis://redis:6379')
CELERY_LOG_LEVEL = os.environ.get('ENV_LOG_LEVEL', 'INFO').upper()

# strip the trailing slash if present
if REDIS_BASE_URL.endswith('/'):
    REDIS_BASE_URL = REDIS_BASE_URL[:-1]
    
REDIS_URL = f"{REDIS_BASE_URL}/{REDIS_QUEUE_DB}" # Redis URL for queues

# Define your queues explicitly
CELERY_QUEUES = (
    Queue('default', routing_key='default'),
    Queue('queue_aprs', routing_key='queue_aprs'),
    Queue('queue_predictions', routing_key='queue_predictions'),
    Queue('queue_iridium', routing_key='queue_iridium'),
    Queue('queue_lora', routing_key='queue_lora'),
    Queue('queue_path_gen', routing_key='queue_path_gen'),
    Queue('queue_broadcast', routing_key='queue_broadcast'),
)

# --- Celery App Initialization ---
app = Celery(
    'code',
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        'code.jobs.aprs',
        'code.jobs.flight_prediction',
        'code.jobs.iridium',
        'code.jobs.lora',
        'code.jobs.path_generator',
        'code.jobs.broadcast',
    ]
)

# --- Celery Configuration ---
app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone=os.environ.get('TZ', 'UTC'),
    enable_utc=True,
    task_queues=CELERY_QUEUES,
    task_default_queue='default',
    task_default_exchange='default',
    task_default_routing_key='default',

    # --- Explicitly Configure Celery Logging ---
    # Ensure Celery doesn't try to redirect stdout/stderr itself
    worker_redirect_stdouts=False,
    # Set the overall worker log level
    worker_loglevel=CELERY_LOG_LEVEL,
     # Format for logs originating from within tasks (using logging module)
    worker_task_log_format="[%(asctime)s: %(levelname)s/%(processName)s][TASK:%(task_name)s(%(task_id)s)] %(message)s",
    # Format for logs originating from the worker itself (e.g., startup, connection)
    worker_log_format="[%(asctime)s: %(levelname)s/%(processName)s][WORKER] %(message)s",
    # Keep logging destination as default (stderr)
    worker_log_destination=None,
    worker_task_log_destination=None,
)

# --- Celery Beat Schedule (Cron Jobs) ---
app.conf.beat_schedule = {
    # --- Flight Prediction Schedule ---
    'schedule-flight-prediction': {
        'task': 'code.jobs.flight_prediction.run_scheduled_flight_prediction', # Task function for scheduled runs
        'schedule': crontab(hour='*/1', minute='0'), # Example: Run every hour at minute 0
        # 'args': (arg1, arg2), # Optional: Args specific to scheduled runs
        'options': {'queue': 'queue_predictions'} # Assign to its specific queue
    },
    # --- Path Generator Schedule ---
     'schedule-path-generator': {
        'task': 'code.jobs.path_generator.run_scheduled_path_generation',
        'schedule': crontab(minute='*/1'),
        'options': {'queue': 'queue_path_gen'}
    },
    # --- Add other scheduled tasks as before ---
    # 'example-periodic-job': { ... },
}

if __name__ == '__main__':
    app.start()