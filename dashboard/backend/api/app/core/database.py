import asyncpg
from contextlib import asynccontextmanager
from fastapi import HTTPException, status
from typing import Optional, Union, Any, List, Dict
import logging

import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta, timezone, time as dt_time
from dateutil.relativedelta import relativedelta
import json

from ..core.redis_client import get_redis_cache_client

from .config import settings

pool: asyncpg.Pool | None = None

log = logging.getLogger(__name__)

async def connect_db():
    """Creates the database connection pool."""
    global pool
    try:
        log.debug(f"Creating database connection pool with DSN: {settings.DATABASE_URL}")
        pool = await asyncpg.create_pool(
            dsn=settings.DATABASE_URL,
            min_size=settings.DB_POOL_MIN_SIZE,
            max_size=settings.DB_POOL_MAX_SIZE,
            # command_timeout=60, # Example: set command timeout
        )
        log.info("Database connection pool created successfully.")
    except Exception as e:
        log.error(f"Error creating database connection pool: {e}")
        # Optionally raise or exit if DB is critical at startup
        raise


async def close_db():
    """Closes the database connection pool."""
    global pool
    if pool:
        await pool.close()
        log.info("Database connection pool closed.")


@asynccontextmanager
async def get_db_connection():
    """Provides a connection from the pool."""
    if not pool:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection pool not available.",
        )
    conn = None
    try:
        # Acquire connection from pool
        conn = await pool.acquire()
        log.debug("Database connection acquired.")
        yield conn
    except Exception as e:
        log.error(f"Database connection error: {e}")
        # Handle specific DB errors if needed
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error occurred.",
        ) from e
    finally:
        if conn:
            # Release connection back to pool
            await pool.release(conn)


# --- Historical Path Functions ---

