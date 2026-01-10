import os
import re
import json
import time
import locale
import asyncio
import logging
from dotenv import load_dotenv

import paho.mqtt.client as mqtt
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)

try:
    locale.setlocale(locale.LC_ALL, "ro_RO.UTF-8")
except locale.Error:
    locale.setlocale(locale.LC_ALL, "en_US.UTF-8")

load_dotenv()

MQTT_BROKER = os.getenv('MQTT_BROKER')
MQTT_PORT = int(os.getenv('MQTT_PORT'))
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID")
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")
MQTT_TOPIC_SUB = os.getenv("MQTT_TOPIC_SUBSCRIBE_1")
MQTT_TOPIC_PUB = os.getenv("MQTT_TOPIC_PUBLISH_1")
QOS = 2

TOKEN = os.getenv("TOKEN")
CHAT_ID = int(os.getenv('CHAT_ID'))
TOPIC_ID = int(os.getenv('TOPIC_ID_1'))

KEYWORDS = os.getenv("KEYWORDS")
KEYWORD_PATTERN = re.compile(re.escape(KEYWORDS), re.IGNORECASE) if KEYWORDS else None

message_queue = asyncio.Queue()
mqtt_client = None
main_loop = None

def get_node_id(topic_pub):
    match = re.search(r"!([0-9a-fA-F]+)$", topic_pub)
    if match:
        return int(match.group(1), 16)
    return 0

def on_connect(client, userdata, flags, rc, properties):
    logger.info(f"MQTT Connected (RC: {rc})")
    logger.info(f"Subscribing to {MQTT_TOPIC_SUB}")
    client.subscribe(MQTT_TOPIC_SUB)

recent_messages = {}

def on_message(client, userdata, msg):
    try:
        if not mqtt.topic_matches_sub(MQTT_TOPIC_SUB, msg.topic):
            return

        logger.info(f"MQTT Topic: {msg.topic}")
        logger.info(f"Raw payload: {msg.payload}")

        payload_str = msg.payload.decode("utf-8")
        payload_dict = json.loads(payload_str)

        if not isinstance(payload_dict, dict):
            return

        # Ignore messages from our own node
        our_node_id = get_node_id(MQTT_TOPIC_PUB)
        sender_id = payload_dict.get("from", 0)
        logger.info(f"Message from {sender_id}, our node is {our_node_id}")
        if sender_id == our_node_id:
            logger.info(f"Ignored message from own node")
            return

        message_content = payload_dict.get("payload", {})
        text = ""
        if isinstance(message_content, str):
            text = message_content
        elif isinstance(message_content, dict):
            text = message_content.get("text", "")

        if not text:
            return

        # Ignore own bot messages
        if text.startswith("iBOT:"):
            logger.info(f"Ignored own message: {text}")
            return

        # Deduplication - ignore if same message in last 5 seconds
        now = time.time()
        msg_hash = hash(text)
        if msg_hash in recent_messages and (now - recent_messages[msg_hash]) < 5:
            logger.info(f"Ignored duplicate: {text}")
            return
        recent_messages[msg_hash] = now

        # Cleanup old entries
        for k in list(recent_messages.keys()):
            if now - recent_messages[k] > 60:
                del recent_messages[k]

        logger.info(f"MQTT RX: {text} -> Sending to Telegram")
        if main_loop:
            main_loop.call_soon_threadsafe(
                message_queue.put_nowait,
                text
            )

    except Exception as e:
        logger.error(f"MQTT Rx Error: {e}")

async def telegram_worker(app: Application):
    while True:
        text = await message_queue.get()

        try:
            await app.bot.send_message(
                chat_id=CHAT_ID,
                message_thread_id=TOPIC_ID,
                text=text
            )

            if KEYWORD_PATTERN and KEYWORD_PATTERN.search(text):
                logger.info("Keyword match! Sending receipt.")
                receipt = "iBOT: Receptionat!"
                publish_to_mqtt(receipt)
                await app.bot.send_message(
                    chat_id=CHAT_ID,
                    message_thread_id=TOPIC_ID,
                    text=receipt
                )

        except Exception as e:
            logger.error(f"Telegram Tx Error: {e}")

        message_queue.task_done()

async def handle_telegram_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    thread_id = update.message.message_thread_id
    text = update.message.text

    if thread_id == TOPIC_ID:
        logger.info(f"Telegram -> MQTT: {text}")
        publish_to_mqtt(text)

def publish_to_mqtt(text):
    try:
        node_id = get_node_id(MQTT_TOPIC_PUB)
        json_msg = {
            "from": node_id,
            "payload": text,
            "type": "sendtext"
        }
        message_bytes = json.dumps(json_msg, ensure_ascii=False).encode("utf-8")
        mqtt_client.publish(MQTT_TOPIC_PUB, message_bytes, qos=QOS)
    except Exception as e:
        logger.error(f"MQTT Publish Error: {e}")

async def main():
    global mqtt_client, main_loop

    main_loop = asyncio.get_running_loop()

    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT_CLIENT_ID)
    mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message

    logger.info(f"Connecting to MQTT Broker {MQTT_BROKER}...")
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
    except Exception as e:
        logger.critical(f"Could not connect to MQTT Broker: {e}")
        return

    mqtt_client.loop_start()

    logger.info("Initializing Telegram Bot...")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT, handle_telegram_message))

    await app.initialize()
    await app.start()

    asyncio.create_task(telegram_worker(app))

    logger.info("Bot is live. Press Ctrl+C to stop.")
    await app.updater.start_polling(poll_interval=2)

    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        logger.info("Stopping...")
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        mqtt_client.loop_stop()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
