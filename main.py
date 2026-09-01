import os
import threading
import io
import re
import json
from flask import Flask
import telebot
from docx import Document

# 1. अपना टेलीग्राम बॉट टोकन यहाँ लिखें
TOKEN = "YOUR_BOT_TOKEN_HERE"
bot = telebot.TeleBot(TOKEN)

# 2. Render के लिए Flask Web Server
app = Flask(__name__)

@app.route('/')
def home():
    return "Quiz Bot is Live 24/7!"

# 3. स्टार्ट कमांड
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "नमस्ते! मैं आपका क्विज़ बॉट हूँ। मुझे केवल .docx फ़ाइल भेजें।")

# 4. हेल्प कमांड
@bot.message_handler(commands=['help'])
def send_help(message):
    bot.reply_to(message, "मदद:\n1. मुझे केवल .docx (Word) फ़ाइल भेजें।\n2. ग्रुप में मुझे एडमिन बनाए रखें।")

# 5. Document Handling (केवल .docx फ़ाइलों के लिए)
@bot.message_handler(content_types=['document'])
def handle_docs(message):
    try:
        file_name = message.document.file_name

        # केवल .docx फ़ाइलें स्वीकार करें
        if file_name and file_name.lower().endswith('.docx'):
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            
            doc = Document(io.BytesIO(downloaded_file))
            text = "\n".join([p.text for p in doc.paragraphs if p.text])
            
            bot.reply_to(message, f"DOCX फ़ाइल प्राप्त हुई! {len(doc.paragraphs)} पैराग्राफ सफलतापूर्वक लोड हुए।")
        else:
            bot.reply_to(message, "❌ अमान्य फ़ाइल! कृपया केवल .docx (Word) फ़ाइल ही भेजें।")
    except Exception as e:
        bot.reply_to(message, f"फ़ाइल प्रोसेस करने में त्रुटि: {str(e)}")

# 6. टेलीग्राम बॉट को बैकग्राउंड (Thread) में चलाना
def run_telegram_bot():
    bot.infinity_polling(skip_pending=True)

threading.Thread(target=run_telegram_bot, daemon=True).start()

# 7. सर्वर रन करना
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
