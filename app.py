
import os, json, sqlite3, base64, requests, time, secrets
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
          daily_carbs DOUBLE PRECISION, daily_fat DOUBLE PRECISION, daily_water_ml DOUBLE PRECISION, custom_water_ml DOUBLE PRECISION,
          protein_factor DOUBLE PRECISION, calorie_deficit_pct DOUBLE PRECISION, bmi_calc DOUBLE PRECISION,
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

        CREATE TABLE IF NOT EXISTS hydration_logs(
          id BIGSERIAL PRIMARY KEY,
          user_key TEXT,
          amount_ml INTEGER,
          created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS submission_tokens(
          token TEXT PRIMARY KEY,
          user_key TEXT,
          created_at TEXT
        );

        ALTER TABLE meals ADD COLUMN IF NOT EXISTS deleted_at TEXT;
        ALTER TABLE profiles ADD COLUMN IF NOT EXISTS daily_water_ml DOUBLE PRECISION;
        ALTER TABLE profiles ADD COLUMN IF NOT EXISTS custom_water_ml DOUBLE PRECISION;
        ALTER TABLE profiles ADD COLUMN IF NOT EXISTS protein_factor DOUBLE PRECISION;
        ALTER TABLE profiles ADD COLUMN IF NOT EXISTS calorie_deficit_pct DOUBLE PRECISION;
        ALTER TABLE profiles ADD COLUMN IF NOT EXISTS bmi_calc DOUBLE PRECISION;

        CREATE INDEX IF NOT EXISTS idx_profiles_line_user_id ON profiles(line_user_id);
        CREATE INDEX IF NOT EXISTS idx_meals_user_key_created_at ON meals(user_key, created_at);
        CREATE INDEX IF NOT EXISTS idx_hydration_user_key_created_at ON hydration_logs(user_key, created_at);
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
        ("veg_fists","REAL"),("estimate_note","TEXT"),("deleted_at","TEXT")
    ]:
        if col not in meal_cols:
            c.execute(f"ALTER TABLE meals ADD COLUMN {col} {typ}")

    profile_cols={r[1] for r in c.execute("PRAGMA table_info(profiles)").fetchall()}
    for col,typ in [
        ("activity_level","TEXT"),("daily_calories","REAL"),("daily_protein","REAL"),
        ("daily_carbs","REAL"),("daily_fat","REAL"),("daily_water_ml","REAL"),("custom_water_ml","REAL"),
        ("protein_factor","REAL"),("calorie_deficit_pct","REAL"),("bmi_calc","REAL"),
        ("line_user_id","TEXT"),("line_display_name","TEXT")
    ]:
        if col not in profile_cols:
            c.execute(f"ALTER TABLE profiles ADD COLUMN {col} {typ}")

    c.executescript("""
    CREATE TABLE IF NOT EXISTS memberships(
      line_user_id TEXT PRIMARY KEY, display_name TEXT, status TEXT DEFAULT 'inactive',
      starts_at TEXT, expires_at TEXT, daily_limit INTEGER DEFAULT 6,
      created_at TEXT, updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS hydration_logs(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_key TEXT, amount_ml INTEGER, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS submission_tokens(
      token TEXT PRIMARY KEY, user_key TEXT, created_at TEXT
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
    c=db()
    r=c.execute("SELECT * FROM profiles WHERE user_key=?",(user_key(),)).fetchone()
    c.close()
    if not r:
        return None

    p=dict(r)

    # Backfill targets for members created before v1.3.
    # Old profiles can have NULL for the newly added nutrition/water fields.
    target_fields=[
        "daily_calories","daily_protein","daily_carbs","daily_fat",
        "daily_water_ml","protein_factor","calorie_deficit_pct","bmi_calc"
    ]
    if any(p.get(k) is None for k in target_fields):
        targets=calculate_targets(p)
        c=db()
        c.execute(
            """UPDATE profiles
               SET daily_calories=?, daily_protein=?, daily_carbs=?, daily_fat=?,
                   daily_water_ml=?, protein_factor=?, calorie_deficit_pct=?, bmi_calc=?,
                   updated_at=?
               WHERE user_key=?""",
            (
                targets["daily_calories"], targets["daily_protein"], targets["daily_carbs"],
                targets["daily_fat"], targets["daily_water_ml"], targets["protein_factor"],
                targets["calorie_deficit_pct"], targets["bmi_calc"],
                datetime.now().isoformat(timespec="seconds"), user_key()
            )
        )
        c.commit()
        c.close()
        p.update(targets)

    return p

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
    r=c.execute("SELECT COUNT(*) n FROM meals WHERE user_key=? AND substr(created_at,1,10)=? AND deleted_at IS NULL",(user_key(),today)).fetchone(); c.close()
    return int(r["n"] or 0)

def calculate_targets(p):
    """General adult wellness starting targets, not medical nutrition therapy."""
    sex=p.get("sex","female")
    age=float(p.get("age") or 30); h=float(p.get("height") or 160); w=float(p.get("weight") or 60)
    activity=p.get("activity_level","light"); exercise_days=int(p.get("exercise_days") or 0); goal=p.get("goal","減脂")
    bmr=10*w+6.25*h-5*age+(5 if sex=="male" else -161)
    tdee=bmr*{"low":1.2,"light":1.35,"moderate":1.5,"high":1.7}.get(activity,1.35)
    bmi=w/((h/100)**2) if h else 0
    deficit=0.0
    if goal in ("減重","減脂"):
        if bmi and bmi<18.5: deficit=0.0
        elif bmi<24: deficit=0.12
        elif bmi<27: deficit=0.15
        else: deficit=0.18
        calories=max(1200 if sex!="male" else 1500,tdee*(1-deficit))
    elif goal=="增肌改善體態": calories=tdee*1.05
    else: calories=tdee
    if goal=="增肌改善體態": protein_factor=1.6 if exercise_days<3 else 1.8
    elif goal in ("減重","減脂"):
        if activity=="low" and exercise_days==0: protein_factor=1.2
        elif exercise_days<=2 and activity in ("low","light"): protein_factor=1.3
        else: protein_factor=1.5
    else:
        if activity=="low" and exercise_days==0: protein_factor=1.0
        elif exercise_days<=2: protein_factor=1.2
        else: protein_factor=1.4
    protein=protein_factor*w
    fat=max(0.8*w,calories*0.25/9)
    carbs=max(0,(calories-protein*4-fat*9)/4)
    default_water=w*35
    try: custom=float(p.get("custom_water_ml")) if p.get("custom_water_ml") not in (None,"") else None
    except: custom=None
    water=custom if custom and 1000<=custom<=5000 else default_water
    water=max(1000,min(5000,water))
    return {"daily_calories":round(calories/10)*10,"daily_protein":round(protein),"daily_carbs":round(carbs),"daily_fat":round(fat),"daily_water_ml":round(water/100)*100,"protein_factor":protein_factor,"calorie_deficit_pct":round(deficit*100),"bmi_calc":round(bmi,1)}

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
        fields=["name","sex","age","height","weight","goal","steps","exercise_days","activity_level","custom_water_ml","pregnant","breastfeeding","conditions","meds","risk_eating"]
        nums={"age":int,"height":float,"weight":float,"steps":int,"exercise_days":int,"custom_water_ml":float}; data={}
        for x in fields:
            v=f.get(x,"").strip()
            if x in nums:
                try:v=nums[x](v) if v else None
                except:v=None
            data[x]=v
        data.update(calculate_targets(data)); data["user_key"]=user_key(); now=datetime.now().isoformat(timespec="seconds")
        lu=current_line_user() or {}; data["line_user_id"]=lu.get("userId"); data["line_display_name"]=lu.get("displayName")
        save=fields+["daily_calories","daily_protein","daily_carbs","daily_fat","daily_water_ml",
                     "protein_factor","calorie_deficit_pct","bmi_calc","line_user_id","line_display_name"]
        c=db(); old=c.execute("SELECT id FROM profiles WHERE user_key=?",(user_key(),)).fetchone()
        if old:
            c.execute("UPDATE profiles SET "+",".join(f"{k}=?" for k in save)+",updated_at=? WHERE user_key=?",[data[k] for k in save]+[now,user_key()])
        else:
            cols=["user_key"]+save+["created_at","updated_at"]
            c.execute(f"INSERT INTO profiles ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",[data["user_key"]]+[data[k] for k in save]+[now,now])
        c.commit(); c.close(); return redirect(url_for("result"))
    return render_template("assessment.html",profile=get_profile() or {})

@app.route("/water-target",methods=["POST"])
def water_target():
    g=guard_member()
    if g:return g
    p=get_profile()
    if not p:return redirect(url_for("assessment"))
    try: custom=float(request.form.get("custom_water_ml","") or 0)
    except: custom=0
    if custom and 1000<=custom<=5000:
        c=db(); c.execute("UPDATE profiles SET custom_water_ml=?, daily_water_ml=?, updated_at=? WHERE user_key=?",(custom,round(custom/100)*100,datetime.now().isoformat(timespec="seconds"),user_key())); c.commit(); c.close()
    return redirect(url_for("result"))

@app.route("/result")
def result():
    g=guard_member()
    if g:return g
    p=get_profile()
    if not p:return redirect(url_for("assessment"))
    return render_template("result.html",p=p,red=safety(p))

def ai_analyze(text=None,image_bytes=None,p=None,timing="before",hunger_before=None,fullness_after=None,totals=None,meal_type=None):
    key=os.getenv("OPENAI_API_KEY")
    if not key:return None,"AI 尚未啟用。請先設定 OPENAI_API_KEY。"
    client=OpenAI(api_key=key, timeout=float(os.getenv("OPENAI_TIMEOUT_SECONDS","60")), max_retries=0)
    started=time.monotonic()
    app.logger.info("AI meal analysis started user=%s photo=%s", user_key(), bool(image_bytes))
    t_prep=time.monotonic()
    totals=totals or {"calories":0,"protein":0,"carbs":0,"fat":0,"water":0}
    rem={"calories":max(0,(p.get("daily_calories") or 0)-totals.get("calories",0)),"protein":max(0,(p.get("daily_protein") or 0)-totals.get("protein",0)),"carbs":max(0,(p.get("daily_carbs") or 0)-totals.get("carbs",0)),"fat":max(0,(p.get("daily_fat") or 0)-totals.get("fat",0))}
    prompt=f"""你是 TDJ AI 餐盤教練。你的任務不是每餐挑毛病，而是幫使用者把今天剩下的飲食安排得更合理。這是一般成人飲食管理，不做醫療診斷或疾病營養治療。
每日目標：{p.get('daily_calories')} kcal，蛋白質 {p.get('daily_protein')}g，碳水 {p.get('daily_carbs')}g，脂肪 {p.get('daily_fat')}g。
本餐前已累計：{totals.get('calories',0)} kcal，P {totals.get('protein',0)}g，C {totals.get('carbs',0)}g，F {totals.get('fat',0)}g。
本餐前剩餘：約 {rem['calories']:.0f} kcal，P {rem['protein']:.0f}g，C {rem['carbs']:.0f}g，F {rem['fat']:.0f}g。
餐別：{meal_type or '未填'}；分析時點：{'還沒吃' if timing=='before' else '已經吃完'}；餐前飢餓：{hunger_before if hunger_before else '未填'}/10；餐後飽足：{fullness_after if fullness_after else '未填'}/10。
規則：
1. 沒有明確重量只能合理估算並明說；有克數或營養標示優先使用。
2. 不可因單餐未達每日目標1/3就判定蛋白質或碳水不足。
3. 要同時看餐別、已吃、剩餘、飢餓/飽足、吃前/吃後。
4. 若單餐蛋白質已約20–30g以上，對多數一般成人不能只因每日目標較高就寫不足。
5. 已吃完且飽足度>=7，不要叫人立刻補吃；把缺口移到下一餐。
6. 蔬菜拳數採實用級距，只能輸出 0、0.5、1、1.5、2 或以上的0.5倍數。蔥花、蒜末、香菜、洋蔥碎、醬汁中的碎菜、裝飾菜、零星配料都不算蔬菜份量；照片沒有看見可形成實際份量的蔬菜時，veg_fists 必須為0，veg_status必須為偏少。吃前可建議加1–2拳；吃完則下一餐補，不要求硬補。
7. 碳水g是碳水化合物，不是白飯/澱粉重量。
8. 下一餐不要固定雞胸白飯；請給營養範圍＋2個台灣常見可執行例子，如自助餐、便當、早餐店、超商、火鍋、麵店、滷味、日式定食。
9. 若這餐其實合理，要明確說這餐不用硬改。
10. 最多只抓1–2個最值得調整的地方。
11. 若脂肪已接近日上限，下一餐優先提醒少炸物、濃醬、加工肉、堅果大量攝取，而不是只減飯。
12. calories與P/C/F應大致一致。
13. 下一餐建議必須受本餐後真正剩餘熱量、蛋白質、碳水、脂肪限制；不可建議超過剩餘額度。若某營養素剩餘很少，就不要給高於剩餘量的建議。
只輸出JSON：{{"calories":數字,"protein_g":數字,"carbs_g":數字,"fat_g":數字,"veg_fists":數字,"confidence":"高/中/低","protein_status":"足夠/可接受/偏少/偏多/無法判斷","carbs_status":"足夠/可接受/偏少/偏多/無法判斷","fat_status":"足夠/可接受/偏少/偏多/無法判斷","veg_status":"足夠/可接受/偏少/偏多/無法判斷","overall":"一句整體判斷","good":"做得好的地方","advice":"現在最優先做的1件事；若不用硬改就明說","remaining_after":"placeholder","next_meal":"下一餐營養範圍＋2個台灣常見實際選擇","estimate_note":"估算限制"}}"""
    content=[{"type":"input_text","text":prompt+"\n餐點："+(text or "請分析照片")}]
    if image_bytes:
        b64=base64.b64encode(image_bytes).decode(); content.append({"type":"input_image","image_url":"data:image/jpeg;base64,"+b64})
    app.logger.info("PHOTO_PREP %.2fs user=%s bytes=%s", time.monotonic()-t_prep, user_key(), len(image_bytes or b""))
    t_api=time.monotonic()
    try:
        r=client.responses.create(
            model=os.getenv("OPENAI_MODEL","gpt-5.4-mini"),
            input=[{"role":"user","content":content}],
            max_output_tokens=1600,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "meal_analysis",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "calories": {"type": "number"},
                            "protein_g": {"type": "number"},
                            "carbs_g": {"type": "number"},
                            "fat_g": {"type": "number"},
                            "veg_fists": {"type": "number"},
                            "confidence": {"type": "string", "enum": ["高", "中", "低"]},
                            "protein_status": {"type": "string", "enum": ["足夠", "可接受", "偏少", "偏多", "無法判斷"]},
                            "carbs_status": {"type": "string", "enum": ["足夠", "可接受", "偏少", "偏多", "無法判斷"]},
                            "fat_status": {"type": "string", "enum": ["足夠", "可接受", "偏少", "偏多", "無法判斷"]},
                            "veg_status": {"type": "string", "enum": ["足夠", "可接受", "偏少", "偏多", "無法判斷"]},
                            "overall": {"type": "string"},
                            "good": {"type": "string"},
                            "advice": {"type": "string"},
                            "remaining_after": {"type": "string"},
                            "next_meal": {"type": "string"},
                            "estimate_note": {"type": "string"}
                        },
                        "required": ["calories","protein_g","carbs_g","fat_g","veg_fists","confidence","protein_status","carbs_status","fat_status","veg_status","overall","good","advice","remaining_after","next_meal","estimate_note"],
                        "additionalProperties": False
                    }
                }
            }
        )
    except Exception as e:
        app.logger.exception("AI_ANALYZE_ERROR user=%s photo=%s bytes=%s type=%s message=%s",
                             user_key(), bool(image_bytes), len(image_bytes or b""),
                             type(e).__name__, str(e))
        raise
    finally:
        app.logger.info("AI meal analysis finished in %.2fs user=%s", time.monotonic()-started, user_key())
    app.logger.info("OPENAI_REQUEST %.2fs user=%s", time.monotonic()-t_api, user_key())
    txt=(getattr(r,"output_text","") or "").strip()
    status=getattr(r,"status",None)
    incomplete=getattr(r,"incomplete_details",None)
    app.logger.info("AI_RESPONSE_RECEIVED user=%s status=%s chars=%s incomplete=%r", user_key(), status, len(txt), incomplete)
    if not txt:
        # Structured Outputs should expose valid JSON through output_text. An empty string is not JSON;
        # surface the API response state instead of calling json.loads("") and hiding the real cause.
        app.logger.error("AI_EMPTY_OUTPUT user=%s status=%s incomplete=%r output=%r", user_key(), status, incomplete, getattr(r,"output",None))
        return None,"AI 這次沒有完成餐點判讀，請再試一次。文字內容已保留；照片需重新選擇。"
    t_parse=time.monotonic()
    if txt.startswith("```"):
        txt=txt.strip("`")
        if txt.startswith("json"): txt=txt[4:].lstrip()
    try:
        data=json.loads(txt)
        app.logger.info("PARSE %.3fs user=%s", time.monotonic()-t_parse, user_key())
        for k in ["calories","protein_g","carbs_g","fat_g","veg_fists"]:
            try:data[k]=float(data.get(k,0) or 0)
            except:data[k]=0
        # Vegetable servings are intentionally coarse. Tiny garnishes/seasonings must not become fake 0.1/0.2-fist vegetables.
        vf=max(0,float(data.get("veg_fists",0) or 0))
        if vf < 0.5:
            vf=0.0
        else:
            vf=round(vf*2)/2
        data["veg_fists"]=vf
        if vf == 0:
            data["veg_status"]="偏少"

        macro_kcal=data["protein_g"]*4+data["carbs_g"]*4+data["fat_g"]*9
        if macro_kcal>0:
            ai_kcal=float(data.get("calories",0) or 0)
            if ai_kcal<=0 or abs(ai_kcal-macro_kcal)/macro_kcal>0.15:
                data["calories"]=round(macro_kcal)
                data["estimate_note"]=(data.get("estimate_note","")+" 熱量已依三大營養素估算校正。").strip()
        post={"calories":max(0,round(rem["calories"]-data["calories"])),"protein":max(0,round(rem["protein"]-data["protein_g"])),"carbs":max(0,round(rem["carbs"]-data["carbs_g"])),"fat":max(0,round(rem["fat"]-data["fat_g"]))}
        data["daily_before"]={"calories":round(totals.get("calories",0)),"protein":round(totals.get("protein",0)),"carbs":round(totals.get("carbs",0)),"fat":round(totals.get("fat",0))}
        data["daily_after"]={"calories":round(totals.get("calories",0)+data["calories"]),"protein":round(totals.get("protein",0)+data["protein_g"]),"carbs":round(totals.get("carbs",0)+data["carbs_g"]),"fat":round(totals.get("fat",0)+data["fat_g"])}
        data["remaining_values"]=post
        data["remaining_after"]=f"約 {post['calories']} kcal｜蛋白質 {post['protein']}g｜碳水 {post['carbs']}g｜脂肪 {post['fat']}g"
        # The numeric target is generated by Python from the exact post-meal remainder,
        # so the next-meal advice cannot accidentally exceed today's remaining allowance.
        data["next_meal_budget"]=next_meal_budget(post)
        data["next_meal"]=data["next_meal_budget"]
        return data,None
    except Exception as e:
        app.logger.exception("AI_JSON_PARSE_ERROR user=%s type=%s message=%s raw=%r",
                             user_key(), type(e).__name__, str(e), txt[:1200])
        return None,"餐點分析暫時無法完成，請稍後再試。文字內容已保留；基於瀏覽器安全限制，照片需要重新選擇。"

def next_meal_budget(post):
    """Create a conservative next-meal target from the REAL remaining daily allowance.
    Remaining is a ceiling/guide, not a requirement to eat it all.
    """
    kcal=max(0,float(post.get("calories",0) or 0))
    protein=max(0,float(post.get("protein",0) or 0))
    carbs=max(0,float(post.get("carbs",0) or 0))
    fat=max(0,float(post.get("fat",0) or 0))
    if kcal <= 0:
        return "今天的熱量目標已用完；下一餐先不要用『補足營養素』為理由硬吃，若仍有明顯飢餓感，優先選清淡、低油且份量小的食物。"
    kcal_hi=min(kcal, 500)
    kcal_lo=min(kcal_hi, 300)
    if kcal_hi < 200:
        kcal_range=f"不超過約 {round(kcal_hi)} kcal"
    else:
        kcal_range=f"約 {round(kcal_lo)}–{round(kcal_hi)} kcal"
    p_hi=min(protein,30); p_lo=min(p_hi,20)
    c_hi=min(carbs,60); c_lo=min(c_hi,30)
    f_hi=min(fat,12); f_lo=min(f_hi,5)
    def rg(lo,hi,unit='g'):
        if hi<=0:return f"0{unit}"
        if hi<lo or abs(hi-lo)<1:return f"不超過約 {round(hi)}{unit}"
        return f"約 {round(lo)}–{round(hi)}{unit}"
    return (f"依今天真正剩餘額度，下一餐先抓：熱量 {kcal_range}、蛋白質 {rg(p_lo,p_hi)}、"
            f"碳水 {rg(c_lo,c_hi)}、脂肪 {rg(f_lo,f_hi)}。這是下一餐安排範圍，不代表今天剩下的額度一定要全部吃完。")

def today_totals():
    today=datetime.now().date().isoformat(); c=db()
    rows=c.execute("SELECT calories,protein_g,carbs_g,fat_g,water_ml FROM meals WHERE user_key=? AND substr(created_at,1,10)=? AND deleted_at IS NULL",(user_key(),today)).fetchall()
    water_rows=c.execute("SELECT amount_ml FROM hydration_logs WHERE user_key=? AND substr(created_at,1,10)=?",(user_key(),today)).fetchall()
    c.close()
    return {
        "calories":round(sum((r["calories"] or 0) for r in rows),1),
        "protein":round(sum((r["protein_g"] or 0) for r in rows),1),
        "carbs":round(sum((r["carbs_g"] or 0) for r in rows),1),
        "fat":round(sum((r["fat_g"] or 0) for r in rows),1),
        "water":int(sum((r["water_ml"] or 0) for r in rows) + sum((r["amount_ml"] or 0) for r in water_rows))
    }

def remaining(p,t):
    return {
        "calories":max(0,round((p.get("daily_calories") or 0)-t["calories"],1)),
        "protein":max(0,round((p.get("daily_protein") or 0)-t["protein"],1)),
        "carbs":max(0,round((p.get("daily_carbs") or 0)-t["carbs"],1)),
        "fat":max(0,round((p.get("daily_fat") or 0)-t["fat"],1)),
        "water":max(0,round((p.get("daily_water_ml") or 0)-t["water"],0))
    }

@app.route("/water",methods=["POST"])
def water():
    g=guard_member()
    if g:return g
    p=get_profile()
    if not p:return redirect(url_for("assessment"))
    try:
        amount=int(request.form.get("amount_ml","0"))
    except:
        amount=0
    amount=max(0,min(3000,amount))
    if amount:
        c=db()
        c.execute("INSERT INTO hydration_logs(user_key,amount_ml,created_at) VALUES(?,?,?)",
                  (user_key(),amount,datetime.now().isoformat(timespec="seconds")))
        c.commit(); c.close()
    return redirect(url_for("meal"))

@app.route("/meal",methods=["GET","POST"])
def meal():
    g=guard_member()
    if g:return g
    p=get_profile()
    if not p:return redirect(url_for("assessment"))
    analysis=None; error=None; form_data={}
    form_token=secrets.token_urlsafe(24)
    if request.method=="POST":
        submitted_token=(request.form.get("form_token") or "").strip()
        if not submitted_token:
            return redirect(url_for("meal"))
        c=db()
        try:
            c.execute("INSERT INTO submission_tokens(token,user_key,created_at) VALUES(?,?,?)",
                      (submitted_token,user_key(),datetime.now().isoformat(timespec="seconds")))
            c.commit()
        except Exception:
            c.close()
            flash("這一餐已經送出分析，不會重複新增。")
            return redirect(url_for("meal"))
        c.close()
        m=membership() or {}; limit=int(m.get("daily_limit") or 6)
        if today_usage()>=limit:
            totals=today_totals(); rem=remaining(p,totals)
            return render_template("meal.html",analysis=None,error=f"今天已達 {limit} 次 AI 分析上限。",p=p,totals=totals,remaining=rem,form_data={},usage=today_usage(),limit=limit,form_token=form_token)
        form_data=request.form.to_dict(); text=request.form.get("content","").strip(); meal_type=request.form.get("meal_type","其他"); timing=request.form.get("timing","before")
        img=request.files.get("photo"); raw=img.read() if img and img.filename else None
        app.logger.info("MEAL_POST user=%s text_chars=%s photo_name=%r photo_bytes=%s content_type=%r",
                        user_key(), len(text), getattr(img,"filename",None), len(raw or b""), getattr(img,"content_type",None))
        # Photo-only and text-only submissions are both valid. Do not consume a token for an empty meal.
        if not text and not raw:
            c=db(); c.execute("DELETE FROM submission_tokens WHERE token=? AND user_key=?",(submitted_token,user_key())); c.commit(); c.close()
            totals=today_totals(); rem=remaining(p,totals)
            return render_template("meal.html",analysis=None,error="請至少上傳一張餐點照片，或輸入餐點內容。",p=p,totals=totals,remaining=rem,form_data=form_data,usage=today_usage(),limit=limit,form_token=secrets.token_urlsafe(24))
        def num(n):
            try:return int(request.form.get(n,"") or 0) or None
            except:return None
        pre_totals=today_totals()
        try:analysis,error=ai_analyze(text,raw,p,timing,num("hunger_before"),num("fullness_after"),pre_totals,meal_type)
        except Exception as e:
            app.logger.exception("AI meal analysis failed")
            error="餐點分析目前暫時無法使用，請稍後再試。文字內容已保留；照片需要重新選擇。"
        if not analysis:
            c=db(); c.execute("DELETE FROM submission_tokens WHERE token=? AND user_key=?",(submitted_token,user_key())); c.commit(); c.close()
            form_token=secrets.token_urlsafe(24)
        if analysis:
            summary=f"整體：{analysis.get('overall','')}\n做得好：{analysis.get('good','')}\n最優先調整：{analysis.get('advice','')}\n本餐後剩餘：{analysis.get('remaining_after','')}\n下一餐：{analysis.get('next_meal','')}"
            c=db(); c.execute("""INSERT INTO meals(user_key,meal_type,source,content,analysis,created_at,hunger_before,fullness_after,water_ml,note,calories,protein_g,carbs_g,fat_g,veg_fists,estimate_note)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(user_key(),meal_type,"photo" if raw else "text",text,summary,datetime.now().isoformat(timespec="seconds"),num("hunger_before"),num("fullness_after"),num("water_ml"),request.form.get("note","").strip(),analysis.get("calories"),analysis.get("protein_g"),analysis.get("carbs_g"),analysis.get("fat_g"),analysis.get("veg_fists"),analysis.get("estimate_note")))
            c.commit(); c.close()
    totals=today_totals(); rem=remaining(p,totals)
    return render_template("meal.html",analysis=analysis,error=error,p=p,totals=totals,remaining=rem,form_data=form_data,usage=today_usage(),limit=int((membership() or {}).get("daily_limit") or 6),form_token=form_token)

