import os
import re
import json
import time
import locale
import asyncio
import logging
from pathlib import Path
from datetime import datetime
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
WX_PATTERN = re.compile(r"\b(wx|meteo)\b", re.IGNORECASE)
IBOT_PATTERN = re.compile(r"\bibot\b", re.IGNORECASE)

# Ignored/banned nodes from .env (completely ignored - no messages processed)
IGNORED_NODES_RAW = os.getenv("IGNORED_NODES", "")
IGNORED_NODE_IDS = set()
IGNORED_NODE_PATTERNS = []

for item in IGNORED_NODES_RAW.split(","):
    item = item.strip()
    if not item:
        continue
    if item.startswith("!"):
        # Hex ID - convert to decimal
        try:
            IGNORED_NODE_IDS.add(int(item[1:], 16))
        except ValueError:
            pass
    elif item.isdigit():
        # Decimal node ID
        IGNORED_NODE_IDS.add(int(item))
    else:
        # Shortname pattern
        IGNORED_NODE_PATTERNS.append(re.compile(re.escape(item), re.IGNORECASE))

logger.info(f"Ignored node IDs: {IGNORED_NODE_IDS}")
logger.info(f"Ignored patterns: {[p.pattern for p in IGNORED_NODE_PATTERNS]}")

def is_wx_request(text):
    """Check if text contains both 'ibot' AND ('wx' OR 'meteo')"""
    return IBOT_PATTERN.search(text) and WX_PATTERN.search(text)

def is_node_ignored(node_id, shortname="", db=None):
    """Check if node should be completely ignored.
    If node matches a pattern, save its ID to database for permanent ban."""
    # Check by node ID from .env
    if node_id in IGNORED_NODE_IDS:
        return True

    # Check by node ID from database ban list
    if db and "banned_nodes" in db:
        if str(node_id) in db["banned_nodes"]:
            return True

    # Check shortname against patterns
    if shortname:
        for pattern in IGNORED_NODE_PATTERNS:
            if pattern.search(shortname):
                # Found match! Save node_id to database for permanent ban
                if db is not None:
                    add_to_ban_list(db, node_id, shortname, pattern.pattern)
                return True
    return False

def add_to_ban_list(db, node_id, shortname, matched_pattern):
    """Add node to permanent ban list in database"""
    if "banned_nodes" not in db:
        db["banned_nodes"] = {}

    node_id_str = str(node_id)
    if node_id_str not in db["banned_nodes"]:
        db["banned_nodes"][node_id_str] = {
            "shortname": shortname,
            "matched_pattern": matched_pattern,
            "banned_at": datetime.now().isoformat(),
            "hex_id": f"!{node_id:08x}"
        }
        save_database(db)
        logger.warning(f"BANNED: Node {shortname} ({node_id_str} / !{node_id:08x}) added to permanent ban list (matched: {matched_pattern})")

# Database
DB_PATH = Path(__file__).parent / "nodes.json"

def load_database():
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"bot_config": {"node_hex_id": "", "telegram_name": "iBOT"}, "nodes": {}, "channels": {}}

def save_database(db):
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)

def update_node(db, node_id, data):
    node_id_str = str(node_id)
    now = datetime.now().isoformat()

    if node_id_str not in db["nodes"]:
        db["nodes"][node_id_str] = {
            "hex_id": "",
            "shortname": "",
            "longname": "",
            "last_seen": now,
            "position": None,
            "telemetry": None
        }

    node = db["nodes"][node_id_str]
    node["last_seen"] = now

    for key in ["hex_id", "shortname", "longname"]:
        if key in data and data[key]:
            node[key] = data[key]

    if "position" in data:
        node["position"] = data["position"]
        node["position"]["time"] = now

    if "telemetry" in data:
        if node["telemetry"] is None:
            node["telemetry"] = {}
        node["telemetry"].update(data["telemetry"])
        node["telemetry"]["last_update"] = now

    save_database(db)

def update_channel(db, channel_name):
    if channel_name not in db["channels"]:
        db["channels"][channel_name] = {
            "first_seen": datetime.now().isoformat()
        }
        save_database(db)

