import os
import logging
import sys
from celery import Celery
from celery.signals import setup_logging
from celery.schedules import crontab
from kombu import Queue

# --- Configuration ---
REDIS_URL = os.environ.get('REDIS_URL', 'redis://redis:6379/0')
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()

# Define your queues explicitly
CELERY_QUEUES = (
    Queue('default', routing_key='default'),
    Queue('queue_aprs', routing_key='queue_aprs'),
    Queue('queue_predictions', routing_key='queue_predictions'), # <--- Ensure defined
    Queue('queue_iridium', routing_key='queue_iridium'),
    Queue('queue_lora', routing_key='queue_lora'),
    Queue('queue_path_gen', routing_key='queue_path_gen'),       # <--- Ensure defined
)

# --- Logging Configuration Setup ---

@setup_logging.connect
def configure_logging(loglevel=None, logfile=None, format=None, colorize=None, **kwargs):
    """
    Configures Python's logging to send output to stdout/stderr.
    This function is connected to the `setup_logging` signal.
    """
    # Determine the effective log level (command line arg > env var > default)
    effective_loglevel = loglevel or LOG_LEVEL
    numeric_level = getattr(logging, effective_loglevel, logging.INFO) # Get numeric level

    # Define the log format
    log_format = "%(asctime)s - %(name)s:%(lineno)d - %(levelname)s - %(message)s"
    formatter = logging.Formatter(log_format)

    # Get the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level) # Set the root logger level

    # --- IMPORTANT: Redirect to Standard Output ---
    # Remove existing handlers to prevent potential duplicates on worker reload
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create a handler that writes to standard output (captured by Docker/Supervisor)
    stdout_handler = logging.StreamHandler(sys.stdout) # Use sys.stdout
    stdout_handler.setFormatter(formatter)
    stdout_handler.setLevel(numeric_level) # Set the handler level too

    # Add the handler to the root logger
    root_logger.addHandler(stdout_handler)

    # Optional: Adjust log levels for noisy libraries
    # logging.getLogger('kombu').setLevel(logging.WARNING)
    # logging.getLogger('amqp').setLevel(logging.WARNING)
    # logging.getLogger('redis').setLevel(logging.INFO) # Set redis logs lower if needed

    # Log that configuration is done (this message will use the new config)
    root_logger.info(f"Logging configured by signal handler at level {effective_loglevel} ({numeric_level}). Outputting to stdout.")

# --- Celery App Initialization ---
app = Celery(
    'code',
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        'code.jobs.aprs',
        'code.jobs.flight_prediction', # <--- Ensure included
        'code.jobs.iridium',
        'code.jobs.lora',
        'code.jobs.path_generator',    # <--- Ensure included
        # Include any module containing other scheduled tasks
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
    worker_redirect_stdouts=False,
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
        'task': 'code.jobs.path_generator.run_scheduled_path_generation', # Task function for scheduled runs
        'schedule': crontab(hour='3', minute='15'), # Example: Run daily at 03:15 UTC
        'kwargs': {'param1': 'value1'}, # Optional: Keyword args for scheduled runs
        'options': {'queue': 'queue_path_gen'} # Assign to its specific queue
    },
    # --- Add other scheduled tasks as before ---
    # 'example-periodic-job': { ... },
}

if __name__ == '__main__':
    app.start()