def tracked_days(rows):
    return len({r["created_at"][:10] for r in rows})

@app.route("/week")
def week():
    g=guard_member()
    if g:return g
    p=get_profile()
    if not p:return redirect(url_for("assessment"))
    since=(datetime.now()-timedelta(days=7)).isoformat(); c=db()
    rows=c.execute("SELECT * FROM meals WHERE user_key=? AND created_at>=? AND deleted_at IS NULL ORDER BY created_at DESC",(user_key(),since)).fetchall()
    water_rows=c.execute("SELECT * FROM hydration_logs WHERE user_key=? AND created_at>=? ORDER BY created_at DESC",(user_key(),since)).fetchall()
    c.close()
    return render_template("week.html",rows=rows,water_rows=water_rows,p=p)

@app.route("/meal/<int:meal_id>/delete",methods=["POST"])
def delete_meal(meal_id):
    g=guard_member()
    if g:return g
    c=db()
    c.execute("UPDATE meals SET deleted_at=? WHERE id=? AND user_key=? AND deleted_at IS NULL",
              (datetime.now().isoformat(timespec="seconds"),meal_id,user_key()))
    c.commit(); c.close()
    flash("已刪除這筆餐點，今日累計已重新計算。")
    return redirect(request.form.get("next") or url_for("week"))

