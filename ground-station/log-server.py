#!/usr/bin/env python3

import asyncio
import json
import logging
import os
import sys
import psutil  # For system stats
import subprocess  # For running commands (reboot, journalctl, iwconfig)
import re  # For parsing command output
from typing import Set, Dict, Any
from datetime import datetime
import socket  # For network status

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles  # To serve CSS/JS if separated

# --- Configuration ---
HOST = "0.0.0.0"
PORT = 8000
# Systemd units for live logs (MAKE SURE THESE NAMES ARE CORRECT!)
UNITS_TO_MONITOR = [
    "direwolf.service",
    "lora.service",
    "aprs.service",
    "log-viewer.service",  # Monitor self
]
JOURNALCTL_CMD = ["journalctl", "-f", "--output=json", "-n", "50"] + [
    f"-u{unit}" for unit in UNITS_TO_MONITOR
]

# Directory where this script and index.html reside
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML_PATH = os.path.join(CURRENT_DIR, "index.html")
# Optional: Serve static files from a sub-directory
# STATIC_DIR = os.path.join(CURRENT_DIR, "static")

# System Stats Update Interval (seconds)
STATS_INTERVAL = 3

# --- Logging Setup for this Server ---
log_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # Use DEBUG for more verbosity
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(log_formatter)
logger.addHandler(stream_handler)

# --- FastAPI App ---
app = FastAPI()

# --- WebSocket Connection Management ---
# Separate sets for different WebSocket purposes
log_connections: Set[WebSocket] = set()
stats_connections: Set[WebSocket] = set()

# --- Helper Functions ---


async def broadcast(connections: Set[WebSocket], message: str):
    """Send message to all clients in a given set."""
    disconnected_clients = set()
    # Iterate over a copy in case the set is modified during iteration
    for connection in list(connections):
        try:
            await connection.send_text(message)
        except WebSocketDisconnect:
            disconnected_clients.add(connection)
        except (
            Exception
        ):  # Catch other potential send errors (e.g., connection closed unexpectedly)
            disconnected_clients.add(connection)

    # Remove disconnected clients
    for client in disconnected_clients:
        connections.discard(client)
        # logger.debug(f"Removed disconnected client. Remaining: {len(connections)}")


def get_cpu_temp():
    """Get CPU temperature."""
    try:
        # Common path for Raspberry Pi CPU temp
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            temp_milli_c = int(f.read().strip())
            return round(temp_milli_c / 1000.0, 1)
    except FileNotFoundError:
        # Try using psutil as a fallback (might work on other systems)
        try:
            temps = psutil.sensors_temperatures()
            if "cpu_thermal" in temps:  # Linux standard name via psutil
                return round(temps["cpu_thermal"][0].current, 1)
            elif "coretemp" in temps:  # Common on Intel CPUs
                return round(temps["coretemp"][0].current, 1)
            # Add more specific checks if needed (e.g., k10temp for AMD)
        except Exception:
            pass  # Ignore psutil errors if sensors aren't available/readable
    except Exception as e:
        logger.error(f"Error reading temperature: {e}")
    return None


