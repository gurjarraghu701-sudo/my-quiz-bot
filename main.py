import os
import threading
import io
import re
import time
import json
import sqlite3
from flask import Flask
import telebot
from telebot import types
from docx import Document
from weasyprint import HTML

# ==========================================
# 1. कॉन्फ़िगरेशन एवं सुरक्षा (Environment Variables)
# ==========================================
TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", "1234567890"))  # 🔒 सुरक्षित Environment Variable से लोड

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

@app.route('/')
def home():
    return "OK", 200


# ==========================================
# 2. SQLite डेटाबेस मैनेजमेंट
# ==========================================
DB_FILE = "quiz_database.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quizzes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            questions TEXT,
            timer INTEGER DEFAULT 15,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def save_quiz_to_db(title, questions, timer):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO quizzes (title, questions, timer) VALUES (?, ?, ?)",
        (title, json.dumps(questions), timer)
    )
    quiz_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return quiz_id

def get_all_quizzes():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, questions, timer FROM quizzes ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    
    quiz_list = []
    for r in rows:
        quiz_list.append({
            'id': r[0],
            'title': r[1],
            'questions': json.loads(r[2]),
            'timer': r[3]
        })
    return quiz_list

def get_quiz_by_id(quiz_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, questions, timer FROM quizzes WHERE id = ?", (quiz_id,))
    r = cursor.fetchone()
    conn.close()
    if r:
        return {
            'id': r[0],
            'title': r[1],
            'questions': json.loads(r[2]),
            'timer': r[3]
        }
    return None

def delete_quiz_from_db(quiz_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM quizzes WHERE id = ?", (quiz_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


pending_uploads = {}
leaderboards = {}
poll_tracker = {}
group_quiz_messages = {}


# ==========================================
# 3. स्टार्ट कमांड्स एवं हेल्प मैसेज
# ==========================================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    if message.chat.type == 'private':
        msg = "🏁 **नमस्ते! मैं आपका Multi-Quiz Bot हूँ।**\n\n📌 **उपयोग कैसे करें:**\n• मुझे कोई भी `.docx` फ़ाइल यहाँ DM में भेजकर क्विज़ सेव करें।\n• `/myquizzes` - अपनी सभी सेव की हुई क्विज़ देखें।\n• `/deletequiz ID` - कोई क्विज़ डिलीट करें\n• ग्रुप में क्विज़ चलाने के लिए ग्रुप में `/startquiz` लिखें।"
        bot.reply_to(message, msg, parse_mode="Markdown")
    else:
        bot.reply_to(message, "नमस्ते! ग्रुप में क्विज़ शुरू करने के लिए ऑनर `/startquiz` लिखें।")


# ==========================================
# 4. Docx Parsing एवं फ़ाइल हैंडलिंग
# ==========================================
def parse_quiz_text(text):
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    quizzes = []
    
    current_q = None
    current_opts = []
    correct_idx = None

    for line in lines:
        opt_match = re.match(r'^(\*?\s*)([a-dA-D])[\.\)]\s*(.+)$', line)
        q_match = re.match(r'^\d+[\.\)]\s*(.+)$', line)
        
        if opt_match:
            is_correct = '*' in opt_match.group(1)
            opt_text = opt_match.group(3).strip()
            current_opts.append(opt_text[:100])
            if is_correct:
                correct_idx = len(current_opts) - 1
        elif q_match:
            if current_q and current_opts and correct_idx is not None:
                quizzes.append({
                    'question': current_q[:300],
                    'options': current_opts,
                    'correct_id': correct_idx
                })
            current_q = q_match.group(1).strip()
            current_opts = []
            correct_idx = None
        else:
            if not current_opts:
                if current_q:
                    current_q += " " + line
                else:
                    current_q = line

    if current_q and current_opts and correct_idx is not None:
        quizzes.append({
            'question': current_q[:300],
            'options': current_opts,
            'correct_id': correct_idx
        })
        
    return quizzes

@bot.message_handler(content_types=['document'])
def handle_docs(message):
    if message.chat.type != 'private':
        bot.reply_to(message, "❌ **सुरक्षा चेतावनी:** क्विज़ फ़ाइल केवल बॉट के पर्सनल DM में ही भेजें!")
        return
    
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "❌ केवल बॉट ऑनर ही क्विज़ अपलोड कर सकता है।")
        return

    try:
        file_name = message.document.file_name

        if file_name and file_name.lower().endswith('.docx'):
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            
            doc = Document(io.BytesIO(downloaded_file))
            full_text = "\n".join([p.text for p in doc.paragraphs if p.text])
            
            quizzes = parse_quiz_text(full_text)
            
            if not quizzes:
                bot.reply_to(message, "⚠️ फ़ाइल में प्रश्न सही प्रारूप में नहीं मिले। सही उत्तर के आगे (*) लगा होना चाहिए।")
                return
            
            title = file_name.rsplit('.', 1)[0]
            pending_uploads[OWNER_ID] = {
                'title': title,
                'quizzes': quizzes
            }

            markup = types.InlineKeyboardMarkup()
            btn15 = types.InlineKeyboardButton("⏱️ 15 सेकंड", callback_data="save_timer_15")
            btn20 = types.InlineKeyboardButton("⏱️ 20 सेकंड", callback_data="save_timer_20")
            btn30 = types.InlineKeyboardButton("⏱️ 30 सेकंड", callback_data="save_timer_30")
            markup.add(btn15, btn20, btn30)
            
            upload_msg = f"📥 **नई फ़ाइल प्राप्त हुई!**\n\n📌 **शीर्षक:** `{title}`\n📊 **कुल प्रश्न:** {len(quizzes)}\n\n⚙️ **इस क्विज़ के लिए प्रति प्रश्न समय सीमा चुनें और सेव करें:**"
            bot.reply_to(message, upload_msg, reply_markup=markup, parse_mode="Markdown")
        else:
            bot.reply_to(message, "❌ केवल `.docx` (Word) फ़ाइल ही भेजें।")
    except Exception as e:
        bot.reply_to(message, f"त्रुटि: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('save_timer_'))
def save_timer_callback(call):
    if call.from_user.id != OWNER_ID:
        bot.answer_callback_query(call.id, "❌ केवल ऑनर यह कर सकता है।", show_alert=True)
        return
    
    timer_val = int(call.data.split('_')[2])
    
    if OWNER_ID in pending_uploads:
        data = pending_uploads.pop(OWNER_ID)
        quiz_id = save_quiz_to_db(data['title'], data['quizzes'], timer_val)
        
        saved_msg = f"✅ **क्विज़ सफलतापूर्वक सेव हो गया!**\n\n🆔 **Quiz ID:** `{quiz_id}`\n📌 **नाम:** `{data['title']}`\n📊 **प्रश्न:** {len(data['quizzes'])}\n⏱️ **समय:** {timer_val} सेकंड प्रति प्रश्न\n\n💡 आप कभी भी इसे चलाने के लिए ग्रुप में `/startquiz` या `/startquiz {quiz_id}` लिखें।"
        bot.edit_message_text(
            saved_msg,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown"
        )
    else:
        bot.answer_callback_query(call.id, "⚠️ क्विज़ डेटा नहीं मिला।", show_alert=True)


# ==========================================
# 5. क्विज़ प्रबंधन कमांड्स
# ==========================================
@bot.message_handler(commands=['myquizzes'])
def list_my_quizzes(message):
    if message.from_user.id != OWNER_ID:
        return
    
    quizzes = get_all_quizzes()
    if not quizzes:
        bot.reply_to(message, "📭 आपके पास अभी कोई सेव्ड क्विज़ नहीं है। मुझे `.docx` फ़ाइल भेजकर सेव करें।")
        return

    text = "📚 **आपकी सेव की हुई क्विज़ लिस्ट:**\n\n"
    for q in quizzes:
        text += f"🔹 **ID {q['id']}:** {q['title']}\n   └ 📊 {len(q['questions'])} प्रश्न | ⏱️ {q['timer']} सेकंड\n\n"
    
    text += "💡 **डिलीट करने के लिए:** `/deletequiz ID` लिखें।"
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(commands=['deletequiz'])
def delete_quiz_cmd(message):
    if message.from_user.id != OWNER_ID:
        return
    
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(message, "⚠️ कृपया क्विज़ ID लिखें। उदाहरण: `/deletequiz 1`")
        return

    q_id = int(parts[1])
    if delete_quiz_from_db(q_id):
        bot.reply_to(message, f"✅ Quiz ID `{q_id}` सफलतापूर्वक बॉट से डिलीट कर दिया गया।")
    else:
        bot.reply_to(message, f"❌ Quiz ID `{q_id}` नहीं मिला।")


# ==========================================
# 6. ग्रुप क्विज़ निष्पादक
# ==========================================
@bot.message_handler(commands=['startquiz'])
def start_quiz_in_group(message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if message.chat.type not in ['group', 'supergroup']:
        bot.reply_to(message, "❌ यह कमांड केवल टेलीग्राम ग्रुप में चलाएं।")
        return

    if user_id != OWNER_ID:
        bot.reply_to(message, "❌ केवल बॉट ऑनर ही क्विज़ प्रतियोगिता शुरू कर सकता है!")
        return

    try:
        member = bot.get_chat_member(chat_id, OWNER_ID)
        if member.status not in ['administrator', 'creator']:
            bot.reply_to(message, "❌ यह क्विज़ केवल तभी चलेगा जब ऑनर इस ग्रुप में एडमिन/क्रिएटर हो।")
            return
    except Exception:
        bot.reply_to(message, "❌ एडमिन स्थिति सत्यापित नहीं हो सकी।")
        return

    parts = message.text.split()
    
    if len(parts) > 1 and parts[1].isdigit():
        q_id = int(parts[1])
        quiz_data = get_quiz_by_id(q_id)
        if quiz_data:
            launch_quiz(chat_id, quiz_data)
        else:
            bot.reply_to(message, f"❌ Quiz ID `{q_id}` नहीं मिला। `/myquizzes` करके सही ID देखें।")
        return

    quizzes = get_all_quizzes()
    if not quizzes:
        bot.reply_to(message, "📭 कोई सेव्ड क्विज़ नहीं मिला! पहले बॉट के DM में `.docx` फ़ाइल भेजें।")
        return

    markup = types.InlineKeyboardMarkup()
    for q in quizzes:
        btn_text = f"▶️ [{q['id']}] {q['title']} ({len(q['questions'])} Qs)"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"launch_{q['id']}"))

    bot.reply_to(
        message, 
        "📚 **कृपया इस ग्रुप में चलाने के लिए क्विज़ चुनें:**", 
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('launch_'))
def launch_quiz_callback(call):
    if call.from_user.id != OWNER_ID:
        bot.answer_callback_query(call.id, "❌ केवल ऑनर क्विज़ चालू कर सकते हैं।", show_alert=True)
        return
        
    q_id = int(call.data.split('_')[1])
    quiz_data = get_quiz_by_id(q_id)
    
    if quiz_data:
        bot.answer_callback_query(call.id, "🚀 क्विज़ शुरू हो रहा है!")
        bot.edit_message_text(
            f"🚀 **क्विज़ शुरू हो रहा है!**\n📌 **{quiz_data['title']}**", 
            chat_id=call.message.chat.id, 
            message_id=call.message.message_id
        )
        launch_quiz(call.message.chat.id, quiz_data)
    else:
        bot.answer_callback_query(call.id, "❌ क्विज़ नहीं मिला।", show_alert=True)

def launch_quiz(chat_id, quiz_data):
    quizzes = quiz_data['questions']
    timer_val = quiz_data['timer']
    title = quiz_data['title']

    start_text = f"🏁 **क्विज़ प्रतियोगिता चालू!**\n\n📖 **विषय:** `{title}`\n📊 **कुल प्रश्न:** {len(quizzes)}\n⏱️ **समय:** {timer_val} सेकंड प्रति प्रश्न\n\n5 सेकंड में पहला प्रश्न आ रहा है..."
    intro_msg = bot.send_message(chat_id, start_text, parse_mode="Markdown")

    group_quiz_messages[chat_id] = [intro_msg.message_id]
    threading.Thread(target=run_quiz_competition, args=(chat_id, quizzes, timer_val, title)).start()

def run_quiz_competition(chat_id, quizzes, timer_val, title):
    leaderboards[chat_id] = {}
    total_q = len(quizzes)
    start_time = time.time()
    
    time.sleep(5)
    
    for idx, q in enumerate(quizzes, start=1):
        q_text = f"[{idx}/{total_q}] {q['question']}"
        try:
            poll_msg = bot.send_poll(
                chat_id=chat_id,
                question=q_text[:300],
                options=q['options'],
                type='quiz',
                correct_option_id=q['correct_id'],
                explanation="Jai Karah Bihari Sarkar 🙏",
                open_period=timer_val,
                is_anonymous=False
            )
            
            if chat_id in group_quiz_messages:
                group_quiz_messages[chat_id].append(poll_msg.message_id)
            
            poll_tracker[poll_msg.poll.id] = {
                'chat_id': chat_id,
                'correct_id': q['correct_id']
            }
            
            time.sleep(timer_val + 3)
        except Exception as e:
            print(f"Poll Error: {e}")
            time.sleep(2)

    total_time_spent = int(time.time() - start_time)
    send_final_leaderboard(chat_id, total_q, title, total_time_spent)

@bot.poll_answer_handler()
def handle_poll_answer(poll_answer):
    poll_id = poll_answer.poll_id
    if poll_id in poll_tracker:
        info = poll_tracker[poll_id]
        chat_id = info['chat_id']
        correct_id = info['correct_id']
        
        user = poll_answer.user
        user_id = user.id
        user_name = user.first_name + (f" {user.last_name}" if user.last_name else "")
        
        selected_option = poll_answer.option_ids[0] if poll_answer.option_ids else None
        
        if chat_id not in leaderboards:
            leaderboards[chat_id] = {}
            
        if user_id not in leaderboards[chat_id]:
            leaderboards[chat_id][user_id] = {
                'name': user_name, 
                'attempted': 0,
                'correct': 0, 
                'wrong': 0
            }
            
        leaderboards[chat_id][user_id]['attempted'] += 1
        if selected_option == correct_id:
            leaderboards[chat_id][user_id]['correct'] += 1
        else:
            leaderboards[chat_id][user_id]['wrong'] += 1


# ==========================================
# 7. WEASYPRINT HTML PDF GENERATION (Calligraphy Banner)
# ==========================================
def generate_pdf_report(group_name, title, total_q, scores, total_time_spent):
    sorted_scores = sorted(
        scores.values(), 
        key=lambda x: (x['correct'] * 1.0) - (x['wrong'] * 0.25), 
        reverse=True
    )
    
    total_candidates = len(sorted_scores) if len(sorted_scores) > 0 else 1
    highest_score_val = (sorted_scores[0]['correct'] * 1.0 - sorted_scores[0]['wrong'] * 0.25) if sorted_scores else 0.0
    max_marks_val = total_q * 1.0
    time_str = f"{total_time_spent // 60}m {total_time_spent % 60:02d}s"

    rows_html = ""
    for rank, p in enumerate(sorted_scores, start=1):
        attempted = p['attempted']
        correct = p['correct']
        wrong = p['wrong']
        neg_marks = wrong * 0.25
        total_score = (correct * 1.0) - neg_marks
        percentile = round(((total_candidates - rank + 1) / total_candidates) * 100, 2)
        name = p['name'][:25]

        if rank <= 3:
            tr_class = ' class="rank-top"'
            rank_html = f'<span class="rank-badge top-{rank}">{rank}</span>'
            name_class = ' class="text-left text-bold"'
            score_style = ' style="font-size: 9.5pt;"'
        else:
            tr_class = ''
            rank_html = f'<strong>{rank}</strong>'
            name_class = ' class="text-left"'
            score_style = ''

        rows_html += f"""
                <tr{tr_class}>
                    <td>{rank_html}</td>
                    <td{name_class}>{name}</td>
                    <td>{attempted}</td>
                    <td>{correct}</td>
                    <td>{wrong}</td>
                    <td class="text-danger">-{neg_marks:.2f}</td>
                    <td class="text-primary"{score_style}>{total_score:.2f}</td>
                    <td>{percentile:.2f} %</td>
                    <td>{time_str}</td>
                </tr>"""

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Consolidated Test Series Result Sheet</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Great+Vibes&display=swap');

        @page {{
            size: A4 landscape;
            margin: 10mm 12mm;
            background-color: #f8fafc;
        }}

        * {{
            box-sizing: border-box;
            font-family: 'DejaVu Sans', sans-serif;
        }}

        body {{
            margin: 0;
            padding: 0;
            color: #1e293b;
            font-size: 9pt;
            line-height: 1.4;
        }}

        .calligraphy-banner {{
            text-align: center;
            background: linear-gradient(90deg, #fff7ed, #ffedd5, #fef3c7, #ffedd5, #fff7ed);
            border: 2px solid #d97706;
            border-radius: 8px;
            padding: 8px 15px;
            margin-bottom: 12px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.06);
        }}

        .calligraphy-text {{
            font-family: 'Great Vibes', 'Times New Roman', cursive, serif;
            font-size: 32pt;
            font-weight: normal;
            color: #b45309;
            letter-spacing: 1.5px;
            margin: 0;
            display: inline-block;
            vertical-align: middle;
            line-height: 1.1;
        }}

        .decor-symbol {{
            color: #ea580c;
            font-size: 16pt;
            margin: 0 15px;
            vertical-align: middle;
        }}

        .header {{
            background: linear-gradient(135deg, #1e3a8a, #2563eb);
            color: #ffffff;
            padding: 12px 18px;
            border-radius: 6px;
            margin-bottom: 12px;
        }}

        .header table {{
            width: 100%;
            border-collapse: collapse;
        }}

        .header-title {{
            font-size: 15pt;
            font-weight: bold;
            margin: 0;
        }}

        .badge-info {{
            background: rgba(255, 255, 255, 0.2);
            padding: 5px 12px;
            border-radius: 15px;
            font-size: 8.5pt;
            display: inline-block;
        }}

        .summary-bar {{
            background: #ffffff;
            border: 1px solid #cbd5e1;
            border-radius: 6px;
            padding: 8px 14px;
            margin-bottom: 12px;
        }}

        .summary-bar table {{
            width: 100%;
            border-collapse: collapse;
        }}

        .summary-item {{
            font-size: 8.5pt;
            color: #475569;
        }}

        .summary-value {{
            font-size: 9.5pt;
            font-weight: bold;
            color: #0f172a;
        }}

        .table-container {{
            background: #ffffff;
            border-radius: 6px;
            border: 1px solid #cbd5e1;
            overflow: hidden;
        }}

        table.result-table {{
            width: 100%;
            border-collapse: collapse;
        }}

        table.result-table th {{
            background-color: #1e3a8a;
            color: #ffffff;
            font-size: 8.5pt;
            font-weight: bold;
            text-align: center;
            padding: 8px 6px;
            border: 1px solid #1d4ed8;
        }}

        table.result-table td {{
            padding: 7px 6px;
            border-bottom: 1px solid #e2e8f0;
            border-right: 1px solid #f1f5f9;
            font-size: 8.5pt;
            text-align: center;
        }}

        table.result-table tr:nth-child(even) {{
            background-color: #f8fafc;
        }}

        .rank-top {{
            font-weight: bold;
            color: #b45309;
            background-color: #fef3c7 !important;
        }}

        .rank-badge {{
            display: inline-block;
            width: 20px;
            height: 20px;
            line-height: 20px;
            border-radius: 50%;
            background-color: #2563eb;
            color: #ffffff;
            font-weight: bold;
            font-size: 8pt;
        }}

        .rank-badge.top-1 {{ background-color: #d97706; }}
        .rank-badge.top-2 {{ background-color: #64748b; }}
        .rank-badge.top-3 {{ background-color: #b45309; }}

        .text-left {{ text-align: left !important; padding-left: 12px !important; }}
        .text-bold {{ font-weight: bold; }}
        .text-danger {{ color: #b91c1c; }}
        .text-primary {{ color: #1d4ed8; font-weight: bold; }}
    </style>
</head>
<body>

    <div class="calligraphy-banner">
        <span class="decor-symbol">🚩</span>
        <span class="calligraphy-text">Jai Karah Bihari Sarkar</span>
        <span class="decor-symbol">🚩</span>
    </div>

    <div class="header">
        <table>
            <tr>
                <td>
                    <div class="header-title">CONSOLIDATED TEST RESULT & RANK LIST</div>
                </td>
                <td style="text-align: right;">
                    <div class="badge-info">Negative Marking: <strong>1/4 (0.25)</strong></div>
                </td>
            </tr>
        </table>
    </div>

    <div class="summary-bar">
        <table>
            <tr>
                <td width="25%">
                    <span class="summary-item">Test Name:</span> <br>
                    <span class="summary-value">{title}</span>
                </td>
                <td width="25%">
                    <span class="summary-item">Total Candidates:</span> <br>
                    <span class="summary-value">{len(scores)} Students</span>
                </td>
                <td width="25%">
                    <span class="summary-item">Max Marks:</span> <br>
                    <span class="summary-value">{max_marks_val:.1f} Marks ({total_q} Qs)</span>
                </td>
                <td width="25%">
                    <span class="summary-item">Highest Score:</span> <br>
                    <span class="summary-value" style="color: #15803d;">{highest_score_val:.2f} / {max_marks_val:.0f}</span>
                </td>
            </tr>
        </table>
    </div>

    <div class="table-container">
        <table class="result-table">
            <thead>
                <tr>
                    <th width="7%">Rank</th>
                    <th width="25%" class="text-left">Student Name</th>
                    <th width="9%">Attempted</th>
                    <th width="9%">Correct</th>
                    <th width="9%">Wrong</th>
                    <th width="11%">Neg. Marks (1/4)</th>
                    <th width="11%">Total Score</th>
                    <th width="9%">Percentile</th>
                    <th width="10%">Time Spent</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
    </div>

</body>
</html>"""

    buffer = io.BytesIO()
    HTML(string=html_content).write_pdf(target=buffer)
    buffer.seek(0)
    return buffer


# ==========================================
# 8. ऑटो-डिलीट (1 Sec) और परिणाम प्रबंधन
# ==========================================
def delete_group_quiz_messages(chat_id, message_ids):
    time.sleep(1)  # ⏱️ 1 सेकंड में प्रश्न और संदेश डिलीट होंगे
    for msg_id in message_ids:
        try:
            bot.delete_message(chat_id, msg_id)
        except Exception as e:
            print(f"Group Auto-Delete Error: {e}")

def send_final_leaderboard(chat_id, total_questions, title, total_time_spent):
    scores = leaderboards.get(chat_id, {})

    try:
        group_info = bot.get_chat(chat_id)
        pdf_file = generate_pdf_report(group_info.title, title, total_questions, scores, total_time_spent)
        
        # 🎯 PDF सीधे बिना किसी कैप्शन/टेक्स्ट मैसेज के जाएगी
        bot.send_document(
            chat_id=chat_id,
            document=('Quiz_Result.pdf', pdf_file, 'application/pdf')
        )
    except Exception as e:
        print(f"Group PDF Send Error: {e}")

    if chat_id in group_quiz_messages:
        msg_ids = group_quiz_messages.pop(chat_id)
        threading.Thread(target=delete_group_quiz_messages, args=(chat_id, msg_ids)).start()

    if chat_id in leaderboards:
        del leaderboards[chat_id]


# ==========================================
# 9. बैकग्राउंड बॉट पोलिंग थ्रेड
# ==========================================
def start_bot():
    try:
        bot.infinity_polling(skip_pending=True)
    except Exception as e:
        print(f"Bot Polling Error: {e}")

threading.Thread(target=start_bot, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)          
