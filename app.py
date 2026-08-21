
import os, json, sqlite3, base64, requests
try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from openai import OpenAI

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "tdj-v2-change-me")
DB = os.getenv("DB_PATH", "tdj_v2.db")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)

class PGConnection:
    """Compatibility wrapper: existing queries can keep SQLite-style ? placeholders."""
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=()):
        return self.conn.execute(sql.replace("?", "%s"), params)

    def executescript(self, script):
        for statement in script.split(";"):
            statement = statement.strip()
            if statement:
                self.conn.execute(statement)

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()

def db():
    if USE_POSTGRES:
        if psycopg is None:
            raise RuntimeError("DATABASE_URL 已設定，但 psycopg 尚未安裝。")
        return PGConnection(psycopg.connect(DATABASE_URL, row_factory=dict_row))

    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    return c

def init_db():
    c=db()

    if USE_POSTGRES:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS profiles(
          id BIGSERIAL PRIMARY KEY,
          user_key TEXT UNIQUE, name TEXT, sex TEXT, age INTEGER, height DOUBLE PRECISION, weight DOUBLE PRECISION,
          body_fat DOUBLE PRECISION, waist DOUBLE PRECISION, goal TEXT, goal_weight DOUBLE PRECISION, recent_change DOUBLE PRECISION,
          occupation TEXT, sitting_hours DOUBLE PRECISION, steps INTEGER, exercise_days INTEGER,
          exercise_type TEXT, exercise_minutes INTEGER, sleep_hours DOUBLE PRECISION, sleep_quality TEXT,
          meals INTEGER, first_meal TEXT, last_meal TEXT, eating_out TEXT, breakfast TEXT,
          late_snack TEXT, sugary_drinks TEXT, coffee TEXT, alcohol TEXT, snacks TEXT,
          trigger_food TEXT, starch TEXT, protein TEXT, vegetables TEXT, water INTEGER,
          past_success TEXT, max_loss DOUBLE PRECISION, regain TEXT, methods TEXT, current_diet TEXT,
          diet_weeks INTEGER, four_week_change DOUBLE PRECISION, pregnant TEXT, breastfeeding TEXT,
          postpartum TEXT, conditions TEXT, meds TEXT, risk_eating TEXT,
          created_at TEXT, updated_at TEXT,
          activity_level TEXT, daily_calories DOUBLE PRECISION, daily_protein DOUBLE PRECISION,
          daily_carbs DOUBLE PRECISION, daily_fat DOUBLE PRECISION,
          line_user_id TEXT, line_display_name TEXT
        );

        CREATE TABLE IF NOT EXISTS meals(
          id BIGSERIAL PRIMARY KEY,
          user_key TEXT, meal_type TEXT, source TEXT, content TEXT, analysis TEXT, created_at TEXT,
          hunger_before INTEGER, fullness_after INTEGER, water_ml INTEGER, note TEXT,
          calories DOUBLE PRECISION, protein_g DOUBLE PRECISION, carbs_g DOUBLE PRECISION,
          fat_g DOUBLE PRECISION, veg_fists DOUBLE PRECISION, estimate_note TEXT
        );

        CREATE TABLE IF NOT EXISTS checkins(
          id BIGSERIAL PRIMARY KEY,
          user_key TEXT, weight DOUBLE PRECISION, body_fat DOUBLE PRECISION,
          water INTEGER, note TEXT, created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS memberships(
          line_user_id TEXT PRIMARY KEY,
          display_name TEXT,
          status TEXT DEFAULT 'inactive',
          starts_at TEXT,
          expires_at TEXT,
          daily_limit INTEGER DEFAULT 6,
          created_at TEXT,
          updated_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_profiles_line_user_id ON profiles(line_user_id);
        CREATE INDEX IF NOT EXISTS idx_meals_user_key_created_at ON meals(user_key, created_at);
        """)
        c.commit()
        c.close()
        return

    c.executescript("""
    CREATE TABLE IF NOT EXISTS profiles(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_key TEXT UNIQUE, name TEXT, sex TEXT, age INTEGER, height REAL, weight REAL,
      body_fat REAL, waist REAL, goal TEXT, goal_weight REAL, recent_change REAL,
      occupation TEXT, sitting_hours REAL, steps INTEGER, exercise_days INTEGER,
      exercise_type TEXT, exercise_minutes INTEGER, sleep_hours REAL, sleep_quality TEXT,
      meals INTEGER, first_meal TEXT, last_meal TEXT, eating_out TEXT, breakfast TEXT,
      late_snack TEXT, sugary_drinks TEXT, coffee TEXT, alcohol TEXT, snacks TEXT,
      trigger_food TEXT, starch TEXT, protein TEXT, vegetables TEXT, water INTEGER,
      past_success TEXT, max_loss REAL, regain TEXT, methods TEXT, current_diet TEXT,
      diet_weeks INTEGER, four_week_change REAL, pregnant TEXT, breastfeeding TEXT,
      postpartum TEXT, conditions TEXT, meds TEXT, risk_eating TEXT,
      created_at TEXT, updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS meals(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_key TEXT, meal_type TEXT,
      source TEXT, content TEXT, analysis TEXT, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS checkins(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_key TEXT, weight REAL, body_fat REAL,
      water INTEGER, note TEXT, created_at TEXT
    );
    """)

    meal_cols={r[1] for r in c.execute("PRAGMA table_info(meals)").fetchall()}
    for col,typ in [
        ("hunger_before","INTEGER"),("fullness_after","INTEGER"),("water_ml","INTEGER"),("note","TEXT"),
        ("calories","REAL"),("protein_g","REAL"),("carbs_g","REAL"),("fat_g","REAL"),
        ("veg_fists","REAL"),("estimate_note","TEXT")
    ]:
        if col not in meal_cols:
            c.execute(f"ALTER TABLE meals ADD COLUMN {col} {typ}")

    profile_cols={r[1] for r in c.execute("PRAGMA table_info(profiles)").fetchall()}
    for col,typ in [
        ("activity_level","TEXT"),("daily_calories","REAL"),("daily_protein","REAL"),
        ("daily_carbs","REAL"),("daily_fat","REAL"),("line_user_id","TEXT"),("line_display_name","TEXT")
    ]:
        if col not in profile_cols:
            c.execute(f"ALTER TABLE profiles ADD COLUMN {col} {typ}")

    c.executescript("""
    CREATE TABLE IF NOT EXISTS memberships(
      line_user_id TEXT PRIMARY KEY, display_name TEXT, status TEXT DEFAULT 'inactive',
      starts_at TEXT, expires_at TEXT, daily_limit INTEGER DEFAULT 6,
      created_at TEXT, updated_at TEXT
    );
    """)
    c.commit()
    c.close()

init_db()

def current_line_user(): return session.get("line_user")

def user_key():
    u=current_line_user()
    return "line-"+u["userId"] if u and u.get("userId") else session.setdefault("user_key","web-"+os.urandom(8).hex())

def get_profile():
    c=db(); r=c.execute("SELECT * FROM profiles WHERE user_key=?",(user_key(),)).fetchone(); c.close()
    return dict(r) if r else None

def membership():
    u=current_line_user()
    if not u:return None
    c=db(); r=c.execute("SELECT * FROM memberships WHERE line_user_id=?",(u["userId"],)).fetchone(); c.close()
    return dict(r) if r else None

def member_active():
    m=membership()
    if not m or m.get("status")!="active" or not m.get("expires_at"): return False
    try:return datetime.fromisoformat(m["expires_at"])>=datetime.now()
    except:return False

def guard_member():
    if not current_line_user(): return redirect(url_for("line_login"))
    if not member_active(): return redirect(url_for("membership_required"))
    return None

def today_usage():
    today=datetime.now().date().isoformat(); c=db()
    r=c.execute("SELECT COUNT(*) n FROM meals WHERE user_key=? AND substr(created_at,1,10)=?",(user_key(),today)).fetchone(); c.close()
    return int(r["n"] or 0)

def calculate_targets(p):
    sex=p.get("sex","female"); age=float(p.get("age") or 30); h=float(p.get("height") or 160); w=float(p.get("weight") or 60)
    bmr=10*w+6.25*h-5*age+(5 if sex=="male" else -161)
    tdee=bmr*{"low":1.2,"light":1.35,"moderate":1.5,"high":1.7}.get(p.get("activity_level","light"),1.35)
    goal=p.get("goal","減脂")
    calories=max(1200 if sex!="male" else 1500,tdee*.82) if goal in ("減重","減脂") else tdee*1.05 if goal=="增肌改善體態" else tdee
    protein=(1.6 if goal in ("減重","減脂","增肌改善體態") else 1.3)*w
    fat=max(.7*w,calories*.25/9); carbs=max(0,(calories-protein*4-fat*9)/4)
    return {"daily_calories":round(calories/10)*10,"daily_protein":round(protein),"daily_carbs":round(carbs),"daily_fat":round(fat)}

def safety(p):
    red=[]; cond=(p.get("conditions") or "").lower()
    if p.get("pregnant")=="yes": red.append("懷孕")
    if p.get("breastfeeding")=="yes": red.append("哺乳")
    if p.get("risk_eating")=="yes": red.append("進食障礙相關風險")
    if "kidney" in cond or "腎" in cond: red.append("腎臟相關狀況")
    if "insulin" in cond or "胰島素" in cond: red.append("使用胰島素")
    return red

@app.route("/line/login")
def line_login():
    cid=os.getenv("LINE_LOGIN_CHANNEL_ID")
    if not cid:
        session["line_user"]={"userId":"demo-line-user","displayName":"測試會員"}
        return redirect(url_for("home"))
    from urllib.parse import urlencode
    state=os.urandom(12).hex(); session["line_state"]=state
    return redirect("https://access.line.me/oauth2/v2.1/authorize?"+urlencode({
      "response_type":"code","client_id":cid,"redirect_uri":url_for("line_callback",_external=True),
      "state":state,"scope":"profile openid"}))

@app.route("/line/callback")
def line_callback():
    if request.args.get("state")!=session.get("line_state"): return "LINE login state error",400
    tok=requests.post("https://api.line.me/oauth2/v2.1/token",data={
      "grant_type":"authorization_code","code":request.args.get("code"),
      "redirect_uri":url_for("line_callback",_external=True),
      "client_id":os.getenv("LINE_LOGIN_CHANNEL_ID"),
      "client_secret":os.getenv("LINE_LOGIN_CHANNEL_SECRET")},timeout=15)
    if tok.status_code!=200:return "LINE token error",400
    prof=requests.get("https://api.line.me/v2/profile",headers={"Authorization":"Bearer "+tok.json()["access_token"]},timeout=15)
    if prof.status_code!=200:return "LINE profile error",400
    p=prof.json(); session["line_user"]={"userId":p["userId"],"displayName":p.get("displayName","LINE會員")}
    now=datetime.now().isoformat(timespec="seconds"); c=db()
    c.execute("""INSERT INTO memberships(line_user_id,display_name,status,created_at,updated_at) VALUES(?,?,?,?,?)
      ON CONFLICT(line_user_id) DO UPDATE SET display_name=excluded.display_name,updated_at=excluded.updated_at""",
      (p["userId"],p.get("displayName"),"inactive",now,now)); c.commit(); c.close()
    return redirect(url_for("home"))

@app.route("/membership-required")
def membership_required(): return render_template("membership_required.html",user=current_line_user(),member=membership())

@app.route("/")
def home(): return render_template("home.html",profile=get_profile(),line_user=current_line_user(),member=membership(),active=member_active())

@app.route("/assessment",methods=["GET","POST"])
def assessment():
    if request.method=="POST":
        f=request.form
        fields=["name","sex","age","height","weight","goal","steps","exercise_days","activity_level","pregnant","breastfeeding","conditions","meds","risk_eating"]
        nums={"age":int,"height":float,"weight":float,"steps":int,"exercise_days":int}; data={}
        for x in fields:
            v=f.get(x,"").strip()
            if x in nums:
                try:v=nums[x](v) if v else None
                except:v=None
            data[x]=v
        data.update(calculate_targets(data)); data["user_key"]=user_key(); now=datetime.now().isoformat(timespec="seconds")
        lu=current_line_user() or {}; data["line_user_id"]=lu.get("userId"); data["line_display_name"]=lu.get("displayName")
        save=fields+["daily_calories","daily_protein","daily_carbs","daily_fat","line_user_id","line_display_name"]
        c=db(); old=c.execute("SELECT id FROM profiles WHERE user_key=?",(user_key(),)).fetchone()
        if old:
            c.execute("UPDATE profiles SET "+",".join(f"{k}=?" for k in save)+",updated_at=? WHERE user_key=?",[data[k] for k in save]+[now,user_key()])
        else:
            cols=["user_key"]+save+["created_at","updated_at"]
            c.execute(f"INSERT INTO profiles ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",[data["user_key"]]+[data[k] for k in save]+[now,now])
        c.commit(); c.close(); return redirect(url_for("result"))
    return render_template("assessment.html",profile=get_profile() or {})

@app.route("/result")
def result():
    g=guard_member()
    if g:return g
    p=get_profile()
    if not p:return redirect(url_for("assessment"))
    return render_template("result.html",p=p,red=safety(p))

def ai_analyze(text=None,image_bytes=None,p=None,timing="before"):
    key=os.getenv("OPENAI_API_KEY")
    if not key:return None,"AI 尚未啟用。請先設定 OPENAI_API_KEY。"
    client=OpenAI(api_key=key)
    prompt=f"""你是 TDJ AI 餐盤教練。使用者每日目標：{p.get('daily_calories')} kcal、蛋白質{p.get('daily_protein')}g、碳水{p.get('daily_carbs')}g、脂肪{p.get('daily_fat')}g。
現在是{'吃之前' if timing=='before' else '已吃完'}。照片無法精準知道重量、油量或熱量；若沒有重量只能做合理估算並明說。若有重量或營養標示則優先使用。
請估算整餐 calories、protein_g、carbs_g、fat_g、veg_fists，並分別判斷蛋白質、碳水、脂肪、蔬菜為不足/適量/偏多/無法判斷。
建議要具體到『再補約1拳蔬菜』『再補半掌蛋白質』。若已吃完，不叫人回頭少吃或硬補，改給下一餐方向。
只輸出JSON：
{{"calories":數字,"protein_g":數字,"carbs_g":數字,"fat_g":數字,"veg_fists":數字,"confidence":"高/中/低","protein_status":"...","carbs_status":"...","fat_status":"...","veg_status":"...","good":"...","advice":"...","next_meal":"...","estimate_note":"..."}}"""
    content=[{"type":"input_text","text":prompt+"\n餐點："+(text or "請分析照片")}]
    if image_bytes:
        b64=base64.b64encode(image_bytes).decode(); content.append({"type":"input_image","image_url":"data:image/jpeg;base64,"+b64})
    r=client.responses.create(model=os.getenv("OPENAI_MODEL","gpt-5-mini"),input=[{"role":"user","content":content}])
    txt=r.output_text.strip()
    if txt.startswith("```"):
        txt=txt.strip("`")
        if txt.startswith("json"):txt=txt[4:].lstrip()
    try:return json.loads(txt),None
    except:return None,"AI 有回覆，但營養資料格式無法讀取，請再試一次。"

def today_totals():
    today=datetime.now().date().isoformat(); c=db()
    rows=c.execute("SELECT calories,protein_g,carbs_g,fat_g FROM meals WHERE user_key=? AND substr(created_at,1,10)=?",(user_key(),today)).fetchall(); c.close()
    return {"calories":round(sum((r["calories"] or 0) for r in rows),1),"protein":round(sum((r["protein_g"] or 0) for r in rows),1),"carbs":round(sum((r["carbs_g"] or 0) for r in rows),1),"fat":round(sum((r["fat_g"] or 0) for r in rows),1)}

def remaining(p,t):
    return {k:max(0,round((p.get("daily_"+("calories" if k=="calories" else k)) or 0)-t[k],1)) for k in ("calories","protein","carbs","fat")}

@app.route("/meal",methods=["GET","POST"])
def meal():
    g=guard_member()
    if g:return g
    p=get_profile()
    if not p:return redirect(url_for("assessment"))
    analysis=None; error=None; form_data={}
    if request.method=="POST":
        m=membership() or {}; limit=int(m.get("daily_limit") or 6)
        if today_usage()>=limit:
            totals=today_totals(); rem=remaining(p,totals)
            return render_template("meal.html",analysis=None,error=f"今天已達 {limit} 次 AI 分析上限。",p=p,totals=totals,remaining=rem,form_data={},usage=today_usage(),limit=limit)
        form_data=request.form.to_dict(); text=request.form.get("content","").strip(); meal_type=request.form.get("meal_type","其他"); timing=request.form.get("timing","before")
        img=request.files.get("photo"); raw=img.read() if img and img.filename else None
        def num(n):
            try:return int(request.form.get(n,"") or 0) or None
            except:return None
        try:analysis,error=ai_analyze(text,raw,p,timing)
        except Exception as e:error="AI 分析暫時無法完成："+str(e)
        if analysis:
            summary=f"做得好：{analysis.get('good','')}\n最優先調整：{analysis.get('advice','')}\n下一餐：{analysis.get('next_meal','')}"
            c=db(); c.execute("""INSERT INTO meals(user_key,meal_type,source,content,analysis,created_at,hunger_before,fullness_after,water_ml,note,calories,protein_g,carbs_g,fat_g,veg_fists,estimate_note)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(user_key(),meal_type,"photo" if raw else "text",text,summary,datetime.now().isoformat(timespec="seconds"),num("hunger_before"),num("fullness_after"),num("water_ml"),request.form.get("note","").strip(),analysis.get("calories"),analysis.get("protein_g"),analysis.get("carbs_g"),analysis.get("fat_g"),analysis.get("veg_fists"),analysis.get("estimate_note")))
            c.commit(); c.close()
    totals=today_totals(); rem=remaining(p,totals)
    return render_template("meal.html",analysis=analysis,error=error,p=p,totals=totals,remaining=rem,form_data=form_data,usage=today_usage(),limit=int((membership() or {}).get("daily_limit") or 6))

def tracked_days(rows):
    return len({r["created_at"][:10] for r in rows})

@app.route("/week")
def week():
    g=guard_member()
    if g:return g
    p=get_profile()
    if not p:return redirect(url_for("assessment"))
    since=(datetime.now()-timedelta(days=7)).isoformat(); c=db()
    rows=c.execute("SELECT * FROM meals WHERE user_key=? AND created_at>=? ORDER BY created_at DESC",(user_key(),since)).fetchall(); c.close()
    return render_template("week.html",rows=rows)

@app.route("/reassessment")
def reassessment():
    p=get_profile()
    if not p:return redirect(url_for("assessment"))
    since=(datetime.now()-timedelta(days=7)).isoformat()
    c=db(); rows=c.execute("SELECT * FROM meals WHERE user_key=? AND created_at>=? ORDER BY created_at",(user_key(),since)).fetchall(); c.close()
    days=tracked_days(rows)
    result=None
    if days>=7:
        first_summary,priorities=synthesize(p)
        key=os.getenv("OPENAI_API_KEY")
        if key:
            data=[{"time":r["created_at"],"type":r["meal_type"],"content":r["content"],"analysis":r["analysis"],"hunger":r["hunger_before"],"fullness":r["fullness_after"],"note":r["note"]} for r in rows]
            prompt=f"""請以繁體中文做一般體態管理的7天二次評估，不診斷疾病、不保證減重、不捏造未記錄資料。
第一次判斷：{first_summary}
第一次優先方向：{json.dumps(priorities,ensure_ascii=False)}
7天真實紀錄：{json.dumps(data,ensure_ascii=False)}
請只輸出 JSON，四個欄位：headline、observed、compare、next。
observed=真實紀錄最明顯的重複模式；compare=第一次問卷與真實紀錄是否一致；next=下一階段只給一個最優先具體調整。"""
            try:
                client=OpenAI(api_key=key)
                resp=client.responses.create(model=os.getenv("OPENAI_MODEL","gpt-5-mini"),input=[{"role":"user","content":[{"type":"input_text","text":prompt}]}])
                txt=resp.output_text.strip()
                if txt.startswith("```"): txt=txt.strip("`").replace("json\n","",1)
                result=json.loads(txt)
            except Exception:
                result={"headline":"7 天紀錄完成，已進入二次評估。","observed":"AI 分析暫時無法完成，但紀錄已保留。","compare":"目前先不改變第一次策略。","next":"稍後重新整理本頁再分析。"}
        else:
            result={"headline":"7 天紀錄完成，可以重新校正第一次評估。","observed":f"這 7 天共記錄 {len(rows)} 餐。","compare":"目前尚未啟用 AI，因此不硬推論問卷與實際飲食的差異。","next":"先檢查最常重複出現的餐點模式，一次只調整一件事。"}
    return render_template("reassessment.html",days=days,result=result)

@app.route("/reset")
def reset():
    session.clear()
    return redirect(url_for("home"))

def admin_ok():
    return bool(os.getenv("ADMIN_TOKEN") and (request.args.get("token") or request.form.get("token"))==os.getenv("ADMIN_TOKEN"))

@app.route("/admin")
def admin():
    if not admin_ok(): return "Unauthorized",401
    today=datetime.now().date().isoformat(); c=db()
    rows=c.execute("""SELECT m.*,p.name,p.weight,p.goal,
      (SELECT COUNT(*) FROM meals x WHERE x.user_key='line-'||m.line_user_id AND substr(x.created_at,1,10)=?) today_uses
      FROM memberships m LEFT JOIN profiles p ON p.line_user_id=m.line_user_id ORDER BY m.updated_at DESC""",(today,)).fetchall(); c.close()
    return render_template("admin.html",rows=rows,token=request.args.get("token"))

@app.route("/admin/member/<uid>",methods=["POST"])
def admin_member(uid):
    if not admin_ok(): return "Unauthorized",401
    days=int(request.form.get("days","30")); now=datetime.now(); c=db()
    old=c.execute("SELECT expires_at FROM memberships WHERE line_user_id=?",(uid,)).fetchone(); start=now
    if old and old["expires_at"]:
        try:
            e=datetime.fromisoformat(old["expires_at"])
            if e>now:start=e
        except: pass
    exp=start+timedelta(days=days)
    c.execute("UPDATE memberships SET status='active',starts_at=COALESCE(starts_at,?),expires_at=?,updated_at=? WHERE line_user_id=?",
      (now.isoformat(timespec="seconds"),exp.isoformat(timespec="seconds"),now.isoformat(timespec="seconds"),uid)); c.commit(); c.close()
    return redirect(url_for("admin",token=request.form.get("token")))

@app.route("/admin/client/<uid>")
def admin_client(uid):
    if not admin_ok(): return "Unauthorized",401
    c=db(); p=c.execute("SELECT * FROM profiles WHERE line_user_id=?",(uid,)).fetchone()
    meals=c.execute("SELECT * FROM meals WHERE user_key=? ORDER BY created_at DESC LIMIT 100",("line-"+uid,)).fetchall()
    m=c.execute("SELECT * FROM memberships WHERE line_user_id=?",(uid,)).fetchone(); c.close()
    return render_template("admin_client.html",p=p,meals=meals,m=m,token=request.args.get("token"))

@app.route("/health")
def health(): return {"status":"ok","version":"meal-coach-1.2","database":"postgres" if USE_POSTGRES else "sqlite"}

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",5000)),debug=True)
