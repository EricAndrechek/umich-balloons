from dis import disco
from fastapi import WebSocket
from typing import Dict, Set, List
import json
import logging

log = logging.getLogger(__name__)

class ConnectionManager:
    def __init__(self):
        # Map grid_gh_str to a set of active WebSockets in that cell
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
        for gh_str in current_cells:
            if gh_str in self.room_connections:
                self.room_connections[gh_str].discard(websocket)
                # Optional: Clean up empty rooms
                if not self.room_connections[gh_str]:
                    del self.room_connections[gh_str]
        # Remove socket from subscriptions mapping
        if websocket in self.socket_subscriptions:
            del self.socket_subscriptions[websocket]

    async def add_to_room(self, websocket: WebSocket, gh_str: str):
        if gh_str not in self.room_connections:
            self.room_connections[gh_str] = set()
        self.room_connections[gh_str].add(websocket)
        if websocket in self.socket_subscriptions:
            self.socket_subscriptions[websocket].add(gh_str)
        # log.debug(f"WebSocket {websocket.client.host} added to room {gh_str}")

    async def remove_from_room(self, websocket: WebSocket, gh_str: str):
        if gh_str in self.room_connections:
            self.room_connections[gh_str].discard(websocket)
            if not self.room_connections[gh_str]:  # Clean up empty room
                del self.room_connections[gh_str]
        if websocket in self.socket_subscriptions:
            self.socket_subscriptions[websocket].discard(gh_str)
        # log.debug(f"WebSocket {websocket.client.host} removed from room {gh_str}")

    async def update_subscriptions(self, websocket: WebSocket, geohashes: List[str]):
        """Updates a websocket's room subscriptions based on the new set."""
        # convert to set
        new_geohashes = set(geohashes)
        current_geohashes = self.socket_subscriptions.get(websocket, set())

        geohashes_to_leave = current_geohashes - new_geohashes
        geohashes_to_join = new_geohashes - current_geohashes

        if geohashes_to_leave:
            for geohash in geohashes_to_leave:
                await self.remove_from_room(websocket, geohash)
        if geohashes_to_join:
            for geohash in geohashes_to_join:
                await self.add_to_room(websocket, geohash)

        # Update the primary mapping
        self.socket_subscriptions[websocket] = new_geohashes
        return (
            geohashes_to_join,
            geohashes_to_leave,
        )  # Return joined/left for potential actions

    async def send_personal_message(self, message: str, websocket: WebSocket):
        """Sends a message directly to a single websocket."""
        try:
            await websocket.send_text(message)
        except Exception as e:
            log.error(f"Error sending personal message: {e}. Disconnecting websocket.")
            # If sending fails, likely disconnected, clean up
            self.disconnect(websocket)

    async def _broadcast_to_room(self, room: str, message: str):
        """Broadcasts a message to all websockets in a room."""
        if room in self.room_connections:
            log.info(
                f"Broadcasting to room {room} ({len(self.room_connections[room])} sockets): {message[:100]}..."
            )

            disconnected_sockets = set()
            # Create a copy of the set to iterate over, as disconnect can modify it
            sockets_in_room = list(self.room_connections[room])
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
            log.warning(f"Attempted to broadcast to non-existent room {room}")

    async def broadcast_to_room(self, gh_str: str, message: str):
        """Broadcasts a message to all websockets in tiered hieracrchy of geohash string"""
        # first try broadcasting to the payload_id in the message, if it exists
        try:
            if "data" in message:
                data = json.loads(message)["data"]
                if "payload_id" in data:
                    payload_id = data["payload_id"]
                    if payload_id in self.room_connections:
                        await self._broadcast_to_room(payload_id, message)
                        return
        except json.JSONDecodeError:
            log.error(f"Error decoding JSON from message: {message}")

        for i in range(len(gh_str), 0, -1):
            # Check if the room exists at the current level
            room = gh_str[:i]
            if room in self.room_connections:
                await self._broadcast_to_room(room, message)

    async def add_to_raw(self, websocket: WebSocket):
        """Adds a websocket to the raw-messages channel."""
        if "raw-messages" not in self.room_connections:
            self.room_connections["raw-messages"] = set()
        self.room_connections["raw-messages"].add(websocket)
        self.socket_subscriptions[websocket].add("raw-messages")
        log.debug(
            f"WebSocket {websocket.client.host} added to raw-messages channel"
        )

    async def remove_from_raw(self, websocket: WebSocket):
        """Removes a websocket from the raw-messages channel."""
        if "raw-messages" in self.room_connections:
            self.room_connections["raw-messages"].discard(websocket)
            if not self.room_connections["raw-messages"]:
                del self.room_connections["raw-messages"]
        if websocket in self.socket_subscriptions:
            self.socket_subscriptions[websocket].discard("raw-messages")

    async def broadcast_raw_msg(self, message):
        """Broadcasts to any clients on the raw-messages channel."""
        if isinstance(message, dict):
            # Convert the message to JSON string
            message_str = json.dumps(message)
        elif isinstance(message, str):
            message_str = message
        else:
            log.error(f"Invalid message type: {type(message)}. Expected str or dict.")
            return
        log.info(f"Broadcasting raw message: {message_str[:100]}...")

        # Send to all connections in raw-messages channel
        disconnected_sockets = set()
        # Create a copy of the set to iterate over, as disconnect can modify it
        sockets_in_room = list(self.room_connections.get("raw-messages", []))
        for websocket in sockets_in_room:
            try:
                await websocket.send_text(message_str)
            except Exception as e:
                # Mark socket for disconnection if sending fails
                log.error(
                    f"Error broadcasting to {websocket.client.host}: {e}. Marking for disconnect."
                )
                disconnected_sockets.add(websocket)
        # Clean up disconnected sockets after broadcast iteration
        for websocket in disconnected_sockets:
            self.disconnect(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)


manager = ConnectionManager()
