import os
import re
import paho.mqtt.client as mqtt
import time
import locale
from filelock import FileLock
from dotenv import load_dotenv
import json

try:
	locale.setlocale(locale.LC_ALL, "ro_RO.UTF-8")
except locale.Error:
	locale.setlocale(locale.LC_ALL, "en_US.UTF-8")

load_dotenv()

MQTT_BROKER = os.getenv('MQTT_BROKER')
MQTT_PORT = int(os.getenv('MQTT_PORT'))
MQTT_TOPIC_SUBSCRIBE = os.getenv("MQTT_TOPIC_SUBSCRIBE")
MQTT_TOPIC_PUBLISH = os.getenv("MQTT_TOPIC_PUBLISH")
NODE_ID = int(re.search(r"!([0-9a-fA-F]+)$", MQTT_TOPIC_PUBLISH).group(1), 16)
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID")
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")
QOS = 2

mqtt_client = mqtt.Client(client_id=MQTT_CLIENT_ID)

def on_message(client, userdata, msg):
	try:
		payload_str = msg.payload.decode("utf-8")
		print(f"Received message on topic {msg.topic} with payload: {repr(payload_str)}")

		payload_dict = json.loads(payload_str)

		if not isinstance(payload_dict, dict):
			print(f"Error: Parsed payload is not a dictionary: {payload_dict}")
			return

		message = payload_dict.get("payload", {})

		if isinstance(message, str):
			text = message
		elif isinstance(message, dict):
			text = message.get("text", "")
		else:
			print(f"Unexpected payload format: {message}")
			return

		print(f"Extracted message: {text}")

		lock = FileLock("msg_to_telegram.lock")
		with lock:
			with open("msg_to_telegram.txt", "w") as f:
				if text:
					f.write(text)
					print("Message written to Telegram file: " + text)

	except Exception as e:
		print(f"Unexpected error: {e}")

def on_connect(client, userdata, flags, rc):
	print(f"Connected to MQTT Broker with result code {rc}")
	print(f"Subscribing to topic {MQTT_TOPIC_SUBSCRIBE}.")
	client.subscribe(MQTT_TOPIC_SUBSCRIBE)

def check_for_message():
	while True:
		lock = FileLock('msg_to_mqtt.lock')
		with lock:
			with open('msg_to_mqtt.txt', 'r') as f:
				text = f.read()
				if text:
					# Uncomment the "channel" field and change the value if needed
					json_msg = {
						"from": NODE_ID,
						"payload": text,
						# "channel" : 0,
						"type": "sendtext"
					}

					message = json.dumps(json_msg).encode("utf-8")

					print(f"Publishing message: {message}")
					mqtt_client.publish(MQTT_TOPIC_PUBLISH, message, qos=QOS)

			with open('msg_to_mqtt.txt', 'w') as f_clear:
				f_clear.write('')

		time.sleep(2)

if __name__ == '__main__':
	print("Setting up MQTT client.")
	mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

	mqtt_client.on_connect = on_connect
	mqtt_client.on_message = on_message

	print(f"Connecting to MQTT broker at {MQTT_BROKER}:{MQTT_PORT}.")
	mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)

	mqtt_client.loop_start()

	print("Starting to check for messages to publish.")
	check_for_message()

# TODO 1: Have a single script that spawns the two processes and makes the IPC through pipes.
# TODO 2: Make handler for SIGINT.