import os, sqlite3, json, base64, hmac, hashlib
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, redirect, url_for
from dotenv import load_dotenv
import requests

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv('APP_SECRET','dev-secret')
DB = os.path.join(os.path.dirname(__file__), 'diet_ai.db')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY','').strip()
OPENAI_MODEL = os.getenv('OPENAI_MODEL','gpt-5.6').strip()
LINE_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN','').strip()
LINE_SECRET = os.getenv('LINE_CHANNEL_SECRET','').strip()

RED_FLAGS = [
    '懷孕','哺乳','腎臟疾病','洗腎','第一型糖尿病','胰島素','進食障礙','厭食','暴食症',
    '癌症治療','近期不明原因快速減重'
]

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    c=db()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      external_id TEXT UNIQUE,
      name TEXT, age INTEGER, sex TEXT, height REAL, weight REAL, body_fat REAL,
      goal TEXT, target_weight REAL, steps INTEGER, meals_per_day INTEGER,
      water_ml INTEGER, sleep_hours REAL, medical TEXT, activity TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS logs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER,
      kind TEXT, meal_type TEXT, raw_text TEXT, image_path TEXT,
      analysis TEXT, score INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS checkins (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER, weight REAL, body_fat REAL, water_ml INTEGER, steps INTEGER,
      note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    ''')
    c.commit(); c.close()

init_db()

def upsert_user(external_id, data):
    c=db(); row=c.execute('SELECT * FROM users WHERE external_id=?',(external_id,)).fetchone()
    fields=['name','age','sex','height','weight','body_fat','goal','target_weight','steps','meals_per_day','water_ml','sleep_hours','medical','activity']
    vals=[data.get(f) if data.get(f) not in ('',None) else None for f in fields]
    if row:
        sets=','.join([f'{f}=?' for f in fields])
        c.execute(f'UPDATE users SET {sets},updated_at=CURRENT_TIMESTAMP WHERE external_id=?', vals+[external_id])
        uid=row['id']
    else:
        q=','.join(['?']*(len(fields)+1))
        c.execute(f"INSERT INTO users (external_id,{','.join(fields)}) VALUES ({q})", [external_id]+vals)
        uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.commit(); c.close(); return uid

def get_user(external_id):
    c=db(); r=c.execute('SELECT * FROM users WHERE external_id=?',(external_id,)).fetchone(); c.close(); return dict(r) if r else None

def red_flag_text(user):
    med=(user.get('medical') or '') if user else ''
    hits=[x for x in RED_FLAGS if x in med]
    return hits

def basic_assessment(user):
    if not user: return {'level':'unknown','findings':['尚未完成初次評估'],'actions':['先完成體態評估問卷']}
    findings=[]; actions=[]
    flags=red_flag_text(user)
    if flags:
        return {'level':'red','findings':['需專業人員優先評估：'+ '、'.join(flags)],'actions':['暫不自動設定減脂熱量或高蛋白目標','建議先由醫師／營養師確認後再進一般體態管理流程']}
    steps=user.get('steps') or 0
    water=user.get('water_ml') or 0
    sleep=user.get('sleep_hours') or 0
    meals=user.get('meals_per_day') or 0
    if steps and steps < 4000:
        findings.append('日常活動量偏低')
        actions.append('這週先把每日平均步數增加 1000–1500 步')
    if water and water < 1500:
        findings.append('飲水量偏少')
        actions.append('先把每日飲水提高到至少 1500–2000 ml，再依個人狀況調整')
    if sleep and sleep < 6:
        findings.append('睡眠時間偏少')
        actions.append('先把平均睡眠往 6.5–7 小時靠近')
    if meals and meals <= 1:
        findings.append('餐次過少，可能增加後續飢餓反撲風險')
        actions.append('先確認是否常因太餓而晚間暴食，不急著再砍食量')
    if not findings:
        findings=['目前基礎資料沒有明顯紅旗，下一步以實際餐點紀錄判斷']
        actions=['連續記錄 3–7 天飲食，系統會找出最常出現的問題']
    return {'level':'normal','findings':findings[:3],'actions':actions[:3]}

def ai_analyze(user, text=None, image_bytes=None, mime='image/jpeg'):
    flags=red_flag_text(user or {})
    safety = '使用者有以下紅旗：'+','.join(flags) if flags else '未偵測到紅旗。'
    profile=json.dumps(user or {}, ensure_ascii=False)
    prompt=f'''你是「TDJ 體態管理飲食評估助理」。用繁體中文，口吻像真人體態顧問，不要醫療診斷，不要保證減重效果。
使用者資料：{profile}
安全資訊：{safety}
任務：分析這一餐或這段飲食紀錄。請務必：
1. 先辨識可見/描述的食物；不確定就標示「可能」而不是亂猜。
2. 用「蛋白質、澱粉、蔬菜、脂肪/醬料、飲品」五面向評估，份量用掌/拳/碗等生活化單位。
3. 熱量若估計只能給寬鬆區間，並說照片無法知道油、醬、實際重量。
4. 給 0-100 分的「這餐結構分數」，不是減肥成績。
5. 最後只給 1-3 個下一餐/今天可執行調整，不要一次塞很多規則。
6. 若有紅旗，只做一般飲食紀錄與均衡提醒，不提供熱量赤字、蛋白質克數或限制性飲食處方。
輸出格式：
【這餐看到什麼】
...
【結構評估】
蛋白質：...
澱粉：...
蔬菜：...
脂肪/醬料：...
飲品：...
【這餐結構分數】XX/100
【下一步】
1. ...
【提醒】照片/文字估算限制...
'''
    if not OPENAI_API_KEY:
        return rule_text_analysis(user, text or '')
    try:
        from openai import OpenAI
        client=OpenAI(api_key=OPENAI_API_KEY)
        content=[{'type':'input_text','text':prompt + ('\n使用者文字：'+text if text else '')}]
        if image_bytes:
            b64=base64.b64encode(image_bytes).decode()
            content.append({'type':'input_image','image_url':f'data:{mime};base64,{b64}'})
        resp=client.responses.create(model=OPENAI_MODEL,input=[{'role':'user','content':content}])
        return resp.output_text
    except Exception as e:
        return 'AI 分析暫時無法使用，已改用基礎規則分析。\n\n'+rule_text_analysis(user,text or '')+'\n\n系統訊息：'+str(e)[:160]

def rule_text_analysis(user, text):
    t=text or ''
    score=75; notes=[]; nexts=[]
    protein=['雞','魚','蛋','豆腐','豆漿','牛肉','豬肉','蝦','優格','奶']
    veg=['菜','青菜','花椰菜','高麗菜','菇','菠菜','蔬菜','沙拉']
    carbs=['飯','麵','吐司','麵包','地瓜','馬鈴薯','粥','冬粉','餃']
    drinks=['奶茶','可樂','手搖','果汁','汽水','珍珠']
    if not any(x in t for x in protein): score-=15; notes.append('蛋白質來源看起來偏少'); nexts.append('下一餐補 1 掌左右蛋白質來源')
    if not any(x in t for x in veg): score-=15; notes.append('蔬菜描述偏少'); nexts.append('下一餐至少補 1 拳蔬菜')
    if any(x in t for x in drinks): score-=10; notes.append('有含糖/額外熱量飲品的可能'); nexts.append('今天其他飲品優先改無糖')
    if any(x in t for x in carbs): notes.append('有主食來源；是否過量仍要看份量與整天飲食')
    if not notes: notes=['目前文字資訊有限，建議補充份量或直接拍照']
    return f'''【這餐基礎分析】\n{chr(10).join('• '+x for x in notes)}\n\n【這餐結構分數】{max(40,score)}/100\n\n【下一步】\n{chr(10).join(f'{i+1}. {x}' for i,x in enumerate(nexts[:3])) if nexts else '1. 下一餐維持蛋白質、蔬菜與主食都有的結構'}\n\n【提醒】目前尚未設定 AI API 金鑰，因此這是關鍵字規則版；照片辨識需啟用 AI。'''

def save_log(uid, kind, meal_type, raw_text, analysis, image_path=None):
    import re
    m=re.search(r'(\d{1,3})/100', analysis or '')
    score=int(m.group(1)) if m else None
    c=db(); c.execute('INSERT INTO logs(user_id,kind,meal_type,raw_text,image_path,analysis,score) VALUES(?,?,?,?,?,?,?)',(uid,kind,meal_type,raw_text,image_path,analysis,score)); c.commit(); c.close()

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/assessment', methods=['GET','POST'])
def assessment():
    if request.method=='POST':
        ext=request.form.get('external_id','demo').strip() or 'demo'
        uid=upsert_user(ext, request.form.to_dict())
        user=get_user(ext); result=basic_assessment(user)
        return render_template('result.html', user=user, result=result, ext=ext)
    return render_template('assessment.html')

@app.route('/analyze', methods=['GET','POST'])
def analyze():
    if request.method=='POST':
        ext=request.form.get('external_id','demo').strip() or 'demo'
        user=get_user(ext)
        if not user:
            return redirect(url_for('assessment'))
        text=request.form.get('text','').strip(); meal=request.form.get('meal_type','未指定')
        f=request.files.get('image'); image_bytes=None; mime='image/jpeg'
        if f and f.filename:
            image_bytes=f.read(); mime=f.mimetype or 'image/jpeg'
        analysis=ai_analyze(user,text,image_bytes,mime)
        save_log(user['id'], 'image' if image_bytes else 'text', meal, text, analysis)
        return render_template('analysis.html', analysis=analysis, ext=ext)
    return render_template('analyze.html')

@app.route('/history')
def history():
    ext=request.args.get('external_id','demo'); user=get_user(ext)
    logs=[]
    if user:
        c=db(); logs=[dict(x) for x in c.execute('SELECT * FROM logs WHERE user_id=? ORDER BY id DESC LIMIT 30',(user['id'],)).fetchall()]; c.close()
    return render_template('history.html', user=user, logs=logs, ext=ext)

@app.route('/weekly')
def weekly():
    ext=request.args.get('external_id','demo'); user=get_user(ext)
    if not user: return redirect(url_for('assessment'))
    c=db(); logs=[dict(x) for x in c.execute("SELECT * FROM logs WHERE user_id=? AND created_at >= datetime('now','-7 day') ORDER BY created_at",(user['id'],)).fetchall()]; c.close()
    avg=round(sum(x['score'] for x in logs if x['score'])/max(1,len([x for x in logs if x['score']]))) if logs else None
    summary=f'近 7 天共有 {len(logs)} 筆飲食紀錄。' + (f' 平均餐點結構分數約 {avg}/100。' if avg else ' 尚無足夠分數資料。')
    return render_template('weekly.html', user=user, logs=logs, summary=summary, ext=ext)

@app.route('/health')
def health(): return jsonify(ok=True)

def line_reply(reply_token, text):
    if not LINE_TOKEN: return
    requests.post('https://api.line.me/v2/bot/message/reply',headers={'Authorization':'Bearer '+LINE_TOKEN,'Content-Type':'application/json'},json={'replyToken':reply_token,'messages':[{'type':'text','text':text[:4900]}]},timeout=20)

def valid_signature(body, sig):
    if not LINE_SECRET: return True
    digest=hmac.new(LINE_SECRET.encode(), body, hashlib.sha256).digest()
    expected=base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, sig or '')

@app.route('/line/webhook', methods=['POST'])
def line_webhook():
    raw=request.get_data(); sig=request.headers.get('X-Line-Signature','')
    if not valid_signature(raw,sig): return 'bad signature',400
    data=request.get_json(silent=True) or {}
    for ev in data.get('events',[]):
        if ev.get('type')!='message': continue
        src=ev.get('source',{}); ext=src.get('userId','line-unknown'); token=ev.get('replyToken')
        user=get_user(ext)
        msg=ev.get('message',{})
        if not user:
            line_reply(token,'歡迎使用 TDJ 體態管理飲食評估。你還沒建立體態資料，請先開啟評估頁完成基本資料後，再回 LINE 傳餐點照片或文字。')
            continue
        if msg.get('type')=='text':
            text=msg.get('text','')
            if text in ['本週分析','7天分析','週報']:
                c=db(); logs=[dict(x) for x in c.execute("SELECT * FROM logs WHERE user_id=? AND created_at >= datetime('now','-7 day')",(user['id'],)).fetchall()]; c.close()
                scores=[x['score'] for x in logs if x['score']]
                avg=round(sum(scores)/len(scores)) if scores else None
                reply=f'近7天共記錄 {len(logs)} 餐。'+(f'\n平均餐點結構分數：{avg}/100' if avg else '')+'\n建議持續記錄，系統會抓出重複出現的飲食卡點。'
            else:
                reply=ai_analyze(user,text=text); save_log(user['id'],'text','LINE',text,reply)
            line_reply(token,reply)
        elif msg.get('type')=='image':
            mid=msg.get('id')
            r=requests.get(f'https://api-data.line.me/v2/bot/message/{mid}/content',headers={'Authorization':'Bearer '+LINE_TOKEN},timeout=30)
            if r.ok:
                reply=ai_analyze(user,image_bytes=r.content,mime=r.headers.get('Content-Type','image/jpeg')); save_log(user['id'],'image','LINE','',reply)
                line_reply(token,reply)
            else: line_reply(token,'照片讀取失敗，請再傳一次。')
    return 'OK',200

if __name__=='__main__':
    app.run(host='0.0.0.0',port=int(os.getenv('PORT','5000')),debug=True)
