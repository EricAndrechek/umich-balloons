from fastapi import WebSocket
from typing import Dict, Set, List
import json
import logging

log = logging.getLogger(__name__)

class ConnectionManager:
    def __init__(self):
        # Map grid_cell_id to a set of active WebSockets in that cell
        self.room_connections: Dict[str, Set[WebSocket]] = {}
        # Map WebSocket connection to the set of cells it's currently subscribed to
        self.socket_subscriptions: Dict[WebSocket, Set[str]] = {}

        # List of active WebSocket connections
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        self.socket_subscriptions[websocket] = set()
        log.debug(
            f"WebSocket connected: {websocket.client.host}:{websocket.client.port}"
        )

    def disconnect(self, websocket: WebSocket):
        log.debug(
            f"WebSocket disconnected: {websocket.client.host}:{websocket.client.port}"
        )
        self.active_connections.remove(websocket)
        # Remove socket from all rooms it was in
        current_cells = self.socket_subscriptions.get(websocket, set())
        for cell_id in current_cells:
            if cell_id in self.room_connections:
                self.room_connections[cell_id].discard(websocket)
                # Optional: Clean up empty rooms
                if not self.room_connections[cell_id]:
                    del self.room_connections[cell_id]
        # Remove socket from subscriptions mapping
        if websocket in self.socket_subscriptions:
            del self.socket_subscriptions[websocket]

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def add_to_room(self, websocket: WebSocket, cell_id: str):
        if cell_id not in self.room_connections:
            self.room_connections[cell_id] = set()
        self.room_connections[cell_id].add(websocket)
        if websocket in self.socket_subscriptions:
            self.socket_subscriptions[websocket].add(cell_id)
        # log.debug(f"WebSocket {websocket.client.host} added to room {cell_id}")

    async def remove_from_room(self, websocket: WebSocket, cell_id: str):
        if cell_id in self.room_connections:
            self.room_connections[cell_id].discard(websocket)
            if not self.room_connections[cell_id]:  # Clean up empty room
                del self.room_connections[cell_id]
        if websocket in self.socket_subscriptions:
            self.socket_subscriptions[websocket].discard(cell_id)
        # log.debug(f"WebSocket {websocket.client.host} removed from room {cell_id}")

    async def update_subscriptions(self, websocket: WebSocket, new_cells: Set[str]):
        """Updates a websocket's room subscriptions based on the new set."""
        current_cells = self.socket_subscriptions.get(websocket, set())
        cells_to_leave = current_cells - new_cells
        cells_to_join = new_cells - current_cells

        for cell_id in cells_to_leave:
            await self.remove_from_room(websocket, cell_id)
        for cell_id in cells_to_join:
            await self.add_to_room(websocket, cell_id)

        # Update the primary mapping
        self.socket_subscriptions[websocket] = new_cells
        return cells_to_join, cells_to_leave  # Return joined/left for potential actions

    async def send_personal_message(self, message: str, websocket: WebSocket):
        """Sends a message directly to a single websocket."""
        try:
            await websocket.send_text(message)
        except Exception as e:
            log.error(f"Error sending personal message: {e}. Disconnecting websocket.")
            # If sending fails, likely disconnected, clean up
            self.disconnect(websocket)

    async def broadcast_to_room(self, cell_id: str, message: str):
        """Broadcasts a message to all websockets in a specific room."""
        if cell_id in self.room_connections:
            disconnected_sockets = set()
            # Create a copy of the set to iterate over, as disconnect can modify it
            sockets_in_room = list(self.room_connections[cell_id])
            log.debug(
                f"Broadcasting to room {cell_id} ({len(sockets_in_room)} sockets): {message[:100]}..."
            )  # Log snippet
            for websocket in sockets_in_room:
                try:
                    await websocket.send_text(message)
                except Exception as e:
                    # Mark socket for disconnection if sending fails
                    log.error(
                        f"Error broadcasting to {websocket.client.host}: {e}. Marking for disconnect."
                    )
                    disconnected_sockets.add(websocket)

            # Clean up disconnected sockets after broadcast iteration
            for websocket in disconnected_sockets:
                self.disconnect(websocket)
        else:
            log.warning(f"Attempted to broadcast to non-existent room {cell_id}")

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)


manager = ConnectionManager()
