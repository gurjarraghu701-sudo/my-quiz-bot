import os, threading, time, io, re, json, docx, telebot
from telebot import types
from flask import Flask

# ---------------------------------------------------------
# 1. Koyeb सर्वर को 24/7 चालू रखने के लिए वेब-सर्वर
# ---------------------------------------------------------
app = Flask('')

@app.route('/')
def home():
    return "Bot is running 24/7 on Koyeb!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_web, daemon=True).start()

# ---------------------------------------------------------
# 2. सुरक्षा व बॉट सेटिंग्स (यहाँ अपनी डिटेल्स डालें)
# ---------------------------------------------------------
OWNER_ID = 8183824919
TOKEN = "8813747977:AAGzH-xGM0wrEnxALh160g6gr9e-fVPCjE0"

bot = telebot.TeleBot(TOKEN)

DATA_FILE = 'telegram_quizzes.json'
SETTINGS_FILE = 'telegram_settings.json'

def load_data(filepath):
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_data(filepath, data):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

QUIZ_STORE = load_data(DATA_FILE)
USER_SETTINGS = load_data(SETTINGS_FILE)
ACTIVE_SESSIONS = {}

def is_owner(user_id):
    return user_id == OWNER_ID

def get_show_voted(chat_id):
    return USER_SETTINGS.get(str(chat_id), {}).get('show_voted', True)

def set_show_voted(chat_id, val):
    str_id = str(chat_id)
    if str_id not in USER_SETTINGS:
        USER_SETTINGS[str_id] = {}
    USER_SETTINGS[str_id]['show_voted'] = val
    save_data(SETTINGS_FILE, USER_SETTINGS)

def extract_text(file_bytes, file_name):
    file_name = file_name.lower()
    if file_name.endswith('.txt'):
        try: return file_bytes.decode('utf-8')
        except: return file_bytes.decode('latin-1', errors='ignore')
    elif file_name.endswith('.docx'):
        doc = docx.Document(io.BytesIO(file_bytes))
        return "\n".join([p.text for p in doc.paragraphs])
    return None

# /start और /help (केवल मालिक के लिए)
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    if not is_owner(message.from_user.id):
        return

    chat_id = message.chat.id
    status = "🟢 चालू (नाम दिखेंगे)" if get_show_voted(chat_id) else "🔴 बंद (Anonymous)"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(f"👁️ Show Who Voted: {status}", callback_data="toggle_show_voted"))
    
    help_text = (
        "🤖 **24/7 मुफ़्त टेलीग्राम क्विज़ बॉट**\n\n"
        "• अपनी `.txt` या `.docx` फ़ाइल DM में भेजकर क्विज़ बनाएं।\n"
        "• किसी भी ग्रुप/चैनल में चलाएं: `/startquiz quiz_1`\n\n"
        "⚙️ **वर्तमान सेटिंग:** Show Who Voted is **" + status + "**"
    )
    bot.reply_to(message, help_text, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "toggle_show_voted")
def callback_toggle(call):
    if not is_owner(call.from_user.id):
        return

    chat_id = call.message.chat.id
    new_val = not get_show_voted(chat_id)
    set_show_voted(chat_id, new_val)
    status = "🟢 चालू (नाम दिखेंगे)" if new_val else "🔴 बंद (Anonymous)"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(f"👁️ Show Who Voted: {status}", callback_data="toggle_show_voted"))
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=f"✅ **सेटिंग अपडेट हो गई!**\n\n👁️ **Show Who Voted:** {status}",
        reply_markup=markup,
        parse_mode="Markdown"
    )

# फ़ाइल अपलोड
@bot.message_handler(content_types=['document'])
def handle_doc(message):
    if not is_owner(message.from_user.id):
        return

    try:
        file_name = message.document.file_name
        if not (file_name.lower().endswith('.txt') or file_name.lower().endswith('.docx')):
            bot.reply_to(message, "⚠️ केवल `.txt` या `.docx` फ़ाइल भेजें!")
            return

        file_info = bot.get_file(message.document.file_id)
        file_bytes = bot.download_file(file_info.file_path)
        content = extract_text(file_bytes, file_name)

        if not content:
            bot.reply_to(message, "❌ फ़ाइल पढ़ने में असमर्थ!")
            return

        blocks = re.split(r'\n\s*\n', content.replace('\r\n', '\n').strip())
        quiz_data = []

        for block in blocks:
            lines = [l.strip() for l in block.strip().split("\n") if l.strip()]
            if len(lines) < 3: continue
            
            q_text = lines[0].replace("Q:", "").strip()[:300]
            opts, correct_id = [], 0
            
            for line in lines[1:]:
                if line.startswith("Ans:"):
                    correct_id = ord(line.replace("Ans:", "").strip().upper()) - ord('A')
                    continue
                clean_opt = re.sub(r'^[A-D\*\)\.\s]+', '', line).strip()
                if clean_opt: opts.append(clean_opt[:100])
                
            if len(opts) >= 2 and 0 <= correct_id < len(opts):
                quiz_data.append({'question': q_text, 'options': opts[:10], 'correct_id': correct_id})

        if not quiz_data:
            bot.reply_to(message, "❌ फ़ाइल में वैध प्रश्न नहीं मिले।")
            return

        quiz_id = f"quiz_{len(QUIZ_STORE) + 1}"
        QUIZ_STORE[quiz_id] = quiz_data
        save_data(DATA_FILE, QUIZ_STORE)

        bot.reply_to(
            message,
            f"🎯 **क्विज़ सेव हो गया!**\n\n🆔 **Quiz ID:** `{quiz_id}`\n❓ **प्रश्न:** {len(quiz_data)}\n\n🚀 चलाएं: `/startquiz {quiz_id}`",
            parse_mode="Markdown"
        )
    except Exception as e:
        bot.reply_to(message, f"❌ एरर: {str(e)}")

