import os
import re
import json
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
QOS = 2

TOKEN = os.getenv("TOKEN")
CHAT_ID = int(os.getenv('CHAT_ID'))

KEYWORDS = os.getenv("KEYWORDS")
KEYWORD_PATTERN = re.compile(re.escape(KEYWORDS), re.IGNORECASE) if KEYWORDS else None

CHANNELS = [
    {
        "id": 1,
        "sub": os.getenv("MQTT_TOPIC_SUBSCRIBE_1"),
        "pub": os.getenv("MQTT_TOPIC_PUBLISH_1"),
        "topic_id": int(os.getenv('TOPIC_ID_1')),
    },
    {
        "id": 2,
        "sub": os.getenv("MQTT_TOPIC_SUBSCRIBE_2"),
        "pub": os.getenv("MQTT_TOPIC_PUBLISH_2"),
        "topic_id": int(os.getenv('TOPIC_ID_2')),
    }
]

message_queue = asyncio.Queue()
mqtt_client = None
main_loop = None

def get_channel_by_sub(topic):
    for ch in CHANNELS:
        if ch["sub"] == topic:
            return ch
    return None

def get_channel_by_topic_id(tid):
    for ch in CHANNELS:
        if ch["topic_id"] == tid:
            return ch
    return None

def get_node_id(topic_pub):
    match = re.search(r"!([0-9a-fA-F]+)$", topic_pub)
    if match:
        return int(match.group(1), 16)
    return 0

def on_connect(client, userdata, flags, rc):
    logger.info(f"MQTT Connected (RC: {rc})")
    for ch in CHANNELS:
        logger.info(f"Subscribing to {ch['sub']}")
        client.subscribe(ch['sub'])

def on_message(client, userdata, msg):
    try:
        src_channel = get_channel_by_sub(msg.topic)
        if not src_channel:
            return

        payload_str = msg.payload.decode("utf-8")
        payload_dict = json.loads(payload_str)

        if not isinstance(payload_dict, dict):
            return

        message_content = payload_dict.get("payload", {})
        text = ""
        if isinstance(message_content, str):
            text = message_content
        elif isinstance(message_content, dict):
            text = message_content.get("text", "")

        if text.startswith("[Bridge]"):
            logger.info("Ignored own bridged message.")
            return

        if text:
            logger.info(f"[CH {src_channel['id']}] RX: {text} -> Sending to Tele & Cross-Link")
            if main_loop:
                main_loop.call_soon_threadsafe(
                    message_queue.put_nowait, 
                    (src_channel, text)
                )

            for dest_channel in CHANNELS:
                if dest_channel["id"] != src_channel["id"]:
                    try:
                        dest_node_id = get_node_id(dest_channel["pub"])
                        
                        bridged_text = f"[Bridge] {text}" 
                        
                        json_msg = {
                            "from": dest_node_id,
                            "payload": bridged_text,
                            "type": "sendtext"
                        }
                        
                        msg_out = json.dumps(json_msg).encode("utf-8")
                        client.publish(dest_channel["pub"], msg_out, qos=QOS)
                        logger.info(f"   -> Bridged to [CH {dest_channel['id']}]")
                        
                    except Exception as e:
                        logger.error(f"Bridge Error: {e}")

    except Exception as e:
        logger.error(f"MQTT Rx Error: {e}")

async def telegram_worker(app: Application):
    while True:
        channel, text = await message_queue.get()
        
        try:
            await app.bot.send_message(
                chat_id=CHAT_ID,
                message_thread_id=channel["topic_id"],
                text=text
            )
            
            if KEYWORD_PATTERN and KEYWORD_PATTERN.search(text):
                logger.info(f"[CH {channel['id']}] Keyword match! Sending receipt.")
                publish_to_mqtt(channel, "iBOT: Receptionat!")

        except Exception as e:
            logger.error(f"Telegram Tx Error: {e}")
        
        message_queue.task_done()

async def handle_telegram_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    thread_id = update.message.message_thread_id
    text = update.message.text
    
    channel = get_channel_by_topic_id(thread_id)
    
    if channel:
        logger.info(f"[CH {channel['id']}] Telegram -> MQTT: {text}")
        publish_to_mqtt(channel, text)
    else:
        pass

def publish_to_mqtt(channel, text):
    try:
        node_id = get_node_id(channel["pub"])
        json_msg = {
            "from": node_id,
            "payload": text,
            "type": "sendtext"
        }
        message_bytes = json.dumps(json_msg).encode("utf-8")
        mqtt_client.publish(channel["pub"], message_bytes, qos=QOS)
    except Exception as e:
        logger.error(f"MQTT Publish Error: {e}")

async def main():
    global mqtt_client, main_loop
    
    main_loop = asyncio.get_running_loop()

    mqtt_client = mqtt.Client(client_id=MQTT_CLIENT_ID)
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

    asyncio.create_task(telegram_worker(app))

    logger.info("Bridge is live. Press Ctrl+C to stop.")
    await app.updater.start_polling(poll_interval=2)
    
    try:
        await asyncio.Future() 
    except asyncio.CancelledError:
        logger.info("Stopping...")
    finally:
        await app.updater.stop()
        mqtt_client.loop_stop()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass