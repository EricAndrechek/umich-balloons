This MQTT broker service is supposed to be the lightweight and reliable connection method for our groundstations. It is preferred over websockets due to the low overhead and efficient message delivery, making it ideal for expensive cellular usage and maintaining realtime connections and data pushes.

However... Cloudflare Tunnels don't seem to support pure MQTT connections, and most VPN solutions would use more data then using basic websockets or HTTP endpoints would... We could use a different server to proxy the connection out as a DIY tunnel, or use another tunnelling service, but at that point we are adding another 3rd party dependency and might as well just host the MQTT broker on a VPS...

So, for now, to reduce complexity and avoid opening Prof. Ridley's home network to the world, we are dropping MQTT. :(