def get_shortname(db, node_id):
    node_id_str = str(node_id)
    if node_id_str in db["nodes"]:
        shortname = db["nodes"][node_id_str].get("shortname", "")
        if shortname:
            return shortname
    # Return hex ID if no shortname
    our_node_id = get_node_id(MQTT_TOPIC_PUB)
    if node_id == our_node_id:
        return db["bot_config"].get("telegram_name", "iBOT")
    return f"!{node_id:08x}"

def get_channel_from_topic(topic):
    # Extract channel name from topic like msh/EU_433/2/json/radioamator/!4339679c
    parts = topic.split("/")
    if len(parts) >= 5:
        return parts[4]  # radioamator, LongFast, mqtt, etc.
    return "unknown"

def get_weather_data(db, max_chars=200):
    """Get weather data from nodes with recent telemetry (< 90 minutes)
    Returns list of message chunks (max 2 messages of 200 chars each)"""
    MAX_AGE_MINUTES = 90
    now = datetime.now()
    weather_reports = []

    for node_id_str, node in db["nodes"].items():
        shortname = node.get("shortname", "")

        # Skip ignored/banned nodes
        try:
            node_id_int = int(node_id_str)
        except ValueError:
            continue
        if is_node_ignored(node_id_int, shortname, db):
            continue

        telemetry = node.get("telemetry")
        if not telemetry:
            continue

        # Check if we have temperature
        temp = telemetry.get("temperature")
        if temp is None:
            continue

        # Check data age
        last_update = telemetry.get("last_update")
        if not last_update:
            continue

        try:
            update_time = datetime.fromisoformat(last_update)
            age_minutes = (now - update_time).total_seconds() / 60

            if age_minutes > MAX_AGE_MINUTES:
                continue

            # Format age - compact
            if age_minutes < 1:
                age_str = "0m"
            elif age_minutes < 60:
                age_str = f"{int(age_minutes)}m"
            else:
                age_str = f"{int(age_minutes / 60)}h{int(age_minutes % 60)}m"

            # Get node name
            if not shortname:
                shortname = f"!{node_id_int:08x}"

            # Build weather string - compact format with pressure
            wx_str = f"{shortname}:{temp:.1f}C"

            humidity = telemetry.get("relative_humidity")
            if humidity is not None:
                wx_str += f",{humidity:.0f}%"

            pressure = telemetry.get("barometric_pressure")
            if pressure is not None:
                wx_str += f",{pressure:.0f}hPa"

            wx_str += f"({age_str})"
            weather_reports.append((age_minutes, wx_str))

        except (ValueError, TypeError):
            continue

    if not weather_reports:
        return None

    # Sort by age (freshest first)
    weather_reports.sort(key=lambda x: x[0])

    # Build response chunks (max 2 messages of 200 chars each)
    chunks = []
    current_chunk = []
    current_len = 7  # "Meteo: " prefix

    for _, report in weather_reports:
        separator = " | " if current_chunk else ""
        needed = len(separator) + len(report)

        if current_len + needed <= max_chars:
            current_chunk.append(report)
            current_len += needed
        elif len(chunks) == 0:
            # Start second chunk
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = [report]
            current_len = 7 + len(report)  # "Meteo: " + report
        else:
            # Already have 2 chunks, stop
            break

    if current_chunk:
        chunks.append(current_chunk)

    return chunks

# Global database
db = load_database()

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
BROADCAST_ID = 4294967295  # 0xFFFFFFFF

