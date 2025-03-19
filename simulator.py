import paho.mqtt.client as mqtt
import os
import json
import time
import random

MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
SYNC_TOPIC = "balloons/sync"
STATION_TOPIC = "stations/data"

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Simulated station connected to MQTT Broker!")
        client.subscribe(SYNC_TOPIC)
        publish_station_data(client)  # Publish initial data and then start loop
        client.loop_start()  # Start loop here to allow periodic publishing
    else:
        print("Failed to connect, return code %d\n", rc)

def on_message(client, userdata, msg):
    print(f"Received update on {msg.topic}: {msg.payload.decode()}")
    try:
        payload = json.loads(msg.payload.decode())
        print("Processed update:", payload)
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON: {e}")

def publish_station_data(client):
    """Simulates publishing data from a ground station."""
    station_id = "Station-" + str(random.randint(1, 100))  # Simulate multiple stations
    while True:
        station_data = {
            "station_id": station_id,
            "latitude": round(random.uniform(30, 50), 4),  # Simulate varying location
            "longitude": round(random.uniform(-120, -70), 4),
            "status": random.choice(["Active", "Idle"]),
            "timestamp": int(time.time())
        }
        try:
            client.publish(STATION_TOPIC, json.dumps(station_data))
            print(f"Published station data: {station_data}")
        except Exception as e:
            print(f"Error publishing station data: {e}")
        time.sleep(random.randint(5, 15))  # Publish data at random intervals

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message
client.connect(MQTT_BROKER, MQTT_PORT)

# The loop now starts in on_connect
# client.loop_forever() 

try:
    while True:
        time.sleep(1)  # Keep the main thread alive
except KeyboardInterrupt:
    print("Simulated station stopped.")
finally:
    client.disconnect()
