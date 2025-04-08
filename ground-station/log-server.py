#!/usr/bin/env python3

import asyncio
import json
import logging
import os
import sys
from typing import Set

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

# --- Configuration ---
HOST = "0.0.0.0"  # Listen on all network interfaces on the Pi
PORT = 8000  # Port the webpage will be served on
# Systemd units to monitor (MAKE SURE THESE NAMES ARE CORRECT!)
UNITS_TO_MONITOR = [
    "aprs.service",
    "lora.service",
    "aprspy.service",
    "gpsd.service",
    # Add other units if needed, e.g., "log-viewer.service" to see its own logs
]
# Command to run journalctl
# -f: follow, --output=json: structured output, -u: specify units, -n 50: get last 50 lines on connect
JOURNALCTL_CMD = ["journalctl", "-f", "--output=json", "-n", "50"]
for unit in UNITS_TO_MONITOR:
    JOURNALCTL_CMD.extend(["-u", unit])

# Directory where this script and index.html reside
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML_PATH = os.path.join(CURRENT_DIR, "index.html")

# --- Logging Setup for this Server ---
log_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # Use DEBUG for more verbose output
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(log_formatter)
logger.addHandler(stream_handler)

# --- FastAPI App ---
app = FastAPI()

# --- WebSocket Management ---
active_connections: Set[WebSocket] = set()


async def broadcast(message: str):
    """Send message to all connected clients."""
    disconnected_clients = set()
    for connection in active_connections:
        try:
            await connection.send_text(message)
        except WebSocketDisconnect:
            disconnected_clients.add(connection)
            logger.info("Client disconnected during broadcast.")
        except Exception as e:
            disconnected_clients.add(connection)
            logger.error(f"Error sending to client: {e}. Disconnecting them.")
    # Remove disconnected clients outside the iteration loop
    for client in disconnected_clients:
        active_connections.discard(client)


async def log_streamer_task():
    """Task to run journalctl and stream logs."""
    logger.info(f"Starting log streamer task: {' '.join(JOURNALCTL_CMD)}")
    while True:  # Loop to automatically restart journalctl if it crashes
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *JOURNALCTL_CMD,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,  # Capture errors from journalctl itself
            )

            # Read stdout line by line
            while True:
                line_bytes = await process.stdout.readline()
                if not line_bytes:
                    logger.warning("journalctl stdout ended. Process likely exited.")
                    break  # Exit inner loop to restart process

                line_str = line_bytes.decode("utf-8", errors="ignore").strip()
                if line_str:
                    # We are already getting JSON, so just broadcast
                    await broadcast(line_str)

            # Read any errors after process exits
            stderr_bytes = await process.stderr.read()
            if stderr_bytes:
                logger.error(
                    f"journalctl stderr: {stderr_bytes.decode('utf-8', errors='ignore').strip()}"
                )

            await process.wait()  # Wait for process to finish completely
            logger.warning(
                f"journalctl process exited with code {process.returncode}. Restarting after delay..."
            )

        except FileNotFoundError:
            logger.error(f"FATAL: 'journalctl' command not found. Cannot stream logs.")
            await asyncio.sleep(60)  # Wait a long time if command is missing
        except Exception as e:
            logger.error(f"Error in log_streamer_task: {e}")
            if process and process.returncode is None:
                logger.info("Terminating existing journalctl process...")
                try:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("journalctl did not terminate gracefully, killing.")
                    process.kill()
                except Exception as term_err:
                    logger.error(f"Error terminating journalctl: {term_err}")
        finally:
            if process and process.returncode is None:
                # Ensure it's cleaned up if loop terminates unexpectedly
                try:
                    process.kill()
                except Exception:
                    pass

        await asyncio.sleep(5)  # Wait 5 seconds before restarting journalctl


@app.on_event("startup")
async def startup_event():
    """Start the background log streamer when the server starts."""
    asyncio.create_task(log_streamer_task())


@app.websocket("/ws_logs")
async def websocket_endpoint(websocket: WebSocket):
    """Handle incoming WebSocket connections."""
    await websocket.accept()
    active_connections.add(websocket)
    logger.info(
        f"Client connected: {websocket.client.host}:{websocket.client.port} (Total: {len(active_connections)})"
    )
    try:
        # Keep connection open until client disconnects
        while True:
            # You could receive commands here, e.g., filtering, pausing
            # For now, just keep the connection alive
            await websocket.receive_text()  # This will raise WebSocketDisconnect if client closes
    except WebSocketDisconnect:
        logger.info(
            f"Client disconnected: {websocket.client.host}:{websocket.client.port}"
        )
    except Exception as e:
        logger.error(f"WebSocket Error: {e}")
    finally:
        active_connections.discard(websocket)
        logger.info(f"Connection closed. (Total: {len(active_connections)})")


# Serve the main HTML page
@app.get("/")
async def read_index():
    if os.path.exists(INDEX_HTML_PATH):
        return FileResponse(INDEX_HTML_PATH)
    else:
        logger.error("index.html not found!")
        return "Error: index.html not found.", 404


# Main entry point for running with Uvicorn
if __name__ == "__main__":
    logger.info(f"Starting Uvicorn server on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
    # For systemd, you'll run: uvicorn log_server:app --host 0.0.0.0 --port 8000