# पोल उत्तर ट्रैकर
@bot.poll_answer_handler()
def handle_poll_answer(poll_answer):
    poll_id = poll_answer.poll_id
    user = poll_answer.user
    selected = poll_answer.option_ids[0] if poll_answer.option_ids else None

    for chat_id, session in ACTIVE_SESSIONS.items():
        if poll_id in session['polls']:
            correct_id = session['polls'][poll_id]
            if selected == correct_id:
                if user.id not in session['scores']:
                    session['scores'][user.id] = {'name': user.first_name, 'score': 0}
                session['scores'][user.id]['score'] += 1
            break

# क्विज़ रनर
def process_start_quiz(chat_id, quiz_id):
    if quiz_id not in QUIZ_STORE:
        bot.send_message(chat_id, "❌ यह Quiz ID नहीं मिली।")
        return

    quiz_data = QUIZ_STORE[quiz_id]
    show_voted = get_show_voted(chat_id)
    is_anon = not show_voted

    ACTIVE_SESSIONS[chat_id] = {'scores': {}, 'polls': {}, 'is_active': True}

    bot.send_message(
        chat_id,
        f"🏆 **क्विज़ शुरू हो रही है!**\n\n📊 **प्रश्न:** {len(quiz_data)}\n⏱️ **समय:** 30 सेकंड/प्रश्न\n\n⏳ *10 सेकंड में पहला प्रश्न...*",
        parse_mode="Markdown"
    )
    time.sleep(10)

    for idx, q in enumerate(quiz_data, start=1):
        if not ACTIVE_SESSIONS.get(chat_id, {}).get('is_active', False): break

        msg = bot.send_poll(
            chat_id=chat_id,
            question=f"[{idx}/{len(quiz_data)}] {q['question']}",
            options=q['options'],
            type="quiz",
            correct_option_id=q['correct_id'],
            open_period=30,
            is_anonymous=is_anon
        )
        ACTIVE_SESSIONS[chat_id]['polls'][msg.poll.id] = q['correct_id']
        time.sleep(32)

    if ACTIVE_SESSIONS.get(chat_id, {}).get('is_active', False):
        scores = ACTIVE_SESSIONS[chat_id]['scores']
        sorted_scores = sorted(scores.values(), key=lambda x: x['score'], reverse=True)

        leaderboard = "🏁 **क्विज़ समाप्त!** 🏁\n\n🏆 **लीडरबोर्ड:**\n\n"
        if not sorted_scores:
            leaderboard += "किसी भी प्रतिभागी ने सही उत्तर नहीं दिया।"
        else:
            medals = ["🥇", "🥈", "🥉"]
            for rank, u in enumerate(sorted_scores[:10], start=1):
                badge = medals[rank-1] if rank <= 3 else f"{rank}."
                leaderboard += f"{badge} **{u['name']}** — {u['score']}/{len(quiz_data)} सही उत्तर\n"

        bot.send_message(chat_id, leaderboard, parse_mode="Markdown")

    if chat_id in ACTIVE_SESSIONS:
        del ACTIVE_SESSIONS[chat_id]

@bot.message_handler(commands=['startquiz'])
def handle_startquiz_msg(message):
    if not is_owner(message.from_user.id): return
    args = message.text.split()
    if len(args) < 2: return
    process_start_quiz(message.chat.id, args[1].strip())

@bot.channel_post_handler(func=lambda post: post.text and post.text.startswith('/startquiz'))
def handle_startquiz_channel(post):
    args = post.text.split()
    if len(args) >= 2: process_start_quiz(post.chat.id, args[1].strip())

print("✅ Koyeb सर्वर पर बॉट सफलता से चालू हो चुका है!")
bot.infinity_polling(skip_pending=True)