def get_network_status():
    """Get status of Ethernet and WiFi interfaces."""
    status = {"ethernet": None, "wifi": None}
    interfaces = psutil.net_if_addrs()
    stats = psutil.net_if_stats()

    for name, addrs in interfaces.items():
        if name not in stats or not stats[name].isup:
            continue  # Skip down interfaces

        ip_addr = None
        for addr in addrs:
            if addr.family == socket.AF_INET:  # Found IPv4
                ip_addr = addr.address
                break

        if name.startswith("eth") or name.startswith("enp"):  # Ethernet?
            if ip_addr:  # Consider it active if it has an IP and is up
                status["ethernet"] = {
                    "interface": name,
                    "ip": ip_addr,
                    "type": "ethernet",
                }

        elif name.startswith("wlan") or name.startswith("wlp"):  # WiFi?
            if ip_addr:
                status["wifi"] = {
                    "interface": name,
                    "ip": ip_addr,
                    "type": "wifi",
                    "signal": None,
                    "quality": None,
                }
                # Try to get signal strength using iwconfig
                try:
                    result = subprocess.run(
                        ["iwconfig", name], capture_output=True, text=True, check=False
                    )
                    # Look for "Link Quality=70/70" or similar
                    quality_match = re.search(
                        r"Link Quality=(\d+)/(\d+)", result.stdout
                    )
                    if quality_match:
                        quality = int(quality_match.group(1))
                        quality_max = int(quality_match.group(2))
                        status["wifi"]["quality"] = f"{quality}/{quality_max}"
                        # Convert quality to percentage/bars (simple linear mapping)
                        signal_percent = (
                            round((quality / quality_max) * 100)
                            if quality_max > 0
                            else 0
                        )
                        status["wifi"]["signal"] = signal_percent

                    # Look for "Signal level=-40 dBm" or similar (can also be used)
                    signal_match = re.search(
                        r"Signal level=(-?\d+)\s+dBm", result.stdout
                    )
                    if signal_match:
                        # You could use dBm directly or convert it if needed
                        pass  # We prioritize Link Quality for percentage

                except FileNotFoundError:
                    logger.warning(
                        "`iwconfig` not found. Cannot get WiFi signal strength."
                    )
                except Exception as e:
                    logger.error(f"Error running iwconfig for {name}: {e}")

    return status

def get_service_status(service_name: str) -> str:
    """Checks systemd service status using systemctl commands."""
    try:
        # Check if failed
        proc_failed = subprocess.run(['systemctl', 'is-failed', service_name], capture_output=True, text=True, check=False)
        status_failed = proc_failed.stdout.strip()

        if status_failed == "failed":
            return "failed"

        # Check if active
        proc_active = subprocess.run(['systemctl', 'is-active', service_name], capture_output=True, text=True, check=False)
        status_active = proc_active.stdout.strip()

        # is-active returns "inactive" or "active", or "unknown" if unit doesn't exist
        # is-failed returns "failed", or the active state if not failed

        # Prioritize 'failed', then 'active', then 'inactive'
        if status_active == "active":
            return "active"
        elif status_active == "inactive":
             # Could be activating, check is-failed again for clarity
             if status_failed == "activating":
                 return "activating"
             return "inactive"
        elif status_active == "unknown":
             return "not-found"
        else: # Could be activating, reloading, deactivating
            return status_active # Return the specific state

    except FileNotFoundError:
        logger.error("systemctl command not found.")
        return "error" # Indicate systemctl is missing
    except Exception as e:
        logger.error(f"Error checking status for {service_name}: {e}")
        return "error" # Indicate a check failure


# --- Background Tasks ---


async def log_streamer_task():
    """Task to run journalctl and stream logs to /ws_logs."""
    # ... (This function remains the same as before) ...
    logger.info(f"Starting log streamer task: {' '.join(JOURNALCTL_CMD)}")
    while True:
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *JOURNALCTL_CMD,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            while True:
                line_bytes = await process.stdout.readline()
                if not line_bytes:
                    break
                line_str = line_bytes.decode("utf-8", errors="ignore").strip()
                if line_str:
                    await broadcast(log_connections, line_str)  # Send to log clients

            stderr_bytes = await process.stderr.read()
            if stderr_bytes:
                logger.error(
                    f"journalctl stderr: {stderr_bytes.decode('utf-8', errors='ignore').strip()}"
                )
            await process.wait()
            logger.warning(
                f"journalctl process exited ({process.returncode}). Restarting..."
            )
        except Exception as e:
            logger.error(f"Error in log_streamer_task: {e}")
            if process and process.returncode is None:
                process.terminate()
        finally:
            if process and process.returncode is None:
                try:
                    process.kill()
                except Exception:
                    pass
        await asyncio.sleep(5)