# --- Helper Function for Binning ---
def date_trunc_30min(dt: datetime) -> datetime:
    """Truncates a datetime object to the start of its 30-minute interval in UTC."""
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) != timedelta(0):
        dt = dt.astimezone(timezone.utc) # Ensure UTC

    minute_bin = (dt.minute // 30) * 30
    return dt.replace(minute=minute_bin, second=0, microsecond=0)

# --- Helper Function for Cache Key ---
def _generate_cache_key(prefix: str, geohashes: List[str], bin_starts_utc: List[datetime]) -> str:
    """Generates a unique and relatively short cache key."""
    try:
        # Ensure consistent order for cache hits
        sorted_hashes = ":".join(sorted(geohashes))
        sorted_bins = ":".join(sorted([dt.isoformat() for dt in bin_starts_utc]))
        key_string = f"{prefix}:{sorted_hashes}:{sorted_bins}"
        # Use SHA256 for a robust hash
        return f"binned_paths:{prefix}:{hashlib.sha256(key_string.encode()).hexdigest()}"
    except Exception as e:
        log.error(f"Failed to generate cache key: {e}", exc_info=True)
        raise ValueError("Cache key generation failed") from e


# --- Internal DB Query Function (AsyncPG) ---
async def _query_binned_paths_from_db_async(
    geohashes: List[str],
    bin_start_times_utc: List[datetime] # Query specific bins
) -> List[Dict]:
    """
    Internal helper to query specific bins directly from PostgreSQL using asyncpg.
    """
    if not geohashes or not bin_start_times_utc:
        return []

    # Ensure times are timezone-aware (UTC assumed based on binning logic)
    if any(dt.tzinfo is None or dt.tzinfo.utcoffset(dt) != timedelta(0) for dt in bin_start_times_utc):
        log.error("_query_binned_paths_from_db_async requires timezone-aware UTC bin_start_times_utc.")
        return []

    # SQL using $1, $2 placeholders for asyncpg
    sql = """
    SELECT
        payload_id, -- Keep as UUID, convert later if needed
        time_bin_start,
        path_segment_geojson,
        first_point_time,
        last_point_time,
        point_count
    FROM
        public.payload_paths_binned
    WHERE
        time_bin_start = ANY($1::timestamptz[]) -- Use explicit cast for array type
        AND intersecting_geohashes && $2::text[] -- Use explicit cast for array type
    ORDER BY
        payload_id, time_bin_start;
    """
    params = (
        bin_start_times_utc, # Pass list directly for ANY()
        geohashes          # Pass list directly for &&
    )
    log.info(f"Querying DB for {len(bin_start_times_utc)} specific bins and {len(geohashes)} geohashes.")
    try:
        # Use the provided async context manager for connection handling
        async with get_db_connection() as conn:
            rows: List[asyncpg.Record] = await conn.fetch(sql, *params) # Unpack params

        # Convert asyncpg.Record objects to dictionaries for easier handling/serialization
        results = [
            {
                "payload_id": str(row['payload_id']), # Convert UUID to string
                "time_bin_start": row['time_bin_start'].isoformat(), # Serialize datetime
                "path_segment_geojson": row['path_segment_geojson'], # Already dict/list from JSONB
                "first_point_time": row['first_point_time'].isoformat(),
                "last_point_time": row['last_point_time'].isoformat(),
                "point_count": row['point_count']
             } for row in rows
        ]
        log.debug(f"DB query for specific bins returned {len(results)} segments.")
        return results
    except HTTPException:
        # Let HTTP exceptions from get_db_connection propagate
        raise
    except Exception as e:
        log.error(f"Failed to query specific binned paths from DB: {e}", exc_info=True)
        return [] # Return empty list on failure


# app/helpers/db.py

# ... (keep imports: asyncio, hashlib, json, datetime, etc.) ...
# ... (keep logging, redis client getter, helper functions: date_trunc_30min, _generate_cache_key) ...
# ... (keep _query_binned_paths_from_db_async function) ...


# --- The main function to be called by the API ---
async def get_historical_paths_with_cache_async(
    geohashes: List[str],
    start_time_utc: datetime,
    end_time_utc: Optional[datetime] = None,
) -> List[Dict]:
    """
    Fetches historical path segments using asyncpg, utilizing Redis (DB 1) cache with
    separate TTLs for completed vs. current bins.

    Args:
        geohashes: List of geohash strings for viewport filtering.
        start_time_utc: Start of the time range (inclusive), timezone-aware (UTC).
        end_time_utc: End of the time range (exclusive), timezone-aware (UTC). Defaults to now().

    Returns:
        List of path segment dictionaries, combined from cache and DB.
    """
    # --- Start: Keep validation and Redis client check ---
    if not geohashes:
        log.debug("Empty geohashes list provided, returning empty result.")
        return []
    if start_time_utc.tzinfo is None or start_time_utc.tzinfo.utcoffset(
        start_time_utc
    ) != timedelta(0):
        log.error(
            "get_historical_paths_with_cache_async requires timezone-aware UTC start_time_utc."
        )
        return []
    if end_time_utc is None:
        end_time_utc = datetime.now(timezone.utc)
    elif end_time_utc.tzinfo is None or end_time_utc.tzinfo.utcoffset(
        end_time_utc
    ) != timedelta(0):
        log.error(
            "get_historical_paths_with_cache_async requires timezone-aware UTC end_time_utc."
        )
        return []

    redis_cache = get_redis_cache_client()
    if not redis_cache:
        log.warning(
            "Redis cache client not available, falling back to direct DB query (NOT IMPLEMENTED)."
        )
        raise HTTPException(
            status_code=503,
            detail="Cache service unavailable, cannot serve historical data.",
        )
    # --- End: Keep validation and Redis client check ---

    # --- Start: Keep bin calculation and separation ---
    required_bins_starts = []
    current_dt = start_time_utc
    while current_dt < end_time_utc:
        bin_start = date_trunc_30min(current_dt)
        if bin_start < end_time_utc and bin_start not in required_bins_starts:
            required_bins_starts.append(bin_start)
        current_dt = bin_start + timedelta(minutes=30)

    if not required_bins_starts:
        log.info("No relevant time bins for the requested range.")
        return []

    now_utc = datetime.now(timezone.utc)
    current_bin_start_utc = date_trunc_30min(now_utc)
    completed_bins_needed = sorted(
        [b for b in required_bins_starts if b < current_bin_start_utc]
    )
    current_bin_needed = (
        current_bin_start_utc if current_bin_start_utc in required_bins_starts else None
    )

    log.info(f"Required Bins: {[b.isoformat() for b in required_bins_starts]}")
    log.info(
        f"Completed Bins Needed: {[b.isoformat() for b in completed_bins_needed]}"
    )
    log.info(
        f"Current Bin Needed: {current_bin_needed.isoformat() if current_bin_needed else 'None'}"
    )
    # --- End: Keep bin calculation and separation ---

    # 4. Cache Lookups (concurrently) - Using Future for placeholders
    completed_results_from_cache: Optional[List[Dict]] = None
    current_result_from_cache: Optional[List[Dict]] = None
    cache_tasks = []
    completed_key = None
    current_key = None

    # Prepare lookup for completed bins
    if completed_bins_needed:
        try:
            completed_key = _generate_cache_key(
                "comp", geohashes, completed_bins_needed
            )
            cache_tasks.append(
                redis_cache.get(completed_key)
            )  # Add the actual awaitable task
            log.info(f"Checking cache for completed bins key: {completed_key}")
        except ValueError:
            # If key gen fails, add a resolved Future with None result
            f = asyncio.Future()
            f.set_result(None)
            cache_tasks.append(f)
    else:
        # If not needed, add a resolved Future with None result
        f = asyncio.Future()
        f.set_result(None)
        cache_tasks.append(f)

    # Prepare lookup for current bin
    if current_bin_needed:
        try:
            current_key = _generate_cache_key("curr", geohashes, [current_bin_needed])
            cache_tasks.append(
                redis_cache.get(current_key)
            )  # Add the actual awaitable task
            log.info(f"Checking cache for current bin key: {current_key}")
        except ValueError:
            f = asyncio.Future()
            f.set_result(None)
            cache_tasks.append(f)
    else:
        f = asyncio.Future()
        f.set_result(None)
        cache_tasks.append(f)

    # --- Start: Keep cache result processing ---
    # (The code processing cache_results[0] and cache_results[1] remains the same)
    try:
        cache_results = await asyncio.gather(*cache_tasks, return_exceptions=True)
    except Exception as e:
        log.error(f"Error during Redis cache lookup gather: {e}", exc_info=True)
        cache_results = [e, e]  # Simulate errors

    # Process completed bins cache result
    if completed_bins_needed and completed_key:  # Check key was generated
        result = cache_results[0]
        # ... (rest of processing logic for completed_results_from_cache remains the same) ...
        if isinstance(result, Exception) or result is None:
            completed_results_from_cache = None
            # Optional logging
        else:
            try:
                loaded_data = json.loads(result)
                if isinstance(loaded_data, list):
                    completed_results_from_cache = loaded_data
                else:
                    completed_results_from_cache = None  # Log warning
            except Exception:  # Catch JSON errors etc.
                completed_results_from_cache = None  # Log error

    # Process current bin cache result
    if current_bin_needed and current_key:  # Check key was generated
        result = cache_results[1]
        # ... (rest of processing logic for current_result_from_cache remains the same) ...
        if isinstance(result, Exception) or result is None:
            current_result_from_cache = None
            # Optional logging
        else:
            try:
                loaded_data = json.loads(result)
                if isinstance(loaded_data, list):
                    current_result_from_cache = loaded_data
                else:
                    current_result_from_cache = None  # Log warning
            except Exception:  # Catch JSON errors etc.
                current_result_from_cache = None  # Log error
    # --- End: Keep cache result processing ---

    # 5. Database Queries (Cache Miss) - Using Future for placeholders
    final_results: List[Dict] = []
    db_tasks = []
    bins_to_fetch_completed = []
    bins_to_fetch_current = []

    # Prepare DB query for completed bins if missed in cache
    if completed_bins_needed and completed_results_from_cache is None:
        bins_to_fetch_completed = completed_bins_needed
        log.info(
            f"Queueing DB query for {len(bins_to_fetch_completed)} completed bins."
        )
        db_tasks.append(
            _query_binned_paths_from_db_async(geohashes, bins_to_fetch_completed)
        )
    else:
        if completed_results_from_cache is not None:
            final_results.extend(completed_results_from_cache)
        # If not querying DB, add a resolved Future with an empty list result
        f = asyncio.Future()
        f.set_result([])
        db_tasks.append(f)

    # Prepare DB query for current bin if missed in cache
    if current_bin_needed and current_result_from_cache is None:
        bins_to_fetch_current = [current_bin_needed]
        log.info(f"Queueing DB query for 1 current bin.")
        db_tasks.append(
            _query_binned_paths_from_db_async(geohashes, bins_to_fetch_current)
        )
    else:
        if current_result_from_cache is not None:
            final_results.extend(current_result_from_cache)
        f = asyncio.Future()
        f.set_result([])
        db_tasks.append(f)

    # Run DB queries concurrently if needed
    if bins_to_fetch_completed or bins_to_fetch_current:
        try:
            db_query_results = await asyncio.gather(*db_tasks, return_exceptions=True)
        except Exception as e:
            log.error(f"Error running DB query tasks: {e}", exc_info=True)
            return final_results  # Return whatever we got from cache

        # --- Start: Keep DB result processing and cache writing ---
        cache_write_tasks = []
        # Process completed bins from DB
        completed_db_result = db_query_results[0]
        if bins_to_fetch_completed:  # Check if we actually queried DB for this
            # ... (logic to process completed_db_result, extend final_results, and queue cache write remains the same) ...
            if isinstance(completed_db_result, list):
                final_results.extend(completed_db_result)
                if completed_key:  # Ensure key exists
                    try:
                        serialized_data = json.dumps(completed_db_result)
                        cache_write_tasks.append(
                            redis_cache.set(completed_key, serialized_data, ex=1800)
                        )
                    except Exception as e:
                        log.error(f"Serialization error for completed cache: {e}")
            elif isinstance(completed_db_result, Exception):
                log.error(f"DB query failed (completed): {completed_db_result}")

        # Process current bin from DB
        current_db_result = db_query_results[1]
        if bins_to_fetch_current:  # Check if we actually queried DB for this
            # ... (logic to process current_db_result, extend final_results, and queue cache write remains the same) ...
            if isinstance(current_db_result, list):
                final_results.extend(current_db_result)
                if current_key:  # Ensure key exists
                    try:
                        serialized_data = json.dumps(current_db_result)
                        cache_write_tasks.append(
                            redis_cache.set(current_key, serialized_data, ex=60)
                        )
                    except Exception as e:
                        log.error(f"Serialization error for current cache: {e}")
            elif isinstance(current_db_result, Exception):
                log.error(f"DB query failed (current): {current_db_result}")

        # Perform cache writes in the background
        if cache_write_tasks:
            log.info(f"Executing {len(cache_write_tasks)} cache write tasks...")

            async def run_cache_writes():
                results = await asyncio.gather(
                    *cache_write_tasks, return_exceptions=True
                )
                # Optional: Log errors from gather results
                for i, res in enumerate(results):
                    if isinstance(res, Exception):
                        log.error(
                            f"Failed background Redis SET operation (task index {i}): {res}"
                        )

            asyncio.create_task(run_cache_writes())  # Run fire-and-forget
        # --- End: Keep DB result processing and cache writing ---

    # 6. Combine Results and return
    final_results.sort(
        key=lambda x: (x.get("payload_id", ""), x.get("time_bin_start", ""))
    )
    log.info(f"Returning {len(final_results)} total path segments for request.")
    return final_results
