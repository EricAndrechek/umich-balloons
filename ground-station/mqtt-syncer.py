import paho.mqtt.client as mqtt
import os

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "user")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "password")
MQTT_CLIENT_ID = MQTT_USERNAME

# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, reason_code, properties):
    print(f"Connected with result code {reason_code}")
    # subscribe to my own client ID
    client.subscribe(f"{MQTT_CLIENT_ID}/sync/#", qos=1)

    # publish that this client is online
    client.publish(f"{MQTT_CLIENT_ID}/status", payload="online", qos=1, retain=True)

# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    print(msg.topic+" "+str(msg.payload))

mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, MQTT_CLIENT_ID)
mqttc.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
# set the last will and testament (LWT) message
mqttc.will_set(f"{MQTT_CLIENT_ID}/status", payload="offline", qos=1, retain=True)

mqttc.on_connect = on_connect
mqttc.on_message = on_message

mqttc.connect(MQTT_BROKER, MQTT_PORT, 60)

# pretend we have an APRS packet we want to upload
aprs_packet = b"KF8ABL-11>APRS,WIDE2-1:!4217.67N/08342.78WO010/005100 ft"
mqttc.publish(f"{MQTT_CLIENT_ID}/aprs", payload=aprs_packet, qos=1, retain=False)
# Publish a message to the topic "aprs"

# Blocking call that processes network traffic, dispatches callbacks and
# handles reconnecting.
# Other loop*() functions are available that give a threaded interface and a
# manual interface.
mqttc.loop_forever(retry_first_connection=True)