async def system_stats_emitter():
    """Task to periodically gather stats AND service status, push to /ws_system."""
    logger.info("Starting system stats emitter task.")
    # Add the list of services this backend monitors here
    # It should ideally match UNITS_TO_MONITOR for consistency
    services_to_check = [
        "direwolf.service",
        "lora.service",
        "aprs.service",
        "log-viewer.service",
    ]

    while True:
        try:
            # --- Gather System Stats (same as before) ---
            cpu_usage = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory()
            ram_used_gb = round(ram.used / (1024**3), 1)
            ram_total_gb = round(ram.total / (1024**3), 1)
            ram_percent = ram.percent
            cpu_temp = get_cpu_temp()
            network = get_network_status()

            # --- Gather Service Statuses ---
            service_statuses = {}
            for service_name in services_to_check:
                # Run sync function in thread pool to avoid blocking async loop
                loop = asyncio.get_running_loop()
                status = await loop.run_in_executor(
                    None, get_service_status, service_name
                )
                service_statuses[service_name] = status

            # --- Prepare Payload ---
            payload = {
                "type": "stats_and_status",  # New type indicator
                "timestamp": datetime.now().isoformat(),
                # System Stats
                "cpu_percent": cpu_usage,
                "ram_percent": ram_percent,
                "ram_used_gb": ram_used_gb,
                "ram_total_gb": ram_total_gb,
                "cpu_temp_c": cpu_temp,
                "network": network,
                # Service Statuses
                "services": service_statuses,
            }

            await broadcast(
                stats_connections, json.dumps(payload)
            )  # Send to stats clients

        except Exception as e:
            logger.error(f"Error in system_stats_emitter: {e}")

        await asyncio.sleep(STATS_INTERVAL)  # Wait before next update


# --- FastAPI Event Handlers ---
@app.on_event("startup")
async def startup_event():
    """Start background tasks."""
    logger.info("Server starting up...")
    asyncio.create_task(log_streamer_task())
    asyncio.create_task(system_stats_emitter())


# --- Web Socket Endpoints ---


@app.websocket("/ws_logs")
async def websocket_logs_endpoint(websocket: WebSocket):
    """Handle WebSocket connections for live logs."""
    await websocket.accept()
    log_connections.add(websocket)
    logger.info(
        f"Log client connected: {websocket.client.host} (Total: {len(log_connections)})"
    )
    try:
        while True:
            await websocket.receive_text()  # Keep alive until disconnect
    except WebSocketDisconnect:
        logger.info(f"Log client disconnected: {websocket.client.host}")
    finally:
        log_connections.discard(websocket)


@app.websocket("/ws_system")
async def websocket_system_endpoint(websocket: WebSocket):
    """Handle WebSocket connections for system stats."""
    await websocket.accept()
    stats_connections.add(websocket)
    logger.info(
        f"Stats client connected: {websocket.client.host} (Total: {len(stats_connections)})"
    )
    try:
        # Optionally send current stats immediately on connect?
        # await websocket.send_text(json.dumps(get_current_stats_snapshot()))
        while True:
            await websocket.receive_text()  # Keep alive
    except WebSocketDisconnect:
        logger.info(f"Stats client disconnected: {websocket.client.host}")
    finally:
        stats_connections.discard(websocket)


# --- HTTP API Endpoints ---


@app.post("/api/services/restart/{service_name}")
async def restart_service(service_name: str):
    """Attempts to restart a specific systemd service using sudo."""
    # Basic validation to prevent arbitrary command execution
    allowed_services = [
        "direwolf.service",
        "lora.service",
        "aprs.service",
        "log-viewer.service",
        # Add any other services you explicitly allowed in sudoers
    ]
    if service_name not in allowed_services:
        logger.warning(f"Attempt to restart non-allowed service: {service_name}")
        raise HTTPException(
            status_code=403,
            detail=f"Restarting service '{service_name}' is not permitted.",
        )

    cmd = ["sudo", "/bin/systemctl", "restart", service_name]
    logger.info(f"Received request to execute restart command: {' '.join(cmd)}")

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        std_out = stdout.decode("utf-8", errors="ignore").strip()
        std_err = stderr.decode("utf-8", errors="ignore").strip()

        if process.returncode == 0:
            logger.info(f"Successfully issued restart for {service_name}.")
            return {"message": f"Restart command issued for {service_name}."}
        else:
            logger.error(
                f"Failed to restart {service_name}. Code: {process.returncode}, Err: {std_err}, Out: {std_out}"
            )
            raise HTTPException(
                status_code=500,
                detail=f"Failed to restart {service_name}: {std_err or 'Unknown error'}",
            )

    except FileNotFoundError:
        logger.error("'systemctl' or 'sudo' command not found.")
        raise HTTPException(
            status_code=500, detail="'systemctl' or 'sudo' command not found."
        )
    except Exception as e:
        logger.error(f"Exception during service restart for {service_name}: {e}")
        raise HTTPException(
            status_code=500, detail=f"An unexpected error occurred: {e}"
        )


