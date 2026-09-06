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

# ReportLab libraries for exact PDF styling
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

# ==========================================
# 1. कॉन्फ़िगरेशन एवं सुरक्षा (Security)
# ==========================================
TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = 8183824919

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Cron-job.org के लिए 200 OK रिस्पॉन्स
@app.route('/')
def home():
    return "OK", 200


# ==========================================
# 2. SQLite डेटाबेस मैनेजमेंट (Persistent Storage)
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


# Global State Trackers
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
        bot.reply_to(
            message, 
            "🏁 **नमस्ते! मैं आपका Multi-Quiz Bot हूँ।**\n\n"
            "📌 **उपयोग कैसे करें:**\n"
            "• मुझे कोई भी `.docx` फ़ाइल यहाँ DM में भेजकर क्विज़ सेव करें।\n"
            "• `/myquizzes` - अपनी सभी सेव की हुई क्विज़ देखें।\n"
            "• `/deletequiz ID` - कोई क्विज़ डिलीट करें (उदा: `/deletequiz 1`)\n"
            "• ग्रुप में क्विज़ चलाने के लिए ग्रुप में `/startquiz` लिखें।"
        )
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
            
            bot.reply_to(
                message, 
                f"📥 **नई फ़ाइल प्राप्त हुई!**\n\n"
                f"📌 **शीर्षक:** `{title}`\n"
                f"📊 **कुल प्रश्न:** {len(quizzes)}\n\n"
                f"⚙️ **इस क्विज़ के लिए प्रति प्रश्न समय सीमा चुनें और सेव करें:**", 
                reply_markup=markup,
                parse_mode="Markdown"
            )
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
        
        bot.edit_message_text(
            f"✅ **क्विज़ सफलतापूर्वक सेव हो गया!**\n\n"
            f"🆔 **Quiz ID:** `{quiz_id}`\n"
            f"📌 **नाम:** `{data['title']}`\n"
            f"📊 **प्रश्न:** {len(data['quizzes'])}\n"
            f"⏱️ **समय:** {timer_val} सेकंड प्रति प्रश्न\n\n"
            f"💡 आप कभी भी इसे चलाने के लिए ग्रुप में `/startquiz` या `/startquiz {quiz_id}` लिखें।",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown"
        )
    else:
        bot.answer_callback_query(call.id, "⚠️ क्विज़ डेटा नहीं मिला।", show_alert=True)


# ==========================================
# 5. क्विज़ प्रबंधन कमांड्स (List & Delete)
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
        text += f"🔹 **ID {q['id']}:** {q['title']}\n"
        text += f"   └ 📊 {len(q['questions'])} प्रश्न | ⏱️ {q['timer']} सेकंड\n\n"
    
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
# 6. ग्रुप क्विज़ निष्पादन (Group Execution)
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

    intro_msg = bot.send_message(
        chat_id, 
        f"🏁 **क्विज़ प्रतियोगिता चालू!**\n\n"
        f"📖 **विषय:** `{title}`\n"
        f"📊 **कुल प्रश्न:** {len(quizzes)}\n"
        f"⏱️ **समय:** {timer_val} सेकंड प्रति प्रश्न\n\n"
        f"5 सेकंड में पहला प्रश्न आ रहा है...",
        parse_mode="Markdown"
    )

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
                explanation="करह बिहारी सरकार की जय 🙏",
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
# 7. PDF जेनरेशन
# ==========================================
def generate_pdf_report(group_name, title, total_q, scores, total_time_spent):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=A4, 
        rightMargin=15, 
        leftMargin=15, 
        topMargin=20, 
        bottomMargin=20
    )
    story = []
    
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'HeaderTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=15,
        alignment=1,
        textColor=colors.HexColor('#8B0000'),
        spaceAfter=4
    )
    
    sub_title_style = ParagraphStyle(
        'SubHeaderTitle',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=11,
        alignment=1,
        textColor=colors.HexColor('#0A2540'),
        spaceAfter=12
    )

    story.append(Paragraph("॥ करह बिहारी सरकार की जय ॥", title_style))
    story.append(Paragraph("CONSOLIDATED TEST RESULT & RANK LIST", sub_title_style))
    
    meta_data = [
        [f"Test Name:\n{title}", f"Total Candidates:\n{len(scores)} Students"],
        [f"Max Marks:\n{total_q * 2.0:.1f} Marks ({total_q} Qs)", "Negative Marking:\n1/4 (0.25)"]
    ]
    meta_table = Table(meta_data, colWidths=[280, 280])
    meta_table.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 8.5),
        ('TEXTCOLOR', (0,0), (-1,-1), colors.HexColor('#1A1A1A')),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 10))
    
    table_data = [[
        'Rank', 'Roll No / ID', 'Student Name', 'Attempted', 
        'Correct', 'Wrong', 'Neg. Marks (1/4)', 'Total Score', 'Percentile', 'Time Spent'
    ]]
    
    sorted_scores = sorted(
        scores.values(), 
        key=lambda x: (x['correct'] * 2.0) - (x['wrong'] * 0.5), 
        reverse=True
    )
    
    total_candidates = len(sorted_scores) if len(sorted_scores) > 0 else 1
    time_str = f"{total_time_spent // 60}m {total_time_spent % 60:02d}s"
    
    for rank, p in enumerate(sorted_scores, start=1):
        attempted = p['attempted']
        correct = p['correct']
        wrong = p['wrong']
        neg_marks = wrong * 0.5
        total_score = (correct * 2.0) - neg_marks
        percentile = round(((total_candidates - rank + 1) / total_candidates) * 100, 2)
        
        table_data.append([
            str(rank),
            f"STU-2026-{rank:03d}",
            p['name'][:18],
            str(attempted),
            str(correct),
            str(wrong),
            f"-{neg_marks:.2f}",
            f"{total_score:.2f}",
            f"{percentile:.2f}%",
            time_str
        ])
        
    result_table = Table(table_data, colWidths=[32, 72, 95, 52, 42, 40, 70, 58, 55, 52])
    result_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#002B49')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 7.5),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#D3D3D3')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F8F9FA')])
    ]))
    
    story.append(result_table)
    doc.build(story)
    
    buffer.seek(0)
    return buffer


# ==========================================
# 8. ऑटो-डिलीट और रिजल्ट मैनेजमेंट
# ==========================================
def delete_group_quiz_messages(chat_id, message_ids):
    time.sleep(60)
    for msg_id in message_ids:
        try:
            bot.delete_message(chat_id, msg_id)
        except Exception as e:
            print(f"Group Auto-Delete Error: {e}")

def send_final_leaderboard(chat_id, total_questions, title, total_time_spent):
    scores = leaderboards.get(chat_id, {})
    
    try:
        bot.send_message(chat_id, "करह बिहारी सरकार की जय")
    except Exception as e:
        print(f"Group Message Error: {e}")

    try:
        group_info = bot.get_chat(chat_id)
        pdf_file = generate_pdf_report(group_info.title, title, total_questions, scores, total_time_spent)
        
        bot.send_document(
            chat_id=OWNER_ID,
            document=('Quiz_Result.pdf', pdf_file, 'application/pdf'),
            caption=f"📊 **क्विज़ परिणाम रिपोर्ट**\n👥 **ग्रुप:*
