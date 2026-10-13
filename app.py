import os
import json
import base64
import re
import sqlite3
from functools import wraps
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, request, jsonify, Response, stream_with_context, redirect, url_for, session
from dotenv import load_dotenv  # type: ignore
load_dotenv()
from werkzeug.security import generate_password_hash, check_password_hash
# 🔑 الاستدعاء الحديث لمكتبة الذكاء الاصطناعي
from google import genai
from contextlib import contextmanager
import sqlite3

@contextmanager
def get_db():
    """Context Manager آمن ومحترف لإدارة اتصال وقاعدة بيانات SQLite"""
    conn = sqlite3.connect(DATABASE_FILE, timeout=10.0)
    try:
        yield conn
        conn.commit()  # حفظ التغييرات تلقائياً إذا سارت الأمور تماماً
    except Exception as e:
        conn.rollback()  # التراجع عن التغييرات إذا حدث أي خطأ
        raise e
    finally:
        conn.close()   # الإغلاق التام للاتصال وتحرير الملف 100% مهما كانت الظروف
        
app = Flask(__name__)
app.json.ensure_ascii = False

# مفتاح تشفير الجلسات (Sessions)
app.secret_key = os.environ.get("SESSION_SECRET_KEY", "ayman_super_secret_key_2026")

# ملفات حفظ البيانات وقواعد البيانات
ADMIN_CONFIG_FILE = 'admin_config.json'
RULES_FILE = 'rules.json'
ADMINS_FILE = 'admins.json'
INSTRUCTIONS_FILE = 'ai_instructions.txt'
DATABASE_FILE = 'database.db'

# 🔑 تهيئة عميل Gemini بشكل آمن
GENAI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GENAI_API_KEY:
    raise ValueError("⚠️ تنبيه أمني: لم يتم تعيين مفتاح GEMINI_API_KEY في متغيرات البيئة الخاصة بالسيرفر!")

client = genai.Client(api_key=GENAI_API_KEY)

# ==================== إدارة قواعد البيانات والملفات ====================

def init_db():
    """تجهيز جدول المحادثات والرسائل في SQLite تلقائياً"""
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chats (
                id TEXT PRIMARY KEY,
                device_id TEXT,
                title TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT,
                sender TEXT,
                text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ خطأ في تهيئة قاعدة البيانات: {e}")

init_db()

import threading

# ==================== 🧠 دالة توليد عنوان ذكي من كامل المحادثة ====================

def async_generate_title(chat_id):
    """قراءة كامل سجل المحادثة واستنباط عنوان ذكي ومختصر لموضوعها الرئيسي"""
    try:
        conn = sqlite3.connect(DATABASE_FILE, timeout=10.0)
        cursor = conn.cursor()
        
        cursor.execute("SELECT sender, text FROM messages WHERE chat_id = ? ORDER BY id ASC", (chat_id,))
        messages = cursor.fetchall()
        conn.close()

        if not messages:
            return

        chat_transcript = ""
        for sender, text in messages:
            role = "المستخدم" if sender == 'user' else "المساعد"
            chat_transcript += f"{role}: {text}\n"

        prompt = (
            "اقرأ نص المحادثة التالي بتمعن، واقترح عنواناً موجزاً وواضحاً جداً (من 2 إلى 4 كلمات فقط) "
            "يلخص الموضوع الرئيسي أو المشكلة التقنية التي يدور حولها النقاش.\n\n"
            "⚠️ شرط مهم جداً: إذا كانت المحادثة حتى الآن تحتوي فقط على تحيات أو مجاملات بسيطة "
            "(مثل: سلام، أهلاً، كيفك، الحمدلله) ولم يتم طرح أي سؤال أو طلب تقني حقيقي بعد، "
            "اكتب فقط الكلمتين التاليين بدون أي زيادة: محادثة جديدة\n\n"
            "لا تضف أي مقدمات أو علامات تنصيص اطلاقاً.\n\n"
            f"نص المحادثة:\n{chat_transcript}"
        )

        res = client.models.generate_content(
            model="gemini-flash-lite-latest",
            contents=prompt,
            config={"max_output_tokens": 15, "temperature": 0.2}
        )

        if res and res.text:
            smart_title = res.text.strip().replace('"', '').replace("'", '').replace('«', '').replace('»', '')
            
            conn = sqlite3.connect(DATABASE_FILE, timeout=10.0)
            cursor = conn.cursor()
            cursor.execute("UPDATE chats SET title = ? WHERE id = ?", (smart_title, chat_id))
            conn.commit()
            conn.close()

    except Exception as e:
        print(f"⚠️ تعذر توليد عنوان ذكي للمحادثة: {e}")


def save_chat_to_db(chat_id, device_id, user_text, ai_text):
    if not chat_id or not device_id:
        return
    try:
        conn = sqlite3.connect(DATABASE_FILE, timeout=10.0)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id FROM chats WHERE id = ?", (chat_id,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO chats (id, device_id, title) VALUES (?, ?, ?)", (chat_id, device_id, "محادثة جديدة"))
            conn.commit()

        if user_text:
            cursor.execute("INSERT INTO messages (chat_id, sender, text) VALUES (?, ?, ?)", (chat_id, 'user', user_text))
        
        if ai_text:
            cursor.execute("INSERT INTO messages (chat_id, sender, text) VALUES (?, ?, ?)", (chat_id, 'model', ai_text))
            
        conn.commit()
        conn.close()

        threading.Thread(target=async_generate_title, args=(chat_id,), daemon=True).start()

    except Exception as e:
        print(f"⚠️ خطأ أثناء حفظ المحادثة في قاعدة البيانات: {e}")
        
