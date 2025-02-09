import os
from dotenv import load_dotenv
import telegram
import telegram.ext
import telegram.error
import asyncio
import json
from filelock import FileLock

load_dotenv()

TOKEN = os.getenv("TOKEN")
if not TOKEN:
	raise ValueError("Token not provided")

CHAT_ID = int(os.getenv('CHAT_ID'))
if not CHAT_ID:
	raise ValueError("Chat ID not provided")

TOPIC_ID = int(os.getenv('TOPIC_ID'))
if not TOPIC_ID:
	raise ValueError("Topic ID not provided")

async def check_for_message(app):
    last_message = ""

    while True:
        lock = FileLock('msg_to_telegram.lock')
        with lock:
            print("Acquiring lock to check message file.")
            try:
                with open('msg_to_telegram.txt', 'r') as f:
                    message = f.read().strip()

                    print(f"Read message: {repr(message)}")
                    print(f"Last message before check: {repr(last_message)}")

                    if message:
                        if last_message != message:
                            print(f"Last message is different from current message. Updating and sending.")
                            last_message = message
                            try:
                                await app.bot.send_message(chat_id=CHAT_ID, message_thread_id=TOPIC_ID, text=message)
                                print("Message sent successfully.")
                            except Exception as e:
                                print(f"Error sending message: {e}")
                        else:
                            print("Message is the same as the last one. Not sending.")
                    else:
                        print("Message in the file is empty. Skipping send.")

            except Exception as e:
                print(f"Error reading message file: {e}")

            print("Clearing message file.")
            with open('msg_to_telegram.txt', 'w') as f_clear:
                f_clear.write("")

        await asyncio.sleep(2)
async def handle_message(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE):
	if update.message.message_thread_id == TOPIC_ID:
		text = update.message.text
		print(f"Received message: {text}")
		
		lock = FileLock('msg_to_mqtt.lock')
		with lock:
			print("Acquiring lock to write to message file.")
			with open('msg_to_mqtt.txt', 'w') as f:
				f.write(text)
			print("Message written to MQTT file.")

if __name__ == '__main__':
	print("Initializing bot...")
	app = telegram.ext.Application.builder().token(TOKEN).build()

	loop = asyncio.get_event_loop()
	loop.create_task(check_for_message(app))

	app.add_handler(telegram.ext.MessageHandler(telegram.ext.filters.TEXT, handle_message))

	print("Starting polling...")
	app.run_polling(poll_interval=3)