def on_message(client, userdata, msg):
    global db
    try:
        if not mqtt.topic_matches_sub(MQTT_TOPIC_SUB, msg.topic):
            return

        channel_name = get_channel_from_topic(msg.topic)
        update_channel(db, channel_name)

        payload_str = msg.payload.decode("utf-8")
        payload_dict = json.loads(payload_str)

        if not isinstance(payload_dict, dict):
            return

        sender_id = payload_dict.get("from", 0)
        sender_gateway = payload_dict.get("sender", "")
        msg_type = payload_dict.get("type", "")
        payload_content = payload_dict.get("payload", {})

        # Log for debugging - raw payload for text messages
        if msg_type == "text":
            logger.info(f"DEBUG RAW: gateway={sender_gateway}, from={sender_id}, payload={payload_content}")

        # Ignore messages from our own node
        our_node_id = get_node_id(MQTT_TOPIC_PUB)
        if sender_id == our_node_id:
            return

        # Check if node is ignored/banned
        # Get shortname from db OR from nodeinfo payload (for new nodes)
        node_shortname = ""
        if str(sender_id) in db.get("nodes", {}):
            node_shortname = db["nodes"][str(sender_id)].get("shortname", "")
        # Also check nodeinfo payload for new nodes
        if msg_type == "nodeinfo" and isinstance(payload_content, dict):
            incoming_shortname = payload_content.get("shortname", "")
            if incoming_shortname:
                node_shortname = incoming_shortname
        if is_node_ignored(sender_id, node_shortname, db):
            logger.info(f"Ignored banned node: {sender_id} ({node_shortname})")
            return

        # Process nodeinfo - learn about nodes
        if msg_type == "nodeinfo" and isinstance(payload_content, dict):
            node_data = {
                "hex_id": payload_content.get("id", ""),
                "shortname": payload_content.get("shortname", ""),
                "longname": payload_content.get("longname", "")
            }
            update_node(db, sender_id, node_data)
            logger.info(f"DB: Updated node {node_data.get('shortname', sender_id)}")
            return

        # Process position - learn node positions
        if msg_type == "position" and isinstance(payload_content, dict):
            lat = payload_content.get("latitude_i", 0) / 10000000.0
            lon = payload_content.get("longitude_i", 0) / 10000000.0
            alt = payload_content.get("altitude", 0)
            if lat != 0 and lon != 0:
                position_data = {"position": {"latitude": lat, "longitude": lon, "altitude": alt}}
                update_node(db, sender_id, position_data)
                logger.info(f"DB: Updated position for {get_shortname(db, sender_id)}")
            return

        # Process telemetry - learn sensor data
        if msg_type == "telemetry" and isinstance(payload_content, dict):
            telemetry_data = {"telemetry": {}}
            for key in ["temperature", "relative_humidity", "barometric_pressure", "battery_level", "voltage"]:
                if key in payload_content:
                    telemetry_data["telemetry"][key] = payload_content[key]
            if telemetry_data["telemetry"]:
                update_node(db, sender_id, telemetry_data)
                logger.info(f"DB: Updated telemetry for {get_shortname(db, sender_id)}")
            return

        # Process text messages - all channels
        if msg_type == "text":

            text = ""
            if isinstance(payload_content, str):
                text = payload_content
            elif isinstance(payload_content, dict):
                text = payload_content.get("text", "")

            # Check for corrupted messages (null bytes = corrupted diacritics from bad gateway)
            if '\x00' in text:
                logger.info(f"Skipped corrupted message from {sender_gateway}: {repr(text)}")
                return

            # Strip whitespace and check if empty
            text = text.strip()
            if not text:
                return

            # Ignore own bot messages
            if text.startswith("iBOT:"):
                return

            # Deduplication - 30 seconds window
            now = time.time()
            # Use message ID if available, otherwise use sender + text
            msg_id = payload_dict.get("id", 0)
            if msg_id:
                dedup_key = f"{sender_id}:{msg_id}"
            else:
                dedup_key = f"{sender_id}:{text[:20] if len(text) > 20 else text}"
            msg_hash = hash(dedup_key)

            logger.info(f"DEBUG DEDUP: key={dedup_key}, hash={msg_hash}, gateway={sender_gateway}")

            if msg_hash in recent_messages and (now - recent_messages[msg_hash]) < 30:
                logger.info(f"Ignored duplicate: {text}")
                return
            recent_messages[msg_hash] = now

            # Cleanup old entries
            for k in list(recent_messages.keys()):
                if now - recent_messages[k] > 60:
                    del recent_messages[k]

            # Format message for Telegram
            sender_name = get_shortname(db, sender_id)
            to_id = payload_dict.get("to", BROADCAST_ID)
            hops_away = payload_dict.get("hops_away", 0)

            # Detect if message came via mqtt or radio
            # Convert sender gateway hex to int and compare with from
            gateway_hex = sender_gateway.replace("!", "")
            try:
                gateway_id = int(gateway_hex, 16)
            except:
                gateway_id = 0

            via = "mqtt" if gateway_id == sender_id else "radio"

            if to_id == BROADCAST_ID:
                # Broadcast message on channel
                formatted_text = f"[{channel_name}] {sender_name} ({via}): {text}"
            else:
                # Private message
                to_name = get_shortname(db, to_id)
                formatted_text = f"[{sender_name} -> {to_name}] ({via}): {text}"

            logger.info(f"MQTT RX: {formatted_text}")
            if main_loop:
                main_loop.call_soon_threadsafe(
                    message_queue.put_nowait,
                    (formatted_text, sender_name, hops_away, to_id)
                )

    except Exception as e:
        logger.error(f"MQTT Rx Error: {e}")

