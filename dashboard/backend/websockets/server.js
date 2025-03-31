const express = require('express');
const http = require('http');
const WebSocket = require('ws');
const Redis = require('ioredis');

// Configuration
const HTTP_PORT = process.env.HTTP_PORT || 3000;
const REDIS_URL = process.env.REDIS_URL || 'redis://redis:6379';
const REDIS_CHANNEL = 'location_updates';

// --- Setup Express App ---
const app = express();
// Middleware to parse JSON bodies (increase limit if needed)
app.use(express.json({ limit: '1mb' }));

// --- Setup Redis Pub/Sub ---
// Publisher client (for sending updates received via HTTP)
const redisPublisher = new Redis(REDIS_URL, {
    // Optional: Add retry strategy, error handling
    maxRetriesPerRequest: 3
});
// Subscriber client (for receiving updates to broadcast via WebSocket)
const redisSubscriber = new Redis(REDIS_URL, {
    maxRetriesPerRequest: 3
});

redisPublisher.on('error', (err) => console.error('Redis Publisher Error:', err));
redisSubscriber.on('error', (err) => console.error('Redis Subscriber Error:', err));
redisPublisher.on('connect', () => console.log('Redis Publisher connected'));
redisSubscriber.on('connect', () => console.log('Redis Subscriber connected'));

// --- Setup HTTP Server ---
// We need the native http server to share with WebSocket server
const server = http.createServer(app);

// --- Setup WebSocket Server ---
const wss = new WebSocket.Server({ server }); // Attach WebSocket server to the HTTP server

// Store connected clients *for this instance*
const clients = new Set();

wss.on('connection', (ws, req) => {
    const clientIp = req.socket.remoteAddress; // Or use headers if behind proxy
    console.log(`Client connected: ${clientIp}. Total clients: ${clients.size + 1}`);
    clients.add(ws);

    // Handle client disconnection
    ws.on('close', (code, reason) => {
        console.log(`Client disconnected: ${clientIp}. Code: ${code}, Reason: ${reason ? reason.toString() : 'N/A'}. Total clients: ${clients.size - 1}`);
        clients.delete(ws);
    });

    // Handle errors on the connection
    ws.on('error', (error) => {
        console.error(`WebSocket error for client ${clientIp}:`, error);
        // Ensure cleanup even on error
        if (clients.has(ws)) {
            clients.delete(ws);
            console.log(`Client removed after error. Total clients: ${clients.size}`);
        }
        // ws.terminate(); // Force close if needed, 'close' event should follow
    });

    // Optional: Setup ping/pong for keep-alive and disconnect detection
    ws.isAlive = true;
    ws.on('pong', () => {
        ws.isAlive = true;
        // console.log(`Pong received from ${clientIp}`); // Debugging
    });

    // We don't expect messages from clients based on requirements
    // ws.on('message', (message) => {
    //   console.log('Received message (unexpected):', message);
    // });
});

// Optional: Interval to check for dead connections using ping/pong
const interval = setInterval(() => {
    // console.log(`Checking ${clients.size} clients for responsiveness...`); // Debugging
    clients.forEach((ws) => {
        if (ws.isAlive === false) {
            console.log(`Client unresponsive, terminating: ${ws._socket.remoteAddress}`);
            return ws.terminate(); // Force close unresponsive clients
        }
        ws.isAlive = false; // Assume dead until pong is received
        ws.ping((err) => {
            if (err) {
                console.error(`Ping failed for client ${ws._socket.remoteAddress}:`, err);
                // Error sending ping often means connection is already dead/broken
                // Let the termination logic above handle it on the next interval check
                // or rely on ws.on('error') / ws.on('close')
            } else {
                // console.log(`Ping sent to ${ws._socket.remoteAddress}`); // Debugging
            }
        });
    });
}, 30000); // Check every 30 seconds

wss.on('close', () => {
    clearInterval(interval); // Stop ping interval when server closes
});

// --- Redis Subscription Logic ---
redisSubscriber.subscribe(REDIS_CHANNEL, (err, count) => {
    if (err) {
        console.error('Failed to subscribe to Redis channel:', err);
        return;
    }
    console.log(`Subscribed successfully to ${count} channel(s). Listening for updates on '${REDIS_CHANNEL}'...`);
});

redisSubscriber.on('message', (channel, message) => {
    if (channel === REDIS_CHANNEL) {
        // console.log(`Received message from Redis: ${message}`); // Can be noisy
        // Broadcast the message to all clients connected *to this instance*
        let broadcastCount = 0;
        clients.forEach((client) => {
            // Check if client is ready to receive messages
            if (client.readyState === WebSocket.OPEN) {
                client.send(message, (err) => {
                    if (err) {
                        console.error('Failed to send message to client:', err);
                        // Optional: Consider removing client if send fails repeatedly
                        // clients.delete(client); client.terminate();
                    } else {
                        broadcastCount++;
                    }
                });
            }
        });
        if (broadcastCount > 0) {
            // console.log(`Broadcasted update to ${broadcastCount} clients.`); // Can be noisy
        }
    }
});


// --- HTTP Endpoints ---
// Endpoint to receive location updates
app.post('/update', async (req, res) => {
    const updateData = req.body;
    // Basic validation (optional)
    if (!updateData || Object.keys(updateData).length === 0) {
        return res.status(400).send('Bad Request: Empty update data');
    }

    try {
        // Publish the raw update data (as string) to Redis
        const message = JSON.stringify(updateData);
        await redisPublisher.publish(REDIS_CHANNEL, message);
        // console.log('Published update to Redis'); // Debugging
        res.status(200).send('Update received and published');
    } catch (err) {
        console.error('Failed to publish update to Redis:', err);
        res.status(500).send('Internal Server Error');
    }
});

// Health check endpoint
app.get('/health', (req, res) => {
    // More advanced checks could verify Redis connection, etc.
    res.status(200).send('OK');
});

// --- Start Server ---
server.listen(HTTP_PORT, () => {
    console.log(`HTTP and WebSocket server listening on port ${HTTP_PORT}`);
});

// --- Graceful Shutdown ---
const cleanup = async (signal) => {
    console.log(`\nReceived ${signal}. Shutting down gracefully...`);
    clearInterval(interval); // Stop ping timer

    // Close WebSocket server (stops accepting new connections)
    wss.close((err) => {
        if (err) {
            console.error("Error closing WebSocket server:", err);
        } else {
            console.log("WebSocket server closed.");
        }

        // Terminate existing client connections
        console.log(`Terminating ${clients.size} client connections...`);
        clients.forEach(client => client.terminate());
        clients.clear(); // Clear the set

        // Close HTTP server
        server.close(async () => {
            console.log('HTTP server closed.');

            // Close Redis connections
            try {
                await redisPublisher.quit();
                await redisSubscriber.quit();
                console.log('Redis connections closed.');
            } catch (redisErr) {
                console.error('Error closing Redis connections:', redisErr);
            } finally {
                // Exit process
                process.exit(0);
            }
        });
    });

    // Force shutdown if graceful takes too long
    setTimeout(() => {
        console.error('Graceful shutdown timed out. Forcing exit.');
        process.exit(1);
    }, 10000); // 10 seconds timeout
};

process.on('SIGTERM', () => cleanup('SIGTERM'));
process.on('SIGINT', () => cleanup('SIGINT')); // Catches Ctrl+C