@app.get("/api/crashlogs")
async def get_crash_logs(lines: int = 200, boot: int = -1):
    """Get logs from a previous boot (-1 = last boot, -2 = one before, etc.)."""
    # Basic validation
    if not isinstance(boot, int) or boot > 0:
        raise HTTPException(
            status_code=400, detail="Boot parameter must be a non-positive integer."
        )
    if not isinstance(lines, int) or lines <= 0:
        raise HTTPException(
            status_code=400, detail="Lines parameter must be a positive integer."
        )

    cmd = ["journalctl", f"--boot={boot}", "--no-pager", "--lines", str(lines)]
    logger.info(f"Running command for crash logs: {' '.join(cmd)}")
    try:
        # Use asyncio's subprocess handling for non-blocking execution
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            err_output = stderr.decode("utf-8", errors="ignore").strip()
            logger.error(f"journalctl failed for boot {boot}: {err_output}")
            # Try to provide a meaningful error message
            if (
                "does not look like a journal" in err_output
                or "file not found" in err_output
            ):
                raise HTTPException(
                    status_code=404,
                    detail=f"No journal data found for boot offset {boot}.",
                )
            else:
                raise HTTPException(
                    status_code=500,
                    detail=f"Error fetching logs for boot {boot}: {err_output or 'Unknown error'}",
                )

        log_output = stdout.decode("utf-8", errors="ignore")
        return PlainTextResponse(log_output)

    except FileNotFoundError:
        logger.error("'journalctl' command not found.")
        raise HTTPException(
            status_code=500, detail="'journalctl' command not found on server."
        )
    except Exception as e:
        logger.error(f"Error fetching crash logs: {e}")
        raise HTTPException(
            status_code=500, detail=f"An unexpected error occurred: {e}"
        )


@app.post("/api/reboot")
async def trigger_reboot():
    """Trigger a system reboot using sudo."""
    cmd = ["sudo", "/sbin/reboot"]
    logger.warning(f"Received request to execute reboot command: {' '.join(cmd)}")
    try:
        # Run the command - use subprocess.run for simplicity here as we don't wait
        # Use start_new_session=True to detach it from the server process
        subprocess.Popen(
            cmd,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("Reboot command issued.")
        # Return immediately, the system will go down shortly
        return {"message": "Reboot command issued successfully."}
    except FileNotFoundError:
        logger.error("'/sbin/reboot' or 'sudo' command not found.")
        raise HTTPException(
            status_code=500, detail="'reboot' or 'sudo' command not found."
        )
    except Exception as e:
        logger.error(f"Failed to issue reboot command: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to issue reboot command: {e}"
        )


# --- Static Files and Main Page ---

# Optional: Mount a static directory if you separate CSS/JS
# app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def read_index():
    """Serve the main HTML dashboard page."""
    if os.path.exists(INDEX_HTML_PATH):
        return FileResponse(INDEX_HTML_PATH)
    else:
        logger.error("index.html not found!")
        return PlainTextResponse("Error: index.html not found.", status_code=404)


# --- Main Execution ---
if __name__ == "__main__":
    # Fix for socket import issue within get_network_status if run directly
    import socket

    # Run using Uvicorn programmatically
    logger.info(f"Starting Uvicorn server on {HOST}:{PORT}")
    uvicorn.run(__name__ + ":app", host=HOST, port=PORT, log_level="info", reload=False)
    # Note: For systemd, use: uvicorn log_server:app --host 0.0.0.0 --port 8000 --workers 1