async def telegram_worker(app: Application):
    bot_name = db["bot_config"].get("telegram_name", "iBOT")
    logger.info("Telegram worker started")

    while True:
        msg_data = await message_queue.get()
        logger.info(f"TG Worker received: {msg_data}")
        formatted_text, sender_name, hops_away, to_id = msg_data

        try:
            logger.info(f"Sending to Telegram: {formatted_text}")
            await app.bot.send_message(
                chat_id=CHAT_ID,
                message_thread_id=TOPIC_ID,
                text=formatted_text
            )
            logger.info("Telegram message sent successfully")

            if KEYWORD_PATTERN and KEYWORD_PATTERN.search(formatted_text):
                logger.info("Keyword match! Sending receipt.")

                # Build response based on hops
                if hops_away == 0:
                    hop_info = "direct"
                else:
                    hop_info = f"prin {hops_away} hop" if hops_away == 1 else f"prin {hops_away} hop-uri"

                # Message for mesh (no prefix, node has shortname)
                mqtt_receipt = f"Roger {sender_name}, te aud {hop_info}!"
                publish_to_mqtt(mqtt_receipt)

                # Message for Telegram (with prefix)
                tg_receipt = f"[{bot_name}]: {mqtt_receipt}"
                await app.bot.send_message(
                    chat_id=CHAT_ID,
                    message_thread_id=TOPIC_ID,
                    text=tg_receipt
                )

            # Check for WX/meteo request (must contain "ibot" AND "wx" or "meteo")
            if is_wx_request(formatted_text):
                logger.info("WX request detected!")
                weather_chunks = get_weather_data(db, max_chars=200)

                if weather_chunks:
                    # Send each chunk as separate message (max 2)
                    all_reports = []
                    for chunk in weather_chunks:
                        wx_response = "Meteo: " + " | ".join(chunk)
                        publish_to_mqtt(wx_response)
                        all_reports.extend(chunk)

                    # Message for Telegram (with prefix, full list)
                    tg_wx = f"[{bot_name}]: Meteo:\n" + "\n".join(all_reports)
                    await app.bot.send_message(
                        chat_id=CHAT_ID,
                        message_thread_id=TOPIC_ID,
                        text=tg_wx
                    )
                else:
                    no_data_msg = "Meteo: Nu am date recente"
                    publish_to_mqtt(no_data_msg)

                    tg_no_data = f"[{bot_name}]: {no_data_msg}"
                    await app.bot.send_message(
                        chat_id=CHAT_ID,
                        message_thread_id=TOPIC_ID,
                        text=tg_no_data
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

        # Check for WX request from Telegram (only needs "wx" or "meteo")
        if WX_PATTERN.search(text):
            logger.info("WX request from Telegram!")
            bot_name = db["bot_config"].get("telegram_name", "iBOT")
            weather_chunks = get_weather_data(db, max_chars=200)

            if weather_chunks:
                # Send each chunk to mesh (max 2)
                all_reports = []
                for chunk in weather_chunks:
                    wx_response = "Meteo: " + " | ".join(chunk)
                    publish_to_mqtt(wx_response)
                    all_reports.extend(chunk)

                tg_wx = f"[{bot_name}]: Meteo:\n" + "\n".join(all_reports)
                await update.message.reply_text(tg_wx)
            else:
                no_data_msg = "Meteo: Nu am date recente"
                publish_to_mqtt(no_data_msg)
                await update.message.reply_text(f"[{bot_name}]: {no_data_msg}")
        else:
            # Normal message - forward to MQTT
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