@app.route("/admin/client/<uid>/meal/<int:meal_id>/delete",methods=["POST"])
def admin_delete_meal(uid,meal_id):
    if not admin_ok(): return "Unauthorized",401
    c=db()
    c.execute("UPDATE meals SET deleted_at=? WHERE id=? AND user_key=? AND deleted_at IS NULL",
              (datetime.now().isoformat(timespec="seconds"),meal_id,"line-"+uid))
    c.commit(); c.close()
    return redirect(url_for("admin_client",uid=uid,token=request.form.get("token")))

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
      (SELECT COUNT(*) FROM meals x WHERE x.user_key='line-'||m.line_user_id AND substr(x.created_at,1,10)=? AND x.deleted_at IS NULL) today_uses
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
    meals=c.execute("SELECT * FROM meals WHERE user_key=? AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 100",("line-"+uid,)).fetchall()
    water_logs=c.execute("SELECT * FROM hydration_logs WHERE user_key=? ORDER BY created_at DESC LIMIT 100",("line-"+uid,)).fetchall()
    m=c.execute("SELECT * FROM memberships WHERE line_user_id=?",(uid,)).fetchone(); c.close()
    return render_template("admin_client.html",p=p,meals=meals,water_logs=water_logs,m=m,token=request.args.get("token"))

@app.route("/health")
def health(): return {"status":"ok","version":"meal-coach-1.6.5","database":"postgres" if USE_POSTGRES else "sqlite"}

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",5000)),debug=True)
