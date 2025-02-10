import os
from dotenv import load_dotenv
import telegram
import telegram.ext
import telegram.error
import asyncio
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

user_message = ""

async def check_for_message(app):
	global user_message

	while True:
		lock = FileLock('msg_to_telegram.lock')
		with lock:
			try:
				with open('msg_to_telegram.txt', 'r') as f:
					message = f.read().strip()

					if message:
						# Prevent the bot from repeating the message sent by the user in the Telegram chat
						if user_message != message:
							user_message = message
							try:
								await app.bot.send_message(chat_id=CHAT_ID, message_thread_id=TOPIC_ID, text=message)
								print("Message sent successfully: " + message)
							except Exception as e:
								print(f"Error sending message: {e}")
			except Exception as e:
				print(f"Error reading message file: {e}")

			with open('msg_to_telegram.txt', 'w') as f_clear:
				f_clear.write("")

		await asyncio.sleep(2)
		
async def handle_message(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE):
	global user_message

	if update.message.message_thread_id == TOPIC_ID:
		text = update.message.text
		user_message = text		# Prevent the bot from repeating the user's message
		print(f"Received message: {text}")
		
		lock = FileLock('msg_to_mqtt.lock')
		with lock:
			with open('msg_to_mqtt.txt', 'w') as f:
				f.write(text)
			print("Message written to MQTT file :" + text)

if __name__ == '__main__':
	print("Initializing bot...")
	app = telegram.ext.Application.builder().token(TOKEN).build()

	loop = asyncio.get_event_loop()
	loop.create_task(check_for_message(app))

	app.add_handler(telegram.ext.MessageHandler(telegram.ext.filters.TEXT, handle_message))

	print("Starting polling...")
	app.run_polling(poll_interval=3)
