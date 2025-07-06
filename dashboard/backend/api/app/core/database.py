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


# --- Helper Function for Binning ---
def date_trunc_30min(dt: datetime) -> datetime:
    """Truncates a datetime object to the start of its 30-minute interval in UTC."""
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) != timedelta(0):
        dt = dt.astimezone(timezone.utc)  # Ensure UTC
    minute_bin = (dt.minute // 30) * 30
    return dt.replace(minute=minute_bin, second=0, microsecond=0)


# --- Helper Function for Cache Key ---
def _generate_cache_key(
    prefix: str, geohashes: List[str], bin_starts_utc: List[datetime]
) -> str:
    """Generates a unique and relatively short cache key for binned path segments."""
    try:
        # Ensure consistent order for cache hits
        sorted_hashes = ":".join(sorted(geohashes))
        sorted_bins = ":".join(sorted([dt.isoformat() for dt in bin_starts_utc]))
        key_string = f"{prefix}:{sorted_hashes}:{sorted_bins}"
        # Use SHA256 for a robust hash
        return (
            f"binned_paths:{prefix}:{hashlib.sha256(key_string.encode()).hexdigest()}"
        )
    except Exception as e:
        log.error(f"Failed to generate cache key: {e}", exc_info=True)
        raise ValueError("Cache key generation failed") from e


# --- Internal DB Query Function (AsyncPG) ---
# This function now queries payload_paths_binned based on time bins AND geohash intersection
async def _query_binned_paths_from_db_async(
    client_geohashes: List[str],  # The geohashes from the client request
    bin_start_times_utc: List[datetime],  # Query specific bins
) -> List[Dict]:
    """
    Internal helper to query specific bins from payload_paths_binned table
    that intersect the client's geohashes. Uses asyncpg.
    """
    if not client_geohashes or not bin_start_times_utc:
        return []

    # Ensure times are timezone-aware (UTC assumed)
    if any(
        dt.tzinfo is None or dt.tzinfo.utcoffset(dt) != timedelta(0)
        for dt in bin_start_times_utc
    ):
        log.error("_query_binned_paths_from_db_async requires UTC bin_start_times_utc.")
        return []

    # SQL using $1, $2 placeholders for asyncpg
    # Filters by time bins AND geohash overlap using the pre-calculated array
    sql = """
    SELECT
        payload_id, -- Keep as UUID
        time_bin_start,
        path_segment_geojson,
        first_point_time,
        last_point_time,
        point_count,
        intersecting_geohashes -- Optionally return the stored hashes? Might be useful.
    FROM
        public.payload_paths_binned
    WHERE
        time_bin_start = ANY($1::timestamptz[]) -- Filter by required time bins
        AND intersecting_geohashes && $2::text[] -- Filter by geohash overlap << KEY CHANGE
    ORDER BY
        payload_id, time_bin_start; -- Consistent ordering
    """
    params = (
        bin_start_times_utc,  # Pass list for ANY()
        client_geohashes,  # Pass list for && overlap operator
    )
    log.info(
        f"Querying DB for {len(bin_start_times_utc)} specific bins intersecting {len(client_geohashes)} geohashes."
    )

    try:
        async with get_db_connection() as conn:
            rows: List[asyncpg.Record] = await conn.fetch(sql, *params)

        # Convert asyncpg.Record objects to dictionaries
        results = [
            {
                "payload_id": str(row["payload_id"]),  # Convert UUID
                "time_bin_start": row["time_bin_start"].isoformat(),
                "path_segment_geojson": row["path_segment_geojson"],
                "first_point_time": row["first_point_time"].isoformat(),
                "last_point_time": row["last_point_time"].isoformat(),
                "point_count": row["point_count"],
                # "intersecting_geohashes": row['intersecting_geohashes'] # Optional
            }
            for row in rows
        ]
        log.debug(
            f"DB query for binned paths intersecting geohashes returned {len(results)} segments."
        )
        return results
    except HTTPException:
        raise  # Let HTTP exceptions propagate
    except Exception as e:
        log.error(
            f"Failed to query intersecting binned paths from DB: {e}", exc_info=True
        )
        return []


# --- Main Caching Function (AsyncPG + Redis) ---
# This function orchestrates caching and calls the DB query function when needed.
# It works directly with the client's geohashes.
async def get_historical_paths_with_cache_async(
    client_geohashes: List[str],  # Renamed for clarity
    start_time_utc: datetime,
    end_time_utc: Optional[datetime] = None,
) -> List[Dict]:
    """
    Fetches historical path segments intersecting client_geohashes, utilizing Redis cache
    with separate TTLs for completed vs. current bins. Queries payload_paths_binned on cache miss.

    Args:
        client_geohashes: List of geohash strings from the client request.
        start_time_utc: Start of the time range (inclusive), timezone-aware (UTC).
        end_time_utc: End of the time range (exclusive), timezone-aware (UTC). Defaults to now().

    Returns:
        List of path segment dictionaries, combined from cache and DB.
    """
    # --- Start: Keep validation and Redis client check ---
    if not client_geohashes:
        return []
    if start_time_utc.tzinfo is None or start_time_utc.tzinfo.utcoffset(
        start_time_utc
    ) != timedelta(0):
        log.error("get_historical_paths_with_cache_async requires UTC start_time_utc.")
        return []
    if end_time_utc is None:
        end_time_utc = datetime.now(timezone.utc)
    elif end_time_utc.tzinfo is None or end_time_utc.tzinfo.utcoffset(
        end_time_utc
    ) != timedelta(0):
        log.error("get_historical_paths_with_cache_async requires UTC end_time_utc.")
        return []

    redis_cache = get_redis_cache_client()
    if not redis_cache:
        log.warning("Redis cache client not available, fallback query not implemented.")
        raise HTTPException(status_code=503, detail="Cache service unavailable.")
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
        log.debug("No relevant time bins for the requested range.")
        return []

    now_utc = datetime.now(timezone.utc)
    current_bin_start_utc = date_trunc_30min(now_utc)
    completed_bins_needed = sorted(
        [b for b in required_bins_starts if b < current_bin_start_utc]
    )
    current_bin_needed = (
        current_bin_start_utc if current_bin_start_utc in required_bins_starts else None
    )

    log.debug(f"Required Bins: {[b.isoformat() for b in required_bins_starts]}")
    log.debug(
        f"Completed Bins Needed: {[b.isoformat() for b in completed_bins_needed]}"
    )
    log.debug(
        f"Current Bin Needed: {current_bin_needed.isoformat() if current_bin_needed else 'None'}"
    )
    # --- End: Keep bin calculation and separation ---

    # 4. Cache Lookups (concurrently) - Uses Future for placeholders
    completed_results_from_cache: Optional[List[Dict]] = None
    current_result_from_cache: Optional[List[Dict]] = None
    cache_tasks = []
    completed_key = None
    current_key = None

    # Prepare lookup for completed bins
    if completed_bins_needed:
        try:
            # Key includes client geohashes and the bin timestamps
            completed_key = _generate_cache_key(
                "comp", client_geohashes, completed_bins_needed
            )
            cache_tasks.append(redis_cache.get(completed_key))
            log.debug(f"Checking cache for completed bins key: {completed_key}")
        except ValueError:
            f = asyncio.Future()
            f.set_result(None)
            cache_tasks.append(f)
    else:
        f = asyncio.Future()
        f.set_result(None)
        cache_tasks.append(f)

    # Prepare lookup for current bin
    if current_bin_needed:
        try:
            # Key includes client geohashes and the single bin timestamp
            current_key = _generate_cache_key(
                "curr", client_geohashes, [current_bin_needed]
            )
            cache_tasks.append(redis_cache.get(current_key))
            log.debug(f"Checking cache for current bin key: {current_key}")
        except ValueError:
            f = asyncio.Future()
            f.set_result(None)
            cache_tasks.append(f)
    else:
        f = asyncio.Future()
        f.set_result(None)
        cache_tasks.append(f)

    # --- Start: Keep cache result processing (No changes needed here) ---
    try:
        cache_results = await asyncio.gather(*cache_tasks, return_exceptions=True)
    except Exception as e:
        log.error(f"Error during Redis cache lookup gather: {e}", exc_info=True)
        cache_results = [e, e]  # Simulate errors

    # Process completed bins cache result
    if completed_bins_needed and completed_key:
        result = cache_results[0]
        if isinstance(result, Exception) or result is None:
            if not isinstance(result, Exception):
                log.debug(f"Cache miss (completed): {completed_key}")
            else:
                log.warning(
                    f"Redis GET error (completed): {result} for key {completed_key}"
                )
            completed_results_from_cache = None
        else:
            try:
                loaded_data = json.loads(result)
                if isinstance(loaded_data, list):
                    completed_results_from_cache = loaded_data
                    log.debug(
                        f"Cache hit (completed): {completed_key}, {len(loaded_data)} segments."
                    )
                else:
                    log.warning(
                        f"Invalid cache data type (completed): {completed_key}. Treating as miss."
                    )
                    completed_results_from_cache = None
            except json.JSONDecodeError as e:
                log.error(
                    f"Cache JSON decode error (completed): {completed_key} - {e}. Data: {str(result)[:100]}..."
                )
                completed_results_from_cache = None

    # Process current bin cache result
    if current_bin_needed and current_key:
        result = cache_results[1]
        if isinstance(result, Exception) or result is None:
            if not isinstance(result, Exception):
                log.debug(f"Cache miss (current): {current_key}")
            else:
                log.warning(
                    f"Redis GET error (current): {result} for key {current_key}"
                )
            current_result_from_cache = None
        else:
            try:
                loaded_data = json.loads(result)
                if isinstance(loaded_data, list):
                    current_result_from_cache = loaded_data
                    log.debug(
                        f"Cache hit (current): {current_key}, {len(loaded_data)} segments."
                    )
                else:
                    log.warning(
                        f"Invalid cache data type (current): {current_key}. Treating as miss."
                    )
                    current_result_from_cache = None
            except json.JSONDecodeError as e:
                log.error(
                    f"Cache JSON decode error (current): {current_key} - {e}. Data: {str(result)[:100]}..."
                )
                current_result_from_cache = None
    # --- End: Keep cache result processing ---

    # 5. Database Queries (Cache Miss) - Natively async with asyncpg
    final_results: List[Dict] = []
    db_tasks = []
    bins_to_fetch_completed = []
    bins_to_fetch_current = []

    # Prepare DB query for completed bins if missed in cache
    if completed_bins_needed and completed_results_from_cache is None:
        bins_to_fetch_completed = completed_bins_needed
        log.info(
            f"Queueing DB query for {len(bins_to_fetch_completed)} completed bins intersecting geohashes."
        )
        # Pass CLIENT geohashes to the query function
        db_tasks.append(
            _query_binned_paths_from_db_async(client_geohashes, bins_to_fetch_completed)
        )
    else:
        if completed_results_from_cache is not None:
            final_results.extend(completed_results_from_cache)
        f = asyncio.Future()
        f.set_result([])
        db_tasks.append(f)  # Placeholder

    # Prepare DB query for current bin if missed in cache
    if current_bin_needed and current_result_from_cache is None:
        bins_to_fetch_current = [current_bin_needed]
        log.info(f"Queueing DB query for current bin intersecting geohashes.")
        # Pass CLIENT geohashes to the query function
        db_tasks.append(
            _query_binned_paths_from_db_async(client_geohashes, bins_to_fetch_current)
        )
    else:
        if current_result_from_cache is not None:
            final_results.extend(current_result_from_cache)
        f = asyncio.Future()
        f.set_result([])
        db_tasks.append(f)  # Placeholder

    # Run DB queries concurrently if needed
    if bins_to_fetch_completed or bins_to_fetch_current:
        try:
            # This gather call now executes _query_binned_paths_from_db_async if needed
            db_query_results = await asyncio.gather(*db_tasks, return_exceptions=True)
        except Exception as e:
            log.error(f"Error running DB query tasks: {e}", exc_info=True)
            return final_results  # Return whatever we got from cache

        # --- Start: Keep DB result processing and cache writing (No changes needed here) ---
        cache_write_tasks = []
        # Process completed bins from DB
        completed_db_result = db_query_results[0]
        if bins_to_fetch_completed:
            if isinstance(completed_db_result, list):
                log.info(
                    f"DB query returned {len(completed_db_result)} completed segments."
                )
                final_results.extend(completed_db_result)
                if completed_key and redis_cache:  # Ensure key and client exist
                    try:
                        serialized_data = json.dumps(completed_db_result)
                        cache_write_tasks.append(
                            redis_cache.set(completed_key, serialized_data, ex=1800)
                        )  # 30 mins
                        log.debug(
                            f"Queueing cache SET (completed): {completed_key} (TTL 1800s)"
                        )
                    except Exception as e:
                        log.error(f"Serialization error (completed cache): {e}")
            elif isinstance(completed_db_result, Exception):
                log.error(f"DB query failed (completed): {completed_db_result}")

        # Process current bin from DB
        current_db_result = db_query_results[1]
        if bins_to_fetch_current:
            if isinstance(current_db_result, list):
                log.info(
                    f"DB query returned {len(current_db_result)} current segments."
                )
                final_results.extend(current_db_result)
                if current_key and redis_cache:  # Ensure key and client exist
                    try:
                        serialized_data = json.dumps(current_db_result)
                        cache_write_tasks.append(
                            redis_cache.set(current_key, serialized_data, ex=60)
                        )  # 1 min
                        log.debug(
                            f"Queueing cache SET (current): {current_key} (TTL 60s)"
                        )
                    except Exception as e:
                        log.error(f"Serialization error (current cache): {e}")
            elif isinstance(current_db_result, Exception):
                log.error(f"DB query failed (current): {current_db_result}")

        # Perform cache writes in background
        if cache_write_tasks:
            log.debug(
                f"Executing {len(cache_write_tasks)} background cache write tasks..."
            )

            async def run_cache_writes():
                results = await asyncio.gather(
                    *cache_write_tasks, return_exceptions=True
                )
                for i, res in enumerate(results):
                    if isinstance(res, Exception):
                        log.error(
                            f"Failed background Redis SET (task index {i}): {res}"
                        )

            asyncio.create_task(run_cache_writes())
        # --- End: Keep DB result processing and cache writing ---

    # 6. Combine Results (already done) and return
    final_results.sort(
        key=lambda x: (x.get("payload_id", ""), x.get("time_bin_start", ""))
    )
    log.info(f"Returning {len(final_results)} total path segments for request.")
    return final_results

async def get_name_and_symbol(
    payload_id: str) -> Optional[Dict[str, Union[str, int]]]:
    """
    Fetches the name and symbol for a given payload_id from the database.
    """
    if not payload_id:
        return None
    
    # try the cache first
    redis_cache = get_redis_cache_client()
    if redis_cache:
        cache_key = f"payload_name_symbol:{payload_id}"
        cached_data = await redis_cache.get(cache_key)
        if cached_data:
            try:
                return json.loads(cached_data)
            except json.JSONDecodeError as e:
                log.error(f"Failed to decode cached data: {e}", exc_info=True)
                # If decoding fails, proceed to fetch from DB
                pass

    sql = """
    SELECT
        name,
        symbol
    FROM
        public.payloads
    WHERE
        id = $1
    """
    params = (payload_id,)
    try:
        async with get_db_connection() as conn:
            row: asyncpg.Record = await conn.fetchrow(sql, *params)
        if row:
            # Cache the result for future requests
            if redis_cache:
                try:
                    cache_key = f"payload_name_symbol:{payload_id}"
                    await redis_cache.set(cache_key, json.dumps(dict(row)), ex=3600)  # Cache for 1 hour
                except Exception as e:
                    log.error(f"Failed to cache payload data: {e}", exc_info=True)
            # Return the name and symbol
            return {
                "name": row["name"],
                "symbol": row["symbol"],
            }
        else:
            log.warning(f"No data found for payload_id {payload_id}.")
            return None
    except HTTPException:
        raise  # Let HTTP exceptions propagate
    except Exception as e:
        log.error(f"Failed to fetch name and symbol: {e}", exc_info=True)
        return None

async def get_telemetry(payload_id):
    """Get the last 5 hours of telemetry for a given payload_id."""

    if not payload_id:
        return None
    
    # try the cache first
    redis_cache = get_redis_cache_client()
    if redis_cache:
        cache_key = f"payload_telemetry:{payload_id}"
        cached_data = await redis_cache.get(cache_key)
        if cached_data:
            try:
                return json.loads(cached_data)
            except json.JSONDecodeError as e:
                log.error(f"Failed to decode cached data: {e}", exc_info=True)
                # If decoding fails, proceed to fetch from DB
                pass

    sql = """
    SELECT
        *
    FROM
        public.payload_telemetry
    WHERE
        payload_id = $1
    AND
        timestamp >= NOW() - INTERVAL '5 hours'
    ORDER BY
        timestamp DESC
    """
    params = (payload_id,)
    try:
        async with get_db_connection() as conn:
            rows: List[asyncpg.Record] = await conn.fetch(sql, *params)
        if rows:
            # Cache the result for future requests
            if redis_cache:
                try:
                    cache_key = f"payload_telemetry:{payload_id}"
                    await redis_cache.set(cache_key, json.dumps([dict(row) for row in rows]), ex=150)  # Cache for 2.5 minutes
                    log.debug(f"Cached telemetry data for payload_id {payload_id}.")
                except Exception as e:
                    log.error(f"Failed to cache telemetry data: {e}", exc_info=True)
            # Return the telemetry data
            return [dict(row) for row in rows]
        else:
            log.warning(f"No telemetry data found for payload_id {payload_id}.")
            return None
    except HTTPException:
        raise  # Let HTTP exceptions propagate
    except Exception as e:
        log.error(f"Failed to fetch telemetry data: {e}", exc_info=True)
        return None