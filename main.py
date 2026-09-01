import os
import threading
import io
from flask import Flask
import telebot
from docx import Document

# 1. अपना BOT TOKEN और Telegram USER ID यहाँ सही से डालें
TOKEN = "8813747977:AAGzH-xGM0wrEnxALh160g6gr9e-fVPCjE0"
OWNER_ID = "8183824919"

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Render के लिए Web Server endpoint
@app.route('/')
def home():
    return "Quiz Bot is Live 24/7!"

# स्टार्ट कमांड
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "नमस्ते! मैं आपका क्विज़ बॉट हूँ।")

# हेल्प कमांड
@bot.message_handler(commands=['help'])
def send_help(message):
    bot.reply_to(message, "मदद:\n1. मुझे केवल .docx (Word) फ़ाइल भेजें।\n2. ग्रुप में ऑनर का एडमिन होना ज़रूरी है।")

# Document Handling (केवल .docx फ़ाइलों के लिए)
@bot.message_handler(content_types=['document'])
def handle_docs(message):
    chat_id = message.chat.id
    
    # 1. ग्रुप में चेक करें कि ऑनर Admin/Creator है या नहीं
    if message.chat.type in ['group', 'supergroup']:
        try:
            member = bot.get_chat_member(chat_id, OWNER_ID)
            if member.status not in ['administrator', 'creator']:
                bot.reply_to(message, "❌ यह बॉट केवल उन्हीं ग्रुप्स में काम करता है जहाँ ऑनर एडमिन या क्रिएटर हैं।")
                return
        except Exception:
            bot.reply_to(message, "❌ ऑनर इस ग्रुप में मौजूद नहीं हैं या एडमिन नहीं हैं।")
            return

    # 2. फ़ाइल प्रोसेस करें
    try:
        file_name = message.document.file_name

        if file_name and file_name.lower().endswith('.docx'):
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            
            doc = Document(io.BytesIO(downloaded_file))
            text = "\n".join([p.text for p in doc.paragraphs if p.text])
            
            # 3. रिजल्ट तैयार करें
            result_msg = (
                f"📊 **क्विज़ फ़ाइल रिपोर्ट / रिजल्ट**\n\n"
                f"📁 **फ़ाइल:** {file_name}\n"
                f"👥 **ग्रुप:** {message.chat.title if message.chat.title else 'Private'}\n"
                f"📝 **कुल पैराग्राफ:** {len(doc.paragraphs)}"
            )

            # 4. केवल आपको (OWNER_ID) पर्सनल DM भेजना
            try:
                bot.send_message(OWNER_ID, result_msg, parse_mode="Markdown")
                bot.reply_to(message, "✅ फ़ाइल प्रोसेस हो गई है! रिजल्ट आपके पर्सनल Telegram DM में भेज दिया गया है।")
            except Exception:
                bot.reply_to(message, "⚠️ रिजल्ट तैयार है, लेकिन कृपया बॉट को पर्सनल चैट में जाकर पहले `/start` करें ताकि बॉट आपको मैसेज भेज सके।")
        else:
            bot.reply_to(message, "❌ अमान्य फ़ाइल! कृपया केवल .docx (Word) फ़ाइल ही भेजें।")
    except Exception as e:
        bot.reply_to(message, f"त्रुटि: {str(e)}")

# Telegram Bot को थ्रेड (बैकग्राउंड) में चलाना
def run_telegram_bot():
    bot.infinity_polling(skip_pending=True)

threading.Thread(target=run_telegram_bot, daemon=True).start()

# Flask Web Server स्टार्ट करना
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