def load_admin_credentials():
    if os.path.exists(ADMIN_CONFIG_FILE):
        try:
            with open(ADMIN_CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    default_password_hash = generate_password_hash("1111")
    default_creds = {"username": "1111", "password": default_password_hash}
    save_admin_credentials("1111", "1111")
    return default_creds

def save_admin_credentials(username, password):
    hashed_password = generate_password_hash(password)
    with open(ADMIN_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump({"username": username, "password": hashed_password}, f, ensure_ascii=False, indent=2)

def load_admins():
    if not os.path.exists(ADMINS_FILE):
        default_admins = [{
            "username": "admin", 
            "password": generate_password_hash("1111"), 
            "status": "active",
            "permissions": {"drivers": True, "tools": True, "train": True, "data": True, "is_master": True}
        }]
        save_admins(default_admins)
        return default_admins
    try:
        with open(ADMINS_FILE, 'r', encoding='utf-8') as f:
            admins = json.load(f)
            for a in admins:
                if 'permissions' not in a:
                    a['permissions'] = {"drivers": True, "tools": True, "train": True, "data": True, "is_master": False}
                elif 'is_master' not in a['permissions']:
                    a['permissions']['is_master'] = False
            return admins
    except Exception:
        return []

def save_admins(admins):
    with open(ADMINS_FILE, 'w', encoding='utf-8') as f:
        json.dump(admins, f, ensure_ascii=False, indent=2)

def admin_required(f_or_perm=None):
    """مزخرف ذكي ومطور يتحقق من الجلسة والصلاحيات الدقيقة للحسابات الفرعية والرئيسية"""
    if callable(f_or_perm):
        f = f_or_perm
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get('is_admin'):
                return jsonify({"status": "error", "message": "غير مصرح لك بالوصول!"}), 403
            return f(*args, **kwargs)
        return decorated_function
    else:
        perm = f_or_perm
        def decorator(f):
            @wraps(f)
            def decorated_function(*args, **kwargs):
                if not session.get('is_admin'):
                    return jsonify({"status": "error", "message": "غير مصرح لك بالوصول!"}), 403
                
                # إذا كان الآدمن الرئيسي المطلق، يُسمح له بكل شيء
                if session.get('is_master'):
                    return f(*args, **kwargs)
                
                username = session.get('username')
                admin_creds = load_admin_credentials()
                if username == admin_creds.get('username'):
                    return f(*args, **kwargs)
                
                # التحقق من الصلاحية المطلوبة للآدمن الفرعي
                if perm:
                    admins = load_admins()
                    found_perm = False
                    for a in admins:
                        if a.get('username') == username:
                            if a.get('status') == 'suspended':
                                return jsonify({"status": "error", "message": "هذا الحساب موقوف حالياً!"}), 403
                            perms = a.get('permissions', {})
                            if perms.get(perm, False) or perms.get('is_master', False):
                                found_perm = True
                            break
                    if not found_perm:
                        return jsonify({"status": "error", "message": "ليس لديك صلاحية الوصول لهذه الوظيفة!"}), 403
                
                return f(*args, **kwargs)
            return decorated_function
        return decorator

def load_rules():
    if os.path.exists(RULES_FILE):
        try:
            with open(RULES_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if 'explanations' not in data:
                    data['explanations'] = []
                return data
        except Exception:
            pass
    return {"drivers": [], "tools": [], "knowledge": [], "explanations": []}

def save_rules(data):
    with open(RULES_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_custom_instructions():
    if os.path.exists(INSTRUCTIONS_FILE):
        try:
            with open(INSTRUCTIONS_FILE, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception:
            pass
    return ""

def save_custom_instructions(text):
    with open(INSTRUCTIONS_FILE, 'w', encoding='utf-8') as f:
        f.write(text)


# ==================== ⚡ محرك المطابقة المحلية السريعة للتلقين ====================

def check_local_knowledge(user_message):
    if not user_message:
        return None
        
    rules = load_rules()
    knowledge_list = rules.get('knowledge', [])
    
    clean_input = user_message.strip().lower()
    clean_input_no_punct = re.sub(r'[^\w\s]', '', clean_input)

    for item in knowledge_list:
        topic = item.get('topic', '').strip().lower()
        response = item.get('response', '').strip()
        if not topic or not response:
            continue
            
        topic_no_punct = re.sub(r'[^\w\s]', '', topic)
        if clean_input == topic or (clean_input_no_punct and clean_input_no_punct == topic_no_punct):
            return response

    for item in knowledge_list:
        topic = item.get('topic', '').strip().lower()
        response = item.get('response', '').strip()
        if not topic or not response:
            continue
            
        topic_no_punct = re.sub(r'[^\w\s]', '', topic)
        if len(topic_no_punct) > 2 and topic_no_punct in clean_input_no_punct:
            return response

    return None


SYSTEM_PROMPT_TEMPLATE = """
أنت تايجر نظام خبير ومستشار احترافي متخصص في برمجة، وصيانة هواتف الأندرويد.
تم تطويري بواسطة المطور أيمن بن فارس.

[المعرفة والتخصص]:
1. لدي معرفة كاملة وشاملة بجميع أدوات، طرق تفعيل الشريحة (4G/CDMA/VOLTE)، تعريب الأجهزة، وفك الحمايات لجميع المعالجات.
2. تخصصك هو الأندرويد فقط. إذا سئلت عن أي مجال آخر خارجي، ارفض فوراً بأسلوب محدد وقصير.

[السيناريوهات والشروحات التشغيلية المضافة من المطور]:
{explanations_text}

[القواعد والتعليمات الإضافية]:
{custom_instructions}

[قاعدة المعرفة والروابط والردود المعتمدة (Q&A)]:
{knowledge_base}
"""


# ==================== مسارات الواجهة والحسابات ====================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.is_json:
            data = request.json or {}
            username = data.get('username', '').strip()
            password = data.get('password', '').strip()
        else:
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '').strip()

        admin_creds = load_admin_credentials()
        admins_list = load_admins()

        stored_main_pwd = admin_creds.get('password', '')
        is_valid = (username == admin_creds.get('username') and (
            check_password_hash(stored_main_pwd, password) if stored_main_pwd.startswith(('pbkdf2:', 'scrypt:')) else (stored_main_pwd == password)
        ))

        matched_admin_user = None
        if not is_valid:
            for admin_user in admins_list:
                if admin_user.get('username') == username:
                    stored_pwd = admin_user.get('password', '')
                    matched = check_password_hash(stored_pwd, password) if stored_pwd.startswith(('pbkdf2:', 'scrypt:')) else (stored_pwd == password)
                    
                    if matched:
                        if admin_user.get('status') == 'suspended':
                            error_msg = "هذا الحساب موقوف حالياً!"
                            return jsonify({"status": "error", "message": error_msg}) if request.is_json else render_template('login.html', error=error_msg)
                        is_valid = True
                        matched_admin_user = admin_user
                        break

        if is_valid:
            session['is_admin'] = True
            session['username'] = username
            if username == admin_creds.get('username'):
                session['is_master'] = True
            else:
                session['is_master'] = matched_admin_user.get('permissions', {}).get('is_master', False) if matched_admin_user else False
            return jsonify({"status": "success", "redirect": "/admin"}) if request.is_json else redirect('/admin')
        else:
            error_msg = "اسم المستخدم أو كلمة المرور غير صحيحة!"
            return jsonify({"status": "error", "message": error_msg}), 401 if request.is_json else render_template('login.html', error=error_msg)

    if session.get('is_admin'):
        return redirect('/admin')
        
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('is_admin', None)
    session.pop('username', None)
    session.pop('is_master', None)
    return redirect('/')

@app.route('/admin')
def admin():
    if not session.get('is_admin'):
        return redirect('/login')
        
    rules = load_rules()
    
    username = session.get('username')
    admin_creds = load_admin_credentials()
    
    # الصلاحيات الافتراضية للآدمن الرئيسي المطلق
    permissions = {"drivers": True, "tools": True, "train": True, "data": True, "is_master": True}
    
    if username != admin_creds.get('username'):
        admins = load_admins()
        for a in admins:
            if a.get('username') == username:
                permissions = a.get('permissions', {"drivers": True, "tools": True, "train": True, "data": True, "is_master": False})
                break
                
    # أضفنا هنا username=username لكي يقرأه القالب فوراً
    return render_template('admin.html', rules=rules, permissions=permissions, username=username)
# ==================== مسارات سجل المحادثات (SQLite) ====================

@app.route('/api/chats', methods=['GET'])
def get_device_chats():
    device_id = request.args.get('device_id')
    if not device_id:
        return jsonify([])
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, created_at FROM chats WHERE device_id = ? ORDER BY id DESC", (device_id,))
        rows = cursor.fetchall()
        conn.close()
        
        response = jsonify([{"id": r[0], "title": r[1], "created_at": r[2]} for r in rows])
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    except Exception:
        return jsonify([])
    
@app.route('/api/chat/<chat_id>', methods=['GET'])
def get_chat_messages(chat_id):
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT sender, text FROM messages WHERE chat_id = ? ORDER BY id ASC", (chat_id,))
        rows = cursor.fetchall()
        conn.close()
        return jsonify([{"sender": r[0], "text": r[1]} for r in rows])
    except Exception:
        return jsonify([])

@app.route('/api/chat/<chat_id>', methods=['DELETE'])
def delete_specific_chat(chat_id):
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        cursor.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        conn.commit()
        conn.close()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/chats', methods=['DELETE'])
def delete_all_device_chats():
    device_id = request.args.get('device_id')
    if not device_id:
        return jsonify({"status": "error", "message": "device_id مطلوب"}), 400
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id FROM chats WHERE device_id = ?", (device_id,))
        rows = cursor.fetchall()
        chat_ids = [r[0] for r in rows]
        
        if chat_ids:
            placeholders = ','.join(['?'] * len(chat_ids))
            cursor.execute(f"DELETE FROM messages WHERE chat_id IN ({placeholders})", chat_ids)
            cursor.execute(f"DELETE FROM chats WHERE id IN ({placeholders})", chat_ids)
            conn.commit()
            
        conn.close()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.json or {}
    user_message = data.get('message', '').strip()
    mode = data.get('mode', 'fast')
    chat_id = data.get('chat_id')
    device_id = data.get('device_id')

    if not user_message:
        return jsonify({"reply": "يرجى كتابة رسالة أولاً."})

    local_reply = check_local_knowledge(user_message)
    if local_reply:
        save_chat_to_db(chat_id, device_id, user_message, local_reply)
        return jsonify({"reply": local_reply, "source": "local_knowledge"})

    rules = load_rules()
    custom_instructions = get_custom_instructions() 

    explanations_text = ""
    for exp in rules.get('explanations', []):
        explanations_text += f"\n--- سيناريو: {exp.get('title')} ---\n{exp.get('content')}\n"

    system_instruction = SYSTEM_PROMPT_TEMPLATE.format(
        explanations_text=explanations_text,
        custom_instructions=custom_instructions,
        knowledge_base=json.dumps(rules.get('knowledge', []), ensure_ascii=False)
    )

    if mode == 'fast':
        candidate_models = [
            "gemini-flash-lite-latest",
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-3.6-flash"
        ]
        system_instruction += "\n[توجيه صارم - إجابة سريعة]: أجب فوراً وبشكل مباشر ومختصر جداً في أسطر معدودة دون مقدمات."
        config = {
            "system_instruction": system_instruction,
            "max_output_tokens": 400,
            "temperature": 0.2
        }

        reply_text = None
        for model_name in candidate_models:
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=user_message,
                    config=config
                )
                if response and response.text:
                    reply_text = response.text
                    break
            except Exception as e:
                print(f"⚠️ تعذر استخدام {model_name}: {e}")
                continue

    else:
        candidate_models = [
            "gemini-3.8-flash",
            "gemini-3.7-flash",
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "gemini-flash-latest"
        ]
        system_instruction += "\n[توجيه خبير - بحث متقدم]: قدم تحليلاً تقنياً مفصلاً وشاملاً، واذكر كافة الأوامر والخطوات بدقة بدون أي اختصار."
        config = {
            "system_instruction": system_instruction,
            "temperature": 0.6
        }

        def fetch_response(model_name):
            try:
                res = client.models.generate_content(
                    model=model_name,
                    contents=user_message,
                    config=config
                )
                if res and res.text:
                    return res.text
            except Exception as e:
                print(f"⚠️ فشل النموذج {model_name}: {e}")
            return None

        reply_text = None
        with ThreadPoolExecutor(max_workers=len(candidate_models)) as executor:
            future_to_model = {executor.submit(fetch_response, m): m for m in candidate_models}
            for future in as_completed(future_to_model):
                result = future.result()
                if result:
                    reply_text = result
                    break

    if not reply_text:
        reply_text = "عذراً، تعذر جلب الرد حالياً. يرجى التأكد من الاتصال أو المحاولة لاحقاً."

    save_chat_to_db(chat_id, device_id, user_message, reply_text)

    return jsonify({"reply": reply_text})


@app.route('/api/chat/stream', methods=['POST'])
def chat_stream():
    data = request.json or {}
    user_message = data.get('message', '').strip()
    mode = data.get('mode', 'fast')
    chat_id = data.get('chat_id')
    device_id = data.get('device_id')
    raw_history = data.get('history', [])
    raw_files = data.get('files', [])

    if not user_message and not raw_files:
        return jsonify({"error": "يرجى كتابة رسالة أو إرفاق ملف أولاً."}), 400

    if user_message and not raw_files:
        local_reply = check_local_knowledge(user_message)
        if local_reply:
            save_chat_to_db(chat_id, device_id, user_message, local_reply)
            def generate_local():
                yield f"data: {json.dumps({'text': local_reply}, ensure_ascii=False)}\n\n"

            return Response(
                stream_with_context(generate_local()), 
                mimetype='text/event-stream',
                headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'}
            )

    greetings_pattern = r'^(السلام عليكم|سلام|مرحباً|هلا|أهلاً|كيف حالك|كيفك|من أنت|صباح الخير|مساء الخير)[\s!?،]*$'
    if mode == 'advanced' and user_message and re.match(greetings_pattern, user_message, re.IGNORECASE) and not raw_files:
        mode = 'fast'

    rules = load_rules()
    custom_instructions = get_custom_instructions()
    
    explanations_text = ""
    for exp in rules.get('explanations', []):
        explanations_text += f"\n--- سيناريو: {exp.get('title')} ---\n{exp.get('content')}\n"

    system_instruction = SYSTEM_PROMPT_TEMPLATE.format(
        explanations_text=explanations_text,
        custom_instructions=custom_instructions,
        knowledge_base=json.dumps(rules.get('knowledge', []), ensure_ascii=False)
    )

    if mode == 'fast':
        candidate_models = [
            "gemini-flash-lite-latest",
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-3.6-flash",
            "gemini-3.5-flash"
        ]
        system_instruction += "\n[توجيه صارم - إجابة سريعة]: أجب فوراً وبشكل مباشر ومختصر جداً."
        config = {
            "system_instruction": system_instruction,
            "max_output_tokens": 500,
            "temperature": 0.2
        }
    else:
        candidate_models = [
            "gemini-flash-latest",
            "gemini-3.5-flash",
            "gemini-3.6-flash",
            "gemini-3.7-flash",
            "gemini-3.8-flash"
        ]
        system_instruction += "\n[توجيه خبير - بحث متقدم]: قدم تحليلاً تقنياً مفصلاً وشاملاً للأكواد والملفات المعقدة."
        config = {
            "system_instruction": system_instruction,
            "max_output_tokens": 2048,
            "temperature": 0.5
        }

    raw_db_messages = []
    if chat_id:
        try:
            conn = sqlite3.connect(DATABASE_FILE, timeout=10.0)
            cursor = conn.cursor()
            cursor.execute("SELECT sender, text FROM messages WHERE chat_id = ? ORDER BY id ASC", (chat_id,))
            raw_db_messages = cursor.fetchall()
            conn.close()
        except Exception as e:
            print(f"⚠️ خطأ في قراءة السجل من قاعدة البيانات: {e}")

    history_items = []
    if raw_db_messages:
        for sender, text in raw_db_messages:
            role = "user" if sender == 'user' else "model"
            if text and text.strip():
                history_items.append({"role": role, "text": text.strip()})
    elif raw_history:
        for turn in raw_history:
            role = "user" if turn.get("role") == "user" else "model"
            text = turn.get("content", "").strip()
            if text:
                history_items.append({"role": role, "text": text})

    contents = []
    last_role = None
    for item in history_items:
        role = item["role"]
        text = item["text"]
        
        if not contents and role != "user":
            continue
        
        if role != last_role:
            contents.append({"role": role, "parts": [{"text": text}]})
            last_role = role

    if contents and contents[-1]["role"] == "user":
        contents.pop()

    current_user_parts = []
    if user_message:
        current_user_parts.append({"text": user_message})

    for f in raw_files:
        try:
            mime_type = f.get('mime_type', '')
            base64_str = f.get('data', '')
            file_bytes = base64.b64decode(base64_str)
            current_user_parts.append({"inline_data": {"mime_type": mime_type, "data": file_bytes}})
        except Exception as e:
            print(f"⚠ خطأ في معالجة الملف المرفق: {e}")

    contents.append({"role": "user", "parts": current_user_parts})

    def generate():
        full_ai_response = ""
        success = False
        for model_name in candidate_models:
            try:
                response = client.models.generate_content_stream(
                    model=model_name,
                    contents=contents,
                    config=config
                )
                
                for chunk in response:
                    if chunk.text:
                        full_ai_response += chunk.text
                        yield f"data: {json.dumps({'text': chunk.text}, ensure_ascii=False)}\n\n"
                
                success = True
                break
            except Exception as e:
                print(f"⚠️ فشل النموذج {model_name}: {e}")
                continue

        if success:
            save_chat_to_db(chat_id, device_id, user_message, full_ai_response)
        else:
            yield f"data: {json.dumps({'text': 'عذراً، تعذر معالجة الطلب حالياً.'}, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()), 
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'}
    )

@app.route('/api/admin/drivers/add', methods=['POST'])
@admin_required('drivers')
def add_driver():
    data = request.json or {}
    name = data.get('name', '').strip()
    url = data.get('url', '').strip()
    
    if not name or not url:
        return jsonify({"status": "error", "message": "بيانات ناقصة"}), 400

    rules = load_rules()
    rules.setdefault('drivers', []).append({"name": name, "url": url})
    save_rules(rules)
    return jsonify({"status": "success", "message": "تمت إضافة التعريف بنجاح!"})

@app.route('/api/admin/tools/add', methods=['POST'])
@admin_required('tools')
def add_tool():
    data = request.json or {}
    name = data.get('name', '').strip()
    url = data.get('url', '').strip()

    if not name or not url:
        return jsonify({"status": "error", "message": "بيانات ناقصة"}), 400

    rules = load_rules()
    rules.setdefault('tools', []).append({"name": name, "url": url})
    save_rules(rules)
    return jsonify({"status": "success", "message": "تمت إضافة الأداة بنجاح!"})

@app.route('/api/admin/train', methods=['POST'])
@admin_required('train')
def train_ai():
    data = request.json or {}
    k_type = data.get('type', 'qa')
    
    rules = load_rules()
    
    if k_type == 'explanation':
        title = data.get('title', '').strip()
        content = data.get('content', '').strip()
        if not title or not content:
            return jsonify({"status": "error", "message": "الرجاء تعبئة عنوان الشرح والمحتوى"}), 400
        rules.setdefault('explanations', []).append({"title": title, "content": content})
        save_rules(rules)
        return jsonify({"status": "success", "message": "تم حفظ الشرح والسيناريو بنجاح!"})
    else:
        topic = data.get('topic', '').strip()
        response = data.get('response', '').strip()
        if not topic or not response:
            return jsonify({"status": "error", "message": "الرجاء تعبئة موضوع التعليم والرد المتوقع"}), 400
        rules.setdefault('knowledge', []).append({"topic": topic, "response": response})
        save_rules(rules)
        return jsonify({"status": "success", "message": "تم إضافة المعلومة وتدريب النظام بنجاح!"})

@app.route('/api/admin/<category>/delete/<int:index>', methods=['DELETE'])
@admin_required('data')
def delete_item(category, index):
    if category not in ['drivers', 'tools', 'knowledge', 'explanations']:
        return jsonify({"status": "error", "message": "الفئة غير صالحة"}), 400
    rules = load_rules()
    items = rules.get(category, [])
    if 0 <= index < len(items):
        deleted = items.pop(index)
        save_rules(rules)
        return jsonify({"status": "success", "message": "تم الحذف بنجاح!", "deleted": deleted})
    return jsonify({"status": "error", "message": "العنصر غير موجود"}), 404

@app.route('/api/admin/<category>/update/<int:index>', methods=['PUT'])
@admin_required('data')
def update_item(category, index):
    if category not in ['drivers', 'tools', 'knowledge', 'explanations']:
        return jsonify({"status": "error", "message": "الفئة غير صالحة"}), 400
    data = request.json or {}
    rules = load_rules()
    items = rules.get(category, [])
    if 0 <= index < len(items):
        if category in ['drivers', 'tools']:
            name = data.get('name', '').strip()
            url = data.get('url', '').strip()
            items[index] = {"name": name, "url": url}
        elif category == 'knowledge':
            topic = data.get('topic', '').strip()
            response = data.get('response', '').strip()
            items[index] = {"topic": topic, "response": response}
        elif category == 'explanations':
            title = data.get('title', '').strip()
            content = data.get('content', '').strip()
            items[index] = {"title": title, "content": content}
        save_rules(rules)
        return jsonify({"status": "success", "message": "تم التعديل بنجاح!"})
    return jsonify({"status": "error", "message": "العنصر غير موجود"}), 404

@app.route('/api/admin/data', methods=['GET'])
@admin_required('data')
def get_admin_data():
    rules = load_rules()
    return jsonify(rules)

@app.route('/api/admin/instructions', methods=['GET', 'POST'])
@admin_required('train')
def handle_instructions():
    if request.method == 'POST':
        data = request.json or {}
        text = data.get('instructions', '').strip()
        save_custom_instructions(text)
        return jsonify({"status": "success", "message": "تم حفظ التعليمات بنجاح!"})
    else:
        return jsonify({"instructions": get_custom_instructions()})

@app.route('/api/admin/change-credentials', methods=['POST'])
@admin_required
def change_admin_credentials():
    data = request.json or {}
    new_username = data.get('new_username', '').strip()
    old_password = data.get('old_password', '').strip()
    new_password = data.get('new_password', '').strip()

    if not new_username or not old_password or not new_password:
        return jsonify({"status": "error", "message": "يرجى تعبئة جميع الحقول!"}), 400

    current_creds = load_admin_credentials()
    stored_hash = current_creds.get('password', '')

    is_old_valid = check_password_hash(stored_hash, old_password) if stored_hash.startswith(('pbkdf2:', 'scrypt:')) else (stored_hash == old_password)

    if not is_old_valid:
        return jsonify({"status": "error", "message": "كلمة المرور القديمة غير صحيحة!"}), 400

    save_admin_credentials(new_username, new_password)
    return jsonify({"status": "success", "message": "تم تغيير اسم المستخدم وكلمة المرور بنجاح وتشفيرها بشكل آمن!"})


# ==================== 🛠️ مسارات إدارة حسابات الآدمن والصلاحيات الجديدة ====================

@app.route('/api/admin/verify-master', methods=['POST'])
@admin_required('is_master')
def verify_master_admin():
    """التحقق من اسم المستخدم وكلمة المرور للوصول لإدارة الحسابات والصلاحيات"""
    data = request.json or {}
    input_username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    
    # 1. التحقق من بيانات الآدمن الرئيسي المطلق
    admin_creds = load_admin_credentials()
    main_username = admin_creds.get('username', '')
    stored_pwd = admin_creds.get('password', '')
    
    is_main_valid = (input_username == main_username) and (
        check_password_hash(stored_pwd, password) if stored_pwd.startswith(('pbkdf2:', 'scrypt:')) else (stored_pwd == password)
    )
    
    if is_main_valid:
        return jsonify({"status": "success", "message": "تم التحقق بنجاح"})
    
    # 2. التحقق من الآدمن الفرعي إذا كان يمتلك صلاحية آدمن رئيسي (is_master) ويطابق اسم المستخدم المدخل
    admins = load_admins()
    for a in admins:
        if a.get('username') == input_username:
            if a.get('permissions', {}).get('is_master', False):
                stored_pwd = a.get('password', '')
                matched = check_password_hash(stored_pwd, password) if stored_pwd.startswith(('pbkdf2:', 'scrypt:')) else (stored_pwd == password)
                if matched:
                    return jsonify({"status": "success", "message": "تم التحقق بنجاح"})
            break
    
    return jsonify({"status": "error", "message": "اسم المستخدم أو كلمة المرور غير صحيحة، أو ليس لديك صلاحية آدمن رئيسي!"}), 401
@app.route('/api/admin/accounts', methods=['GET'])
@admin_required('is_master')
def get_admin_accounts():
    """جلب قائمة كافة حسابات الآدمن الفرعية"""
    admins = load_admins()
    safe_admins = []
    for a in admins:
        safe_admins.append({
            "username": a.get("username"),
            "status": a.get("status", "active"),
            "permissions": a.get("permissions", {"drivers": True, "tools": True, "train": True, "data": True, "is_master": False})
        })
    return jsonify(safe_admins)

@app.route('/api/admin/accounts/add', methods=['POST'])
@admin_required('is_master')
def add_admin_account():
    """إضافة حساب آدمن جديد مع تحديد الصلاحيات الشاملة ومن ضمنها is_master"""
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    permissions = data.get('permissions', {"drivers": True, "tools": True, "train": True, "data": True, "is_master": False})

    if not username or not password:
        return jsonify({"status": "error", "message": "يرجى إدخال اسم المستخدم وكلمة المرور"}), 400

    admins = load_admins()
    for a in admins:
        if a.get('username') == username:
            return jsonify({"status": "error", "message": "اسم المستخدم موجود مسبقاً!"}), 400

    new_admin = {
        "username": username,
        "password": generate_password_hash(password),
        "status": "active",
        "permissions": permissions
    }
    admins.append(new_admin)
    save_admins(admins)
    return jsonify({"status": "success", "message": "تم إنشاء حساب الآدمن بنجاح!"})

@app.route('/api/admin/accounts/<username>', methods=['PUT'])
@admin_required('is_master')
def update_admin_account(username):
    """تعديل حالة الحساب (نشط/موقوف) أو الصلاحيات ومن ضمنها is_master"""
    data = request.json or {}
    admins = load_admins()
    found = False

    for a in admins:
        if a.get('username') == username:
            if 'status' in data:
                a['status'] = data['status']
            if 'permissions' in data:
                a['permissions'] = data['permissions']
            if 'password' in data and data['password'].strip():
                a['password'] = generate_password_hash(data['password'].strip())
            found = True
            break

    if not found:
        return jsonify({"status": "error", "message": "الحساب غير موجود"}), 404

    save_admins(admins)
    return jsonify({"status": "success", "message": "تم تحديث حساب الآدمن بنجاح!"})

@app.route('/api/admin/accounts/<username>', methods=['DELETE'])
@admin_required('is_master')
def delete_admin_account(username):
    """حذف حساب آدمن"""
    admins = load_admins()
    new_admins = [a for a in admins if a.get('username') != username]

    if len(new_admins) == len(admins):
        return jsonify({"status": "error", "message": "الحساب غير موجود"}), 404

    save_admins(new_admins)
    return jsonify({"status": "success", "message": "تم حذف الحساب بنجاح!"})


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000, threaded=True)