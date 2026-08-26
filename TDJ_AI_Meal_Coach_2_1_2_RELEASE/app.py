
import os, json, sqlite3, base64, requests, time, secrets
try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from openai import OpenAI

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "tdj-v2-change-me")
DB = os.getenv("DB_PATH", "tdj_v2.db")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

def utc_now_naive():
    """UTC naive timestamp for DB compatibility with existing rows."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

def db_now_iso():
    return utc_now_naive().isoformat(timespec="seconds")

def taipei_now():
    return datetime.now(TAIPEI_TZ)

def taipei_day_utc_bounds(day=None):
    """Return UTC-naive ISO [start,end) for a Taiwan calendar day."""
    d = day or taipei_now().date()
    start_local = datetime(d.year,d.month,d.day,tzinfo=TAIPEI_TZ)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc.isoformat(timespec="seconds"), end_utc.isoformat(timespec="seconds")

def taipei_time(value, fmt="%Y-%m-%d %H:%M"):
    if not value:
        return ""
    try:
        dt=datetime.fromisoformat(str(value))
        # Existing DB rows are UTC-naive. Aware values are normalized as UTC first.
        if dt.tzinfo is None:
            dt=dt.replace(tzinfo=timezone.utc)
        else:
            dt=dt.astimezone(timezone.utc)
        return dt.astimezone(TAIPEI_TZ).strftime(fmt)
    except Exception:
        return str(value).replace("T"," ")[:16]

app.jinja_env.filters["taipei_time"] = taipei_time

def program_status(start_value, plan_days=30):
    if not start_value: return "尚未設定開始日"
    try:
        dt=datetime.fromisoformat(str(start_value))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        start_day=dt.astimezone(TAIPEI_TZ).date()
        n=(taipei_now().date()-start_day).days+1
        total=int(plan_days or 30)
        if n<1:return f"尚未開始｜共{total}天"
        if n>total:return f"已完成{total}天方案｜目前第{n}天"
        return f"第{n}天｜剩餘{max(0,total-n)}天"
    except:return "開始日格式異常"

app.jinja_env.globals["program_status"] = program_status

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
        CREATE TABLE IF NOT EXISTS consultants(
          id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, username TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL, invite_code TEXT UNIQUE NOT NULL, active INTEGER DEFAULT 1, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS meal_photos(
          id BIGSERIAL PRIMARY KEY, meal_id BIGINT NOT NULL, user_key TEXT NOT NULL,
          data_url TEXT NOT NULL, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS consultant_notes(
          id BIGSERIAL PRIMARY KEY, consultant_id BIGINT, line_user_id TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT
        );

        ALTER TABLE meals ADD COLUMN IF NOT EXISTS deleted_at TEXT;
        ALTER TABLE memberships ADD COLUMN IF NOT EXISTS consultant_id BIGINT;
        ALTER TABLE memberships ADD COLUMN IF NOT EXISTS plan_days INTEGER DEFAULT 30;
        ALTER TABLE memberships ADD COLUMN IF NOT EXISTS program_starts_at TEXT;
        ALTER TABLE profiles ADD COLUMN IF NOT EXISTS daily_water_ml DOUBLE PRECISION;
        ALTER TABLE profiles ADD COLUMN IF NOT EXISTS custom_water_ml DOUBLE PRECISION;
        ALTER TABLE profiles ADD COLUMN IF NOT EXISTS protein_factor DOUBLE PRECISION;
        ALTER TABLE profiles ADD COLUMN IF NOT EXISTS calorie_deficit_pct DOUBLE PRECISION;
        ALTER TABLE profiles ADD COLUMN IF NOT EXISTS bmi_calc DOUBLE PRECISION;

        CREATE INDEX IF NOT EXISTS idx_profiles_line_user_id ON profiles(line_user_id);
        CREATE INDEX IF NOT EXISTS idx_meals_user_key_created_at ON meals(user_key, created_at);
        CREATE INDEX IF NOT EXISTS idx_hydration_user_key_created_at ON hydration_logs(user_key, created_at);
        CREATE INDEX IF NOT EXISTS idx_meal_photos_meal_id ON meal_photos(meal_id);
        CREATE INDEX IF NOT EXISTS idx_memberships_consultant ON memberships(consultant_id);
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
    CREATE TABLE IF NOT EXISTS consultants(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, username TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL, invite_code TEXT UNIQUE NOT NULL, active INTEGER DEFAULT 1, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS meal_photos(
      id INTEGER PRIMARY KEY AUTOINCREMENT, meal_id INTEGER NOT NULL, user_key TEXT NOT NULL, data_url TEXT NOT NULL, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS consultant_notes(
      id INTEGER PRIMARY KEY AUTOINCREMENT, consultant_id INTEGER, line_user_id TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT
    );
    """)
    membership_cols={r[1] for r in c.execute("PRAGMA table_info(memberships)").fetchall()}
    for col,typ in [("consultant_id","INTEGER"),("plan_days","INTEGER DEFAULT 30"),("program_starts_at","TEXT")]:
        if col not in membership_cols: c.execute(f"ALTER TABLE memberships ADD COLUMN {col} {typ}")
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

    # Keep default water target aligned with current rule: body weight × 40 ml.
    # If the user has set a custom target, preserve it.
    if not p.get("custom_water_ml") and p.get("weight"):
        expected_water=round((float(p.get("weight") or 0)*40)/100)*100
        expected_water=max(1000,min(5000,expected_water))
        if expected_water and round(float(p.get("daily_water_ml") or 0)) != round(expected_water):
            c=db()
            c.execute("UPDATE profiles SET daily_water_ml=?, updated_at=? WHERE user_key=?",
                      (expected_water,db_now_iso(),user_key()))
            c.commit(); c.close()
            p["daily_water_ml"]=expected_water

    # Keep stored targets aligned with the current goal-specific formula.
    # This also upgrades profiles created by older releases where all goals shared
    # nearly the same calorie/protein logic.
    target_fields=[
        "daily_calories","daily_protein","daily_carbs","daily_fat",
        "daily_water_ml","protein_factor","calorie_deficit_pct","bmi_calc"
    ]
    targets=calculate_targets(p)
    def _different(a,b):
        try:return abs(float(a)-float(b))>0.01
        except:return a!=b
    if any(p.get(k) is None or _different(p.get(k),targets.get(k)) for k in target_fields):
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
                db_now_iso(), user_key()
            )
        )
        c.commit(); c.close()
        p.update({k:targets[k] for k in target_fields})

    return p

def membership():
    u=current_line_user()
    if not u:return None
    c=db(); r=c.execute("SELECT * FROM memberships WHERE line_user_id=?",(u["userId"],)).fetchone(); c.close()
    return dict(r) if r else None

def member_active():
    m=membership()
    if not m or m.get("status")!="active" or not m.get("expires_at"): return False
    try:return datetime.fromisoformat(m["expires_at"])>=utc_now_naive()
    except:return False

def guard_member():
    if not current_line_user(): return redirect(url_for("line_login"))
    if not member_active(): return redirect(url_for("membership_required"))
    return None

def today_usage():
    start_utc,end_utc=taipei_day_utc_bounds(); c=db()
    r=c.execute("SELECT COUNT(*) n FROM meals WHERE user_key=? AND created_at>=? AND created_at<? AND deleted_at IS NULL",(user_key(),start_utc,end_utc)).fetchone(); c.close()
    return int(r["n"] or 0)

def calculate_targets(p):
    """Goal-specific starting targets for general adult body-composition coaching.

    The four goal choices are intentionally different:
    - 減重：以體重下降為主，採較明確但仍保守的熱量赤字。
    - 減脂：採溫和至中度赤字，蛋白質配置高於單純減重，重視保留瘦體組織。
    - 維持體重：接近估算 TDEE，不刻意製造赤字。
    - 增肌改善體態：需搭配訓練；無規律運動時先維持，有運動時才小幅增加熱量。
    These are starting estimates, not medical nutrition therapy.
    """
    sex=p.get("sex","female")
    age=float(p.get("age") or 30); h=float(p.get("height") or 160); w=float(p.get("weight") or 60)
    activity=p.get("activity_level","light"); exercise_days=int(p.get("exercise_days") or 0); goal=p.get("goal","減脂")
    bmr=10*w+6.25*h-5*age+(5 if sex=="male" else -161)
    activity_factor={"low":1.2,"light":1.35,"moderate":1.5,"high":1.7}.get(activity,1.35)
    tdee=bmr*activity_factor
    bmi=w/((h/100)**2) if h else 0
    deficit=0.0; surplus=0.0

    if goal=="減重":
        if bmi and bmi<18.5: deficit=0.0
        elif bmi<24: deficit=0.15
        elif bmi<27: deficit=0.18
        else: deficit=0.20
        calories=tdee*(1-deficit)
        if activity=="low" and exercise_days==0: protein_factor=1.2
        elif exercise_days<=2 and activity in ("low","light"): protein_factor=1.3
        else: protein_factor=1.5
        fat_ratio=0.25
        strategy="以體重下降為主：控制總熱量，同時保留足量蛋白質與基本蔬菜。"
    elif goal=="減脂":
        if bmi and bmi<18.5: deficit=0.0
        elif bmi<24: deficit=0.12
        elif bmi<27: deficit=0.15
        else: deficit=0.18
        calories=tdee*(1-deficit)
        if activity=="low" and exercise_days==0: protein_factor=1.4
        elif exercise_days<=2 and activity in ("low","light"): protein_factor=1.5
        else: protein_factor=1.6
        fat_ratio=0.25
        strategy="以降低脂肪、盡量保留肌肉為主：使用溫和熱量赤字，並提高蛋白質優先度。"
    elif goal=="增肌改善體態":
        if exercise_days<=0:
            surplus=0.0
        elif exercise_days<=2:
            surplus=0.03
        else:
            surplus=0.05
        calories=tdee*(1+surplus)
        if exercise_days<=0: protein_factor=1.6
        elif exercise_days<=2: protein_factor=1.7
        else: protein_factor=1.8
        fat_ratio=0.25
        strategy="以肌肉與體態改善為主：蛋白質較高；有規律訓練才小幅增加熱量，沒有訓練時先接近維持量。"
    else:
        calories=tdee
        if activity=="low" and exercise_days==0: protein_factor=1.0
        elif exercise_days<=2: protein_factor=1.2
        else: protein_factor=1.4
        fat_ratio=0.30
        strategy="以維持目前體重與飲食穩定為主：熱量接近每日總消耗，不刻意製造赤字。"

    # Conservative floor for generic adult self-management. Special populations are handled separately by safety().
    if goal in ("減重","減脂"):
        calories=max(1200 if sex!="male" else 1500, calories)

    protein=protein_factor*w
    fat=max(0.8*w,calories*fat_ratio/9)
    carbs=max(0,(calories-protein*4-fat*9)/4)
    default_water=w*40
    try: custom=float(p.get("custom_water_ml")) if p.get("custom_water_ml") not in (None,"") else None
    except: custom=None
    water=custom if custom and 1000<=custom<=5000 else default_water
    water=max(1000,min(5000,water))
    return {
        "daily_calories":round(calories/10)*10,
        "daily_protein":round(protein),
        "daily_carbs":round(carbs),
        "daily_fat":round(fat),
        "daily_water_ml":round(water/100)*100,
        "protein_factor":protein_factor,
        "calorie_deficit_pct":round(deficit*100),
        "bmi_calc":round(bmi,1),
        "bmr_calc":round(bmr/10)*10,
        "tdee_calc":round(tdee/10)*10,
        "activity_factor":activity_factor,
        "calorie_surplus_pct":round(surplus*100),
        "goal_strategy":strategy,
    }

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
    now=db_now_iso(); c=db()
    c.execute("""INSERT INTO memberships(line_user_id,display_name,status,created_at,updated_at) VALUES(?,?,?,?,?)
      ON CONFLICT(line_user_id) DO UPDATE SET display_name=excluded.display_name,updated_at=excluded.updated_at""",
      (p["userId"],p.get("displayName"),"inactive",now,now));
    invite=session.pop("pending_invite",None)
    if invite:
        con=c.execute("SELECT id FROM consultants WHERE invite_code=? AND active=1",(invite,)).fetchone()
        current=c.execute("SELECT consultant_id FROM memberships WHERE line_user_id=?",(p["userId"],)).fetchone()
        if con and current and not current["consultant_id"]:
            c.execute("UPDATE memberships SET consultant_id=?,updated_at=? WHERE line_user_id=?",(con["id"],now,p["userId"]))
    c.commit(); c.close()
    return redirect(url_for("home"))

@app.route("/membership-required")
def membership_required(): return render_template("membership_required.html",user=current_line_user(),member=membership())

@app.route("/")
def home():
    p=get_profile()
    active=member_active()
    totals=today_totals() if p and active else None
    progress=daily_nutrition_progress(p,totals) if p and active and totals else None
    rem=remaining(p,totals) if p and active and totals else None
    return render_template("home.html",profile=p,line_user=current_line_user(),member=membership(),active=active,totals=totals,progress=progress,remaining=rem,target_meta=calculate_targets(p) if p else None)

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
        data.update(calculate_targets(data)); data["user_key"]=user_key(); now=db_now_iso()
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
        c=db(); c.execute("UPDATE profiles SET custom_water_ml=?, daily_water_ml=?, updated_at=? WHERE user_key=?",(custom,round(custom/100)*100,db_now_iso(),user_key())); c.commit(); c.close()
    return redirect(url_for("result"))

@app.route("/result")
def result():
    g=guard_member()
    if g:return g
    p=get_profile()
    if not p:return redirect(url_for("assessment"))
    return render_template("result.html",p=p,red=safety(p),target_meta=calculate_targets(p))




def _meal_stage(meal_type):
    """Approximate how far through the day the user is from the selected meal label."""
    m=(meal_type or "").strip()
    if m in ("早餐","早午餐"): return "early"
    if m in ("午餐","下午茶","點心"): return "mid"
    if m in ("晚餐","宵夜"): return "late"
    return "unknown"

def confidence_label(value):
    v=(value or "").strip()
    return {"高":"較高","中":"中等","低":"較低"}.get(v, v or "中等")


def daily_nutrition_progress(p, totals, meal_type=None):
    """Whole-day coaching progress.
    Protein/vegetables are active goals; carbs/fat are allocation space, not numbers to force-fill.
    Wording is meal-stage aware so breakfast is not judged like the end of the day.
    """
    protein_now=float(totals.get("protein",0) or 0)
    carbs_now=float(totals.get("carbs",0) or 0)
    fat_now=float(totals.get("fat",0) or 0)
    veg_now=float(totals.get("veg_fists", totals.get("veg",0)) or 0)

    protein_goal=float(p.get("daily_protein",0) or 0)
    carbs_goal=float(p.get("daily_carbs",0) or 0)
    fat_goal=float(p.get("daily_fat",0) or 0)
    veg_goal=3.0
    stage=_meal_stage(meal_type or totals.get("last_meal_type"))

    protein_gap=max(0, protein_goal-protein_now)
    protein_ratio=(protein_now/protein_goal) if protein_goal>0 else 0
    if protein_goal<=0:
        protein_status="無法判斷"; protein_note="尚未設定蛋白質目標。"
    elif protein_ratio>=0.9:
        protein_status="已達建議量"
        protein_note=f"今天蛋白質約 {protein_now:.0f}g，已接近或達到建議量，後續依飢餓感正常安排即可。"
    elif stage=="early" and protein_ratio>=0.25:
        protein_status="目前進度可接受"
        protein_note=f"早餐後累積約 {protein_now:.0f}g，先照正常餐次繼續安排蛋白質即可。"
    elif stage=="mid" and protein_ratio>=0.45:
        protein_status="目前進度可接受"
        protein_note=f"目前累積約 {protein_now:.0f}g，後續餐次持續安排一份蛋白質主菜即可。"
    elif stage=="late" and protein_ratio<0.65:
        protein_status="今日明顯偏少"
        protein_note=f"今天累積約 {protein_now:.0f}g，距離建議量仍差約 {protein_gap:.0f}g；若後續還會進食，優先安排蛋白質來源。"
    elif protein_ratio<0.65:
        protein_status="目前偏少"
        protein_note=f"今天累積約 {protein_now:.0f}g，進度偏慢；下一個正餐優先安排足量的蛋白質主菜。"
    else:
        protein_status="接近目標"
        protein_note=f"今天累積約 {protein_now:.0f}g，後續餐次再安排一些蛋白質即可。"

    carb_gap=max(0, carbs_goal-carbs_now)
    if carbs_goal<=0:
        carbs_status="無法判斷"; carbs_note="尚未設定碳水化合物參考量。"
    elif carbs_now > carbs_goal*1.1:
        carbs_status="偏多"
        carbs_note=f"今天碳水約 {carbs_now:.0f}g，已高於參考配置；後續主食份量先縮小。"
    elif carbs_now >= carbs_goal*0.8:
        carbs_status="已接近今日配置"
        carbs_note=f"今天碳水約 {carbs_now:.0f}g，已接近今日配置；主食依飢餓感安排，不需要刻意補滿。"
    else:
        carbs_status="尚有配置空間"
        carbs_note=f"今天碳水約 {carbs_now:.0f}g，約還有 {carb_gap:.0f}g 配置空間；依飢餓感安排即可，不需要刻意吃到上限。"

    if fat_goal<=0:
        fat_status="無法判斷"; fat_note="尚未設定脂肪參考量。"
    elif fat_now > fat_goal*1.05:
        fat_status="偏多"
        fat_note=f"今天脂肪約 {fat_now:.0f}g，已超過參考量；後續優先低油烹調，炸物、肥肉、堅果與濃醬先跳過。"
    elif fat_now >= fat_goal*0.75:
        fat_status="已接近今日建議量"
        fat_note=f"今天脂肪約 {fat_now:.0f}g，已接近今日建議量；後續不需要特別補脂肪，優先選低油料理。"
    else:
        fat_status="目前可接受"
        fat_note=f"今天脂肪約 {fat_now:.0f}g，目前仍在可接受範圍；目前不需要特別增加油脂。"

    veg_gap=max(0, veg_goal-veg_now)
    if veg_now>=veg_goal:
        veg_status="已達建議量"; veg_note=f"今天蔬菜約 {veg_now:.1f} 拳，已達基本建議量。"
    elif stage=="early" and veg_now<0.5:
        veg_status="今天還沒吃到蔬菜"
        veg_note="早餐目前沒有明顯蔬菜沒關係，午、晚餐記得各安排 1–2 拳。"
    elif stage=="late" and veg_now<2:
        veg_status="今日明顯不足"
        veg_note=f"今天目前約 {veg_now:.1f} 拳蔬菜；若後續還會進食，優先補 1–2 拳。"
    elif veg_now<1:
        veg_status="目前偏少"
        veg_note=f"今天目前約 {veg_now:.1f} 拳蔬菜；下一個正餐優先安排 1–2 拳。"
    else:
        veg_status="目前偏少"
        veg_note=f"今天目前約 {veg_now:.1f} 拳蔬菜，距離約 {veg_goal:.0f} 拳參考量還差約 {veg_gap:.1f} 拳。"

    return {
        "protein":{"current":protein_now,"goal":protein_goal,"status":protein_status,"note":protein_note},
        "carbs":{"current":carbs_now,"goal":carbs_goal,"status":carbs_status,"note":carbs_note},
        "fat":{"current":fat_now,"goal":fat_goal,"status":fat_status,"note":fat_note},
        "veg":{"current":veg_now,"goal":veg_goal,"status":veg_status,"note":veg_note},
    }


def _single_meal_statuses(data):
    """Labels describe this meal only, never whether the whole day hit a target."""
    protein=float(data.get("protein_g",0) or 0)
    carbs=float(data.get("carbs_g",0) or 0)
    fat=float(data.get("fat_g",0) or 0)
    veg=float(data.get("veg_fists",0) or 0)
    data["protein_status"]="本餐有吃到" if protein>=15 else ("有一些" if protein>=8 else "本餐偏少")
    data["carbs_status"]="主食比例較高" if carbs>=60 else ("本餐有主食" if carbs>=20 else "本餐較少")
    data["fat_status"]="油脂較多" if fat>=18 else ("油脂中等" if fat>=8 else "油脂較少")
    data["veg_status"]="本餐足夠" if veg>=1 else ("有一些" if veg>=0.5 else "本餐不足")
    return data


def _meal_good_copy(data):
    p=float(data.get("protein_g",0) or 0); v=float(data.get("veg_fists",0) or 0)
    c=float(data.get("carbs_g",0) or 0); f=float(data.get("fat_g",0) or 0)
    if p>=20 and v>=1:
        return "這餐有同時安排明顯的蛋白質來源和至少 1 拳蔬菜，餐盤結構有顧到兩個重要部分。"
    if v>=1:
        return "這餐有吃到至少 1 拳蔬菜，蔬菜量有顧到。"
    if p>=20 and c<60 and f<18:
        return "這餐有安排明顯的蛋白質來源，而且主食與油脂沒有特別高。"
    return ""

def _meal_overall_copy(data):
    dish=(data.get("dish_name") or "這餐").strip()
    p=float(data.get("protein_g",0) or 0); c=float(data.get("carbs_g",0) or 0)
    f=float(data.get("fat_g",0) or 0); v=float(data.get("veg_fists",0) or 0)
    points=[]
    if c>=60: points.append("主食比例較高")
    elif c>=20: points.append("有安排主食")
    else: points.append("主食量較少")
    if p<12: points.append("蛋白質偏少")
    elif p<25: points.append("有蛋白質來源，但份量屬中等")
    else: points.append("蛋白質份量有顧到")
    if f>=18: points.append("油脂偏高")
    elif f>=8: points.append("油脂中等")
    else: points.append("油脂較低")
    if v<0.5: points.append("幾乎沒有蔬菜")
    elif v<1: points.append("蔬菜偏少")
    else: points.append("有吃到蔬菜")
    return f"{dish}這餐的結構是：" + "、".join(points) + "。"

def _meal_advice_copy(data):
    p=float(data.get("protein_g",0) or 0); c=float(data.get("carbs_g",0) or 0)
    f=float(data.get("fat_g",0) or 0); v=float(data.get("veg_fists",0) or 0)
    if v<0.5 and f>=18:
        return "這餐最明顯的問題是蔬菜缺席、油脂也不低。下一次進食先補 1–2 拳蔬菜，並優先選清蒸、滷、烤或燙的蛋白質來源。"
    if v<0.5:
        return "這餐最明顯缺的是蔬菜。下一次進食先安排 1–2 拳蔬菜，讓今天的蔬菜量補上來。"
    if p<12:
        return "這餐蛋白質偏少。下一次進食優先安排約 1 個掌心以上的肉、魚、豆腐或蛋類。"
    if f>=18:
        return "這餐油脂偏高。下一餐改選低油烹調，炸物、肥肉與濃醬先跳過。"
    if c>=70:
        return "這餐主食比例較高。下一餐先把蛋白質和蔬菜顧好，主食再依飢餓感決定份量。"
    return "這餐整體沒有明顯失衡；下一餐照正常份量，持續把蛋白質與蔬菜顧好即可。"

def daily_priority_summary(progress):
    priorities=[]
    if progress["protein"]["status"] in ("目前偏少","今日明顯偏少"):
        priorities.append("蛋白質")
    if progress["veg"]["status"] in ("今天還沒吃到蔬菜","今日明顯不足","目前偏少"):
        priorities.append("蔬菜")
    if not priorities:
        if progress["fat"]["status"] in ("已接近今日建議量","偏多"):
            return "今天接下來最重要的是控制油脂，其他依飢餓感正常安排。"
        return "今天目前沒有特別需要追趕的項目，照正常餐次與飢餓感安排即可。"
    return "今天接下來優先顧「" + "＋".join(priorities) + "」；" + ("脂肪已接近建議量，後續盡量低油。" if progress["fat"]["status"] in ("已接近今日建議量","偏多") else "其他項目依飢餓感安排即可。")

def next_meal_from_daily_progress(progress, remaining, meal_type=None, timing="before", fullness_after=None):
    """Dynamic client-facing next-meal coaching.

    Decision order is intentional: remaining calories/macros first, then nutrition gaps.
    This prevents the same generic meal template from being shown when today's remaining
    allowance is very different (for example 780 kcal vs 120 kcal).
    """
    try:
        fullness=int(fullness_after) if fullness_after not in (None,"") else None
    except:
        fullness=None

    protein_status=progress["protein"]["status"]
    veg_status=progress["veg"]["status"]
    fat_status=progress["fat"]["status"]
    carb_status=progress["carbs"]["status"]

    cal=max(0,int(round(remaining.get("calories",0) or 0)))
    p=max(0,int(round(remaining.get("protein",0) or 0)))
    c=max(0,int(round(remaining.get("carbs",0) or 0)))
    f=max(0,int(round(remaining.get("fat",0) or 0)))

    veg_need=veg_status in ("今天還沒吃到蔬菜","今日明顯不足","目前偏少")
    # Remaining grams matter too: a status such as「目前進度可接受」does not mean
    # there is no protein gap left for the day.
    protein_need=(protein_status in ("目前偏少","今日明顯偏少","接近目標") or p>=20)
    fat_high=(fat_status in ("已接近今日建議量","偏多") or f<=12)
    carbs_high=(carb_status=="偏多" or c<=5)

    parts=[]
    if timing=="after" and fullness is not None and fullness>=7:
        parts.append("這餐吃完已有飽足感，現在不用再加餐；下一次真的餓了再吃。")
    elif timing=="after":
        parts.append("這餐已經吃完，下一次真的餓了再吃。")

    # 1) Very little room left: never output a normal full-meal template.
    if cal <= 0:
        parts.append("今天的熱量參考空間已用完，不需要為了補足蛋白質或蔬菜再硬吃。")
        parts.append("如果之後真的餓，以無糖飲品、清湯或少量低油蔬菜為主；明天再把蛋白質與蔬菜正常安排回餐盤。")
        return "".join(parts)

    if cal <= 180:
        if carbs_high:
            parts.append(f"今天約只剩 {cal} kcal 的熱量參考空間，而且碳水已達到或超過今天的配置；現在不適合再安排飯、麵、冬粉、地瓜等完整主食。")
        else:
            parts.append(f"今天約只剩 {cal} kcal 的熱量參考空間，不需要再安排完整一餐。")
        choices=[]
        if protein_need and f>0:
            choices.append("無糖豆漿小份、茶葉蛋／蛋白、嫩豆腐等低脂蛋白質")
        if veg_need:
            choices.append("燙青菜或清湯蔬菜")
        if choices:
            parts.append("如果晚一點真的餓，可以從"+"或".join(choices)+"擇一小份即可。")
        else:
            parts.append("如果晚一點真的餓，再選一份清淡的小點心即可。")
        if fat_high:
            parts.append("今天脂肪也已接近建議量，炸物、肥肉、堅果與濃醬先跳過。")
        parts.append("今天沒補滿的營養不用硬追，明天恢復正常餐盤即可。")
        return "".join(parts)

    # 2) Limited room: light meal, and suppress starch when carbs are exhausted.
    if cal <= 300:
        target=[]
        if protein_need: target.append("一小份低脂蛋白質")
        if veg_need: target.append("1–2拳蔬菜")
        if not carbs_high and c>=20: target.append("少量主食")
        parts.append(f"今天約還有 {cal} kcal 的參考空間，下一次若餓，以"+("＋".join(target) if target else "清淡小份量")+"為主，不需要吃完整套餐。")
        if carbs_high: parts.append("今天主食先不特別補。")
        if fat_high: parts.append("炸物、肥肉、堅果與濃醬先跳過。")
        parts.append("剩餘數字只是參考，不需要硬吃滿。")
        return "".join(parts)

    # 3) Enough room for a real meal: build the meal from the actual remaining macros.
    target=[]
    if protein_need:
        protein_target=min(35,p)
        protein_low=min(25,protein_target)
        if protein_target>=20:
            target.append(f"蛋白質約 {protein_low}–{protein_target}g" if protein_target>protein_low else f"蛋白質約 {protein_target}g")
    if veg_need: target.append("蔬菜 1–2拳")
    if carbs_high:
        target.append("今天主食先不特別補")
    elif c>=30:
        target.append("主食約半碗–1碗，依飢餓感調整")
    elif c>5:
        target.append("主食抓小份")
    if fat_high: target.append("烹調以清蒸、滷、烤、燙為主")
    parts.append("下一餐重點："+("、".join(target) if target else "照正常份量吃，依飢餓感安排")+"。")

    # Detailed shopping/restaurant examples are intentionally shown only in the
    # separate「外食下一餐指南」UI below. Keep this paragraph strategy-only.
    if p < 20 and protein_need:
        parts.append("蛋白質仍有缺口，但以實際飢餓感與剩餘熱量安排，不需要為了追數字額外加餐。")
    if fat_high:
        parts.append("今天油脂已接近配置，後續料理優先低油。")
    parts.append("剩餘數字只是參考，不需要為了把數字吃滿而硬吃。")
    return "".join(parts)



def external_next_meal_guides(progress, remaining, timing="before", fullness_after=None, goal="減脂"):
    """Return clearly separated, client-facing eating-out options.

    Keep presentation separate from next_meal_from_daily_progress so decision logic
    can change without removing the eating-out guide UI. Each item is one category.
    """
    try:
        fullness=int(fullness_after) if fullness_after not in (None,"") else None
    except:
        fullness=None

    cal=max(0,int(round(remaining.get("calories",0) or 0)))
    c=max(0,int(round(remaining.get("carbs",0) or 0)))
    f=max(0,int(round(remaining.get("fat",0) or 0)))
    p=max(0,int(round(remaining.get("protein",0) or 0)))

    protein_status=(progress.get("protein") or {}).get("status","")
    veg_status=(progress.get("veg") or {}).get("status","")
    fat_status=(progress.get("fat") or {}).get("status","")
    carb_status=(progress.get("carbs") or {}).get("status","")

    protein_need=(protein_status in ("目前偏少","今日明顯偏少","接近目標") or p>=20)
    veg_need=veg_status in ("今天還沒吃到蔬菜","今日明顯不足","目前偏少")
    # Goal mode affects the emphasis, while the actual gram caps still come from today's target.
    preserve_protein = goal in ("減脂","增肌改善體態")
    fat_high=(fat_status in ("已接近今日建議量","偏多") or f<=12)
    carbs_high=(carb_status=="偏多" or c<=5)

    # If already full after the meal, guides remain available for later, but copy
    # explicitly says there is no need to eat now.
    prefix = "現在不餓就不用吃；真的餓了再照這個方向選。" if timing=="after" and fullness is not None and fullness>=7 else "真的餓了再從下面挑一種，不需要為了把數字吃滿而硬吃。"
    if goal=="減脂":
        prefix += " 減脂模式優先保留蛋白質與蔬菜，主食依今天剩餘空間決定。"
    elif goal=="減重":
        prefix += " 減重模式先守住總熱量，蛋白質與蔬菜仍要顧到。"
    elif goal=="維持體重":
        prefix += " 維持模式以均衡與飢餓感為主，不需要刻意壓低主食。"
    elif goal=="增肌改善體態":
        prefix += " 增肌改善體態模式優先蛋白質；有訓練且仍有碳水空間時可保留主食。"

    guides=[]
    if cal <= 0:
        return [{"title":"今天先不用再安排正餐","text":"今天的熱量參考空間已用完。若之後真的餓，可先選無糖飲品、清湯或少量低油蔬菜；明天再恢復正常餐盤。"}]

    if cal <= 180:
        guides.append({"title":"超商","text":"無糖豆漿小份、茶葉蛋／蛋白、嫩豆腐擇1樣；若還需要蔬菜，可搭少量沙拉或關東煮蔬菜。今天先不加飯糰、麵包、地瓜等主食。" if carbs_high else "無糖豆漿小份、茶葉蛋／蛋白、嫩豆腐擇1樣；可搭少量沙拉或關東煮蔬菜。"})
        guides.append({"title":"自助餐／便當店","text":"若真的餓，選1份燙青菜或少量清淡豆腐／瘦肉即可，不需要再點完整便當；飯、麵先跳過。" if carbs_high else "若真的餓，選1份燙青菜＋少量清淡豆腐／瘦肉即可，不需要再點完整便當。"})
        guides.append({"title":"滷味／鹽水雞","text":"青菜1–2樣＋豆腐或蛋擇1樣，醬少、不額外淋油；麵、冬粉、甜不辣等主食類先不加。" if carbs_high else "青菜1–2樣＋豆腐或蛋擇1樣，醬少、不額外淋油。"})
        guides.append({"title":"湯品","text":"清湯、蔬菜湯、蛋花湯擇一小份，避開濃湯與勾芡湯。"})
        if fat_high:
            guides.append({"title":"今天先避開","text":"炸物、肥肉、堅果大量攝取、濃醬與額外淋油先跳過。"})
        return [{"title":"這次怎麼選","text":prefix}]+guides

    if cal <= 300:
        guides.append({"title":"超商","text":"雞胸、茶葉蛋或無糖豆漿擇1–2樣＋沙拉／關東煮蔬菜；" + ("今天主食先不特別補。" if carbs_high else "真的很餓再加小地瓜或小份主食。")})
        guides.append({"title":"自助餐／便當店","text":"選小份低脂蛋白質＋1–2樣青菜；" + ("不另外配飯或麵。" if carbs_high else "主食抓少量即可。")})
        guides.append({"title":"滷味／鹽水雞","text":"豆腐、蛋或少量雞肉＋2種蔬菜，少醬、不淋油；" + ("麵、冬粉先不加。" if carbs_high else "有需要再加一小份主食。")})
        if fat_high:
            guides.append({"title":"今天先避開","text":"炸物、肥肉、堅果大量攝取與濃醬先跳過，烹調以清蒸、滷、烤、燙為主。"})
        return [{"title":"這次怎麼選","text":prefix}]+guides

    rice = "今天先不另外配飯／麵" if carbs_high else ("主食抓小份" if c<30 else "飯約半碗到1碗，依飢餓感調整")
    protein = ("一份足量雞肉、魚、瘦肉、豆腐或蛋" if preserve_protein else "一份雞肉、魚、瘦肉、豆腐或蛋") if protein_need else "原本主菜正常份量"
    veg = "2樣青菜" if veg_need else "至少1樣青菜"

    guides.append({"title":"便當／自助餐","text":f"{protein}＋{veg}＋{rice}。" + ("炸排骨、肥肉、勾芡與濃醬先跳過。" if fat_high else "")})
    guides.append({"title":"超商","text":("雞胸肉＋茶葉蛋／無糖豆漿擇一，再搭沙拉或關東煮蔬菜；" if protein_need else "沙拉／關東煮蔬菜搭一份蛋白質；") + ("今天先不另外加飯糰、麵包或地瓜。" if carbs_high else "主食依飢餓感與今天剩餘碳水決定。")})
    guides.append({"title":"滷味／鹽水雞","text":"雞肉、豆腐或蛋擇2種＋2–3種蔬菜，少醬、不額外淋油；" + ("麵、冬粉先不加。" if carbs_high else "有碳水空間再加一小份主食。")})
    guides.append({"title":"火鍋","text":"先拿2拳蔬菜＋一份肉／魚／豆腐，丸餃少量；" + ("今天主食先不加。" if carbs_high else "飯、麵、冬粉只選一種，依飢餓感抓份量。")})
    if not carbs_high:
        guides.append({"title":"麵店","text":"麵食可抓小份，另外加燙青菜＋蛋／豆干／瘦肉；若今天後續碳水空間變少，就改湯菜＋蛋白質。"})
    if fat_high:
        guides.append({"title":"今天先避開","text":"炸物、肥肉、濃醬、額外淋油與大量堅果先跳過；優先清蒸、滷、烤、燙。"})
    return guides

def next_meal_guidance(rem, meal_type=None, timing="before", fullness_after=None):
    cal=max(0,int(round(rem.get("calories",0) or 0)))
    p=max(0,int(round(rem.get("protein",0) or 0)))
    c=max(0,int(round(rem.get("carbs",0) or 0)))
    f=max(0,int(round(rem.get("fat",0) or 0)))
    meal_type=meal_type or ""
    try: fullness=int(fullness_after) if fullness_after not in (None,"") else None
    except: fullness=None

    if cal <= 0:
        return "今天的熱量參考額度已用完；如果仍然餓，先以無糖飲品、清湯或少量低熱量蔬菜為主，不用因為數字還有空間就勉強進食。"

    # If dinner/supper is already eaten and user is full, avoid prescribing another full meal.
    if timing=="after" and fullness is not None and fullness>=7 and meal_type in ("晚餐","宵夜"):
        parts=["你已經吃完而且飽足度高，現在不用再補吃。"]
        if p>0 or c>0 or f>0:
            parts.append(f"今天目前約還有 {cal} kcal 的參考空間，但不代表一定要吃完。")
        if f <= 8:
            parts.append("如果晚一點真的餓，優先選低脂蛋白質或蔬菜，例如無糖豆漿、茶葉蛋、嫩豆腐、燙青菜；先避開炸物、堅果與濃醬。")
        else:
            parts.append("如果晚一點真的餓，再依飢餓感補一份小點心即可。")
        return "".join(parts)

    # Choose a next-meal calorie range that never exceeds remaining calories.
    hi=min(cal, 500)
    if hi < 200:
        return f"今天約只剩 {cal} kcal 的參考空間；下一次進食以小份量為主，不需要再安排完整一餐。"

    lo=min(300, max(180, int(round(hi*0.65/10)*10)))
    if lo > hi: lo=hi

    p_hi=min(p, 30)
    p_lo=min(p_hi, 20) if p_hi>=20 else max(0,p_hi)
    c_hi=min(c, 60)
    c_lo=min(c_hi, 30) if c_hi>=30 else max(0,c_hi)
    f_hi=min(f, 12)
    f_lo=min(f_hi, 5) if f_hi>=5 else max(0,f_hi)

    ranges=[f"熱量約 {lo}–{hi} kcal"]
    if p_hi>0: ranges.append(f"蛋白質約 {p_lo}–{p_hi}g")
    if c_hi>0: ranges.append(f"碳水約 {c_lo}–{c_hi}g")
    if f_hi>0: ranges.append(f"脂肪不超過約 {f_hi}g")

    if f <= 8:
        example="優先低脂蛋白質＋蔬菜；例如烤/滷雞胸、白肉魚、豆腐搭配1–2拳青菜，主食依剩餘碳水少量安排。"
    elif p >= 20 and c >= 30:
        example="例如自助餐選一掌心肉/魚＋1–2拳青菜＋半碗左右主食；或超商選雞胸/茶葉蛋＋沙拉＋地瓜。"
    elif p >= 20:
        example="優先補蛋白質與蔬菜，例如雞胸、魚、豆腐、蛋搭配青菜；主食依飢餓感少量即可。"
    else:
        example="依飢餓感安排小份均衡餐，優先蔬菜與清淡烹調，不需要把剩餘額度全部吃完。"

    return "下一次進食建議抓："+"、".join(ranges)+"。"+example+" 剩餘額度只是今天的參考上限，不代表一定要全部吃完。"

def ai_analyze(text=None,image_bytes_list=None,p=None,timing="before",hunger_before=None,fullness_after=None,totals=None,meal_type=None):
    import time
    started=time.perf_counter()
    key=os.getenv("OPENAI_API_KEY")
    if not key:return None,"AI 尚未啟用。請先設定 OPENAI_API_KEY。"
    client=OpenAI(api_key=key, timeout=60.0)
    totals=totals or {"calories":0,"protein":0,"carbs":0,"fat":0,"water":0}
    images=image_bytes_list or []

    rem={
      "calories":max(0,(p.get("daily_calories") or 0)-totals.get("calories",0)),
      "protein":max(0,(p.get("daily_protein") or 0)-totals.get("protein",0)),
      "carbs":max(0,(p.get("daily_carbs") or 0)-totals.get("carbs",0)),
      "fat":max(0,(p.get("daily_fat") or 0)-totals.get("fat",0))
    }

    prompt=f"""你是 TDJ AI 餐盤教練。所有上傳照片都屬於同一餐，可能是同一盤不同角度，也可能是不同盤、飲料、湯、小菜。先判斷重複角度，避免重複計算；不同盤則合併成同一餐。

主要體態目標：{p.get('goal','減脂')}。
目標策略：{calculate_targets(p).get('goal_strategy','')}
每日目標：{p.get('daily_calories')} kcal，蛋白質 {p.get('daily_protein')}g，碳水 {p.get('daily_carbs')}g，脂肪 {p.get('daily_fat')}g。
本餐前今日累計：{totals.get('calories',0)} kcal，蛋白質 {totals.get('protein',0)}g，碳水 {totals.get('carbs',0)}g，脂肪 {totals.get('fat',0)}g。
本餐前剩餘：約 {rem['calories']:.0f} kcal，蛋白質 {rem['protein']:.0f}g，碳水 {rem['carbs']:.0f}g，脂肪 {rem['fat']:.0f}g。
餐別：{meal_type or '未填'}；分析時點：{'還沒吃' if timing=='before' else '已吃完'}；餐前飢餓：{hunger_before or '未填'}/10；餐後飽足：{fullness_after or '未填'}/10。

估算規則：
1. 不要直接憑菜名吐單點熱量。先拆成「互不重複」的組成：主食、主菜蛋白質、蔬菜、額外醬汁/勾芡/油脂、飲料、小菜。
2. components 每一列的熱量必須互斥，不能重複計算。例如：
   - 如果「白飯」只算飯本體，就可另外列「燴汁/勾芡/油脂」；
   - 如果某一列已寫「白飯＋燴汁」，就不能再額外列一筆同一份燴汁/油脂熱量。
3. 每個主要組成先估份量範圍，再估熱量範圍；所有 components 的低值加總應接近 calories_low，高值加總應接近 calories_high，中心值則落在兩者之間。
4. 外食看不出重量、油、糖、勾芡時，不能採過度樂觀的低值。尤其主菜份量看不清楚時，應使用「常見外食份量中位值」而不是最低值。
5. 對牛三寶、牛腩、控肉、滷肉、五花肉、豬腳、炸排骨、咖哩肉、丼飯肉量等，照片看不清重量時，主菜份量範圍要保守：
   - 一般有明顯一份主菜：通常不要低估到 50–60g 以下；
   - 看起來接近完整便當主菜：可抓約 80–150g 起跳；
   - 若畫面明顯有多塊主菜，不能只用『少量配料』估法。
6. 炒飯、燴飯、咖哩飯、滷肉飯、鍋燒、濃醬、炸物、牛三寶、五花肉、加工肉要把烹調油/醬汁/較高脂肪的不確定性算進去。
7. 使用者若輸入克數、包裝營養標示或明確份量，優先使用文字縮小範圍。
8. 多張照片要先去重，不能同一盤算兩次。
9. 不要假裝照片可以精準到個位數。中心值以約 10 kcal / 5g 粒度合理。
10. 已吃完且飽足度>=7，不叫使用者立刻補吃；把調整放到下一餐。
11. 下一餐建議不得超過今天真正剩餘額度。
10. 蔬菜拳數只計算「可實際當作一份蔬菜吃下去的量」。蔥花、蒜末、香菜、洋蔥碎、醬汁中的零星碎菜、裝飾葉菜都不要算蔬菜。
11. 如果只有零星配料、看不到明顯蔬菜份量，veg_fists 必須回 0，不要回 0.1、0.2、0.3。
12. 蔬菜拳數只用 0、0.5、1、1.5、2、2.5... 這種 0.5 拳級距。
13. overall、good、advice 不可重複同一件事：overall只講本餐結構；good只講一個優點；advice只講一個最優先動作。
14. 今日蛋白質、碳水、脂肪、蔬菜累計與缺口會由程式另外顯示，所以 overall、good、advice 不要再複述「今天累計多少、還差多少」。
15. 文字要像真人體態教練：短、直接、可執行；每個欄位最多1–2句。不要使用「營養層次更完整」「吃得順口」這種空泛稱讚。
16. good 只有在真的有值得肯定的飲食行為時才寫，而且要講具體食物與具體原因。多種肉類不等於更均衡；不要因為有牛肉、牛筋、牛肚就硬說更完整。若只是一般搭配或沒有明顯優點，good 請輸出空字串。
17. 不要用「補到」、「硬補」、「吃滿」來描述蛋白質目標；改用「優先安排」、「今天仍偏少」、「後續再安排一些」等自然說法。
18. 單餐 status 只描述這一餐的結構，不代表全天是否達標：protein_status=本餐有沒有明顯蛋白質來源；carbs_status=本餐主食比例；fat_status=本餐油脂程度；veg_status=本餐蔬菜量。
19. overall 不要寫「澱粉量明顯偏多／碳水明顯偏多」這種容易和全天目標混淆的句子；若只是這餐飯量高，改寫「這餐以主食為主／主食比例較高」。
20. 「本餐主食比例較高」不等於「今天碳水超標」；全天判斷由程式另外計算。
21. 不要把「配菜少」寫成蛋白質問題。蛋白質來源與蔬菜/配菜量要分開評論。
22. overall 優先用「主食比例、蛋白質來源、蔬菜量、油脂」描述，不要用模糊的好/壞評語。

只輸出 JSON：
{{
"dish_name":"餐點名稱",
"components":[{{"name":"組成","portion":"份量範圍","calories_low":數字,"calories_high":數字,"note":"估算依據"}}],
"calories_low":數字,"calories_estimate":數字,"calories_high":數字,
"protein_g":數字,"carbs_g":數字,"fat_g":數字,"veg_fists":數字,
"confidence":"高/中/低",
"protein_status":"本餐有吃到/有一些/本餐偏少",
"carbs_status":"主食比例較高/本餐有主食/本餐較少",
"fat_status":"油脂較多/油脂中等/油脂較少",
"veg_status":"本餐足夠/有一些/本餐不足",
"overall":"只評論這一餐本身，1句；不要重複全天累計、剩餘目標或下一餐資訊",
"good":"只講這一餐做得好的一個重點，1句；不要加入今天累計還差多少",
"advice":"只給這一餐最值得調整的一個動作，1句；不要重複全天數字",
"next_meal":"留空字串；下一餐由程式依全天進度產生",
"estimate_note":"照片估算限制"
}}"""
    content=[{"type":"input_text","text":prompt+"\n餐點文字補充："+(text or "無")}]
    prep=time.perf_counter()
    for raw in images[:5]:
        b64=base64.b64encode(raw).decode()
        content.append({"type":"input_image","image_url":"data:image/jpeg;base64,"+b64})
    app.logger.info("PHOTO_PREP images=%s elapsed=%.3fs",len(images),time.perf_counter()-prep)

    req=time.perf_counter()
    try:
        resp=client.responses.create(
            model=os.getenv("OPENAI_MODEL","gpt-5.4-mini"),
            input=[{"role":"user","content":content}],
            max_output_tokens=1200
        )
    except Exception:
        app.logger.exception("AI_ANALYZE_ERROR")
        return None,"餐點分析暫時無法完成，請稍後再試。"
    app.logger.info("OPENAI_REQUEST elapsed=%.3fs",time.perf_counter()-req)

    txt=(getattr(resp,"output_text","") or "").strip()
    if not txt:
        app.logger.error("AI_EMPTY_OUTPUT status=%s",getattr(resp,"status",None))
        return None,"餐點分析暫時無法完成，請稍後再試。"
    if txt.startswith("```"):
        txt=txt.strip("`")
        if txt.startswith("json"):txt=txt[4:].lstrip()
    try:
        data=json.loads(txt)
    except Exception:
        app.logger.exception("AI_JSON_PARSE_ERROR raw=%r",txt[:1000])
        return None,"餐點分析暫時無法完成，請稍後再試。"

    for k in ["calories_low","calories_estimate","calories_high","protein_g","carbs_g","fat_g","veg_fists"]:
        try:data[k]=float(data.get(k,0) or 0)
        except:data[k]=0.0

    # Vegetable amount: tiny garnish/seasoning does not count as a vegetable serving.
    vf=max(0,float(data.get("veg_fists",0) or 0))
    if vf < 0.5:
        vf=0.0
    else:
        vf=round(vf*2)/2
    data["veg_fists"]=vf
    if vf==0:
        data["veg_status"]="偏少"

    # Keep single-meal labels separate from whole-day progress labels and generate
    # deterministic client-facing coaching copy to avoid vague/contradictory AI wording.
    data=_single_meal_statuses(data)
    data["overall"]=_meal_overall_copy(data)
    data["good"]=_meal_good_copy(data)
    data["advice"]=_meal_advice_copy(data)

    low=max(0,data["calories_low"]); est=max(0,data["calories_estimate"]); high=max(low,data["calories_high"])
    est=min(max(est,low),high)

    # Cross-check center kcal against macros. Photo estimates can differ because of fiber, sauces and rounding,
    # but a large mismatch should not be shown to clients as contradictory numbers.
    macro_kcal=data["protein_g"]*4 + data["carbs_g"]*4 + data["fat_g"]*9
    if macro_kcal>0 and est>0:
        gap=abs(est-macro_kcal)/max(est,macro_kcal)
        if gap>0.12:
            if low <= macro_kcal <= high:
                est=(est+macro_kcal)/2
            else:
                low=min(low,macro_kcal*0.9)
                high=max(high,macro_kcal*1.1)
            data["estimate_note"]=(data.get("estimate_note","")+" 熱量與三大營養素已做一致性校正。").strip()

    data["calories_low"]=round(low/10)*10
    data["calories_estimate"]=round(est/10)*10
    data["calories_high"]=round(high/10)*10
    data["calories"]=data["calories_estimate"]

    if images and not text and data["calories_estimate"]>0:
        min_width=max(100,data["calories_estimate"]*0.20)
        if data["calories_high"]-data["calories_low"]<min_width:
            data["calories_low"]=max(0,round((data["calories_estimate"]-min_width/2)/10)*10)
            data["calories_high"]=round((data["calories_estimate"]+min_width/2)/10)*10


    # Component accounting consistency:
    # components are expected to be mutually exclusive. When the component sum
    # meaningfully disagrees with the meal range, widen the meal range instead
    # of presenting contradictory numbers.
    comps=data.get("components") or []
    comp_low=0.0
    comp_high=0.0
    valid_components=0
    for comp in comps:
        try:
            cl=max(0,float(comp.get("calories_low",0) or 0))
            ch=max(cl,float(comp.get("calories_high",0) or 0))
            comp["calories_low"]=round(cl/10)*10
            comp["calories_high"]=round(ch/10)*10
            comp_low += comp["calories_low"]
            comp_high += comp["calories_high"]
            valid_components += 1
        except:
            pass

    if valid_components:
        comp_low=round(comp_low/10)*10
        comp_high=round(comp_high/10)*10
        # If component totals fall clearly outside the declared whole-meal range,
        # align the meal range with the non-overlapping component decomposition.
        if comp_low > data["calories_low"]*1.15:
            data["calories_low"]=comp_low
        if comp_high > data["calories_high"]*1.15:
            data["calories_high"]=comp_high

        # Keep center inside the revised range.
        data["calories_estimate"]=min(
            max(data["calories_estimate"], data["calories_low"]),
            data["calories_high"]
        )
        data["calories_estimate"]=round(data["calories_estimate"]/10)*10
        data["calories"]=data["calories_estimate"]

    after={
      "calories":round(totals.get("calories",0)+data["calories_estimate"]),
      "protein":round(totals.get("protein",0)+data["protein_g"]),
      "carbs":round(totals.get("carbs",0)+data["carbs_g"]),
      "fat":round(totals.get("fat",0)+data["fat_g"]),
      "veg":round((totals.get("veg",0) or 0)+data["veg_fists"],1)
    }
    remaining_after={
      "calories":max(0,round((p.get("daily_calories") or 0)-after["calories"])),
      "protein":max(0,round((p.get("daily_protein") or 0)-after["protein"])),
      "carbs":max(0,round((p.get("daily_carbs") or 0)-after["carbs"])),
      "fat":max(0,round((p.get("daily_fat") or 0)-after["fat"]))
    }
    data["today_after"]=after
    data["remaining_after"]=remaining_after

    # Whole-day progress is calculated once below using the body-composition coaching rules.
    # Do not trust a free-form AI range here. Python enforces today's true remaining caps.
    daily_totals_after={
        "calories":after["calories"],
        "protein":after["protein"],
        "carbs":after["carbs"],
        "fat":after["fat"],
        "veg_fists":round((totals.get("veg",0) or 0)+(data.get("veg_fists",0) or 0),1)
    }
    progress=daily_nutrition_progress(p,daily_totals_after,meal_type=meal_type)
    data["daily_progress"]=progress
    data["daily_priority"]=daily_priority_summary(progress)
    data["next_meal"]=next_meal_from_daily_progress(
        progress,
        remaining_after,
        meal_type=meal_type,
        timing=timing,
        fullness_after=fullness_after
    )
    data["next_meal_guides"]=external_next_meal_guides(
        progress,
        remaining_after,
        timing=timing,
        fullness_after=fullness_after,
        goal=p.get("goal","減脂")
    )

    app.logger.info("TOTAL_ANALYSIS elapsed=%.3fs",time.perf_counter()-started)
    return data,None

def today_totals():
    start_utc,end_utc=taipei_day_utc_bounds(); c=db()
    rows=c.execute("SELECT calories,protein_g,carbs_g,fat_g,veg_fists,water_ml,meal_type,created_at FROM meals WHERE user_key=? AND created_at>=? AND created_at<? AND deleted_at IS NULL ORDER BY created_at",(user_key(),start_utc,end_utc)).fetchall()
    water_rows=c.execute("SELECT amount_ml FROM hydration_logs WHERE user_key=? AND created_at>=? AND created_at<?",(user_key(),start_utc,end_utc)).fetchall()
    c.close()
    last_meal_type=(rows[-1]["meal_type"] if rows else None)
    return {
        "calories":round(sum((r["calories"] or 0) for r in rows),1),
        "protein":round(sum((r["protein_g"] or 0) for r in rows),1),
        "carbs":round(sum((r["carbs_g"] or 0) for r in rows),1),
        "fat":round(sum((r["fat_g"] or 0) for r in rows),1),
        "veg":round(sum((r["veg_fists"] or 0) for r in rows),1),
        "water":int(sum((r["water_ml"] or 0) for r in rows) + sum((r["amount_ml"] or 0) for r in water_rows)),
        "meal_count":len(rows),
        "last_meal_type":last_meal_type
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
                  (user_key(),amount,db_now_iso()))
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
                      (submitted_token,user_key(),db_now_iso()))
            c.commit()
        except Exception:
            c.close()
            flash("這一餐已經送出分析，不會重複新增。")
            return redirect(url_for("meal"))
        c.close()
        m=membership() or {}; limit=int(m.get("daily_limit") or 6)
        if today_usage()>=limit:
            totals=today_totals(); rem=remaining(p,totals)
            return render_template("meal.html",analysis=None,error=f"今天已達 {limit} 次 AI 分析上限。",p=p,totals=totals,remaining=rem,today_progress=daily_nutrition_progress(p,totals),target_meta=calculate_targets(p),form_data={},usage=today_usage(),limit=limit,form_token=form_token)
        form_data=request.form.to_dict(); text=request.form.get("content","").strip(); meal_type=request.form.get("meal_type","其他"); timing=request.form.get("timing","before")
        imgs=request.files.getlist("photos")
        if not imgs:
            one=request.files.get("photo")
            imgs=[one] if one else []
        raws=[]
        for img in imgs[:5]:
            if img and img.filename:
                b=img.read()
                if b: raws.append(b)
        raw=raws[0] if raws else None
        first_img=next((x for x in imgs if x and getattr(x,"filename",None)),None)
        app.logger.info("MEAL_POST user=%s text_chars=%s photo_count=%s first_photo=%r first_photo_bytes=%s content_type=%r",
                        user_key(), len(text), len(raws), getattr(first_img,"filename",None),
                        len(raw or b""), getattr(first_img,"content_type",None))
        # Photo-only and text-only submissions are both valid. Do not consume a token for an empty meal.
        if not text and not raw:
            c=db(); c.execute("DELETE FROM submission_tokens WHERE token=? AND user_key=?",(submitted_token,user_key())); c.commit(); c.close()
            totals=today_totals(); rem=remaining(p,totals)
            return render_template("meal.html",analysis=None,error="請至少上傳一張餐點照片，或輸入餐點內容。",p=p,totals=totals,remaining=rem,today_progress=daily_nutrition_progress(p,totals),target_meta=calculate_targets(p),form_data=form_data,usage=today_usage(),limit=limit,form_token=secrets.token_urlsafe(24))
        def num(n):
            try:return int(request.form.get(n,"") or 0) or None
            except:return None
        pre_totals=today_totals()
        try:
            analysis,error=ai_analyze(text,raws,p,timing,num("hunger_before"),num("fullness_after"),pre_totals,meal_type)
            if analysis:
                analysis["source_label"]="照片估算" if raws else "文字估算"
        except Exception as e:
            app.logger.exception("AI meal analysis failed")
            error="餐點分析目前暫時無法使用，請稍後再試。文字內容已保留；照片需要重新選擇。"
        if not analysis:
            c=db(); c.execute("DELETE FROM submission_tokens WHERE token=? AND user_key=?",(submitted_token,user_key())); c.commit(); c.close()
            form_token=secrets.token_urlsafe(24)
        if analysis:
            # Store a detailed snapshot so the client can later understand what this meal looked like
            # AND what the whole-day state was immediately after this meal.
            def fmtveg(v):
                v=float(v or 0)
                return str(int(v)) if v.is_integer() else str(v)
            dp=analysis.get("daily_progress") or {}
            history_parts=[
                f"當時體態目標：{p.get('goal','減脂')}",
                f"餐點判斷：{analysis.get('overall','')}",
                f"本餐估算：約 {analysis.get('calories_estimate',analysis.get('calories',0)):.0f} kcal",
                f"本餐營養：蛋白質 {analysis.get('protein_g',0):.0f}g｜碳水 {analysis.get('carbs_g',0):.0f}g｜脂肪 {analysis.get('fat_g',0):.0f}g｜蔬菜 {fmtveg(analysis.get('veg_fists',0))}拳",
            ]
            comps=analysis.get("components") or []
            if comps:
                history_parts.append("食物拆解：")
                for comp in comps:
                    history_parts.append(f"• {comp.get('name','食物')}｜{comp.get('portion','份量未明')}｜約 {float(comp.get('calories_low',0) or 0):.0f}–{float(comp.get('calories_high',0) or 0):.0f} kcal" + (f"｜{comp.get('note')}" if comp.get('note') else ""))
            if (analysis.get('good') or '').strip():
                history_parts.append(f"這餐有做到的地方：{analysis.get('good','')}")
            history_parts.append(f"這餐最需要調整：{analysis.get('advice','')}")
            history_parts.append("當時今天整體狀態：")
            after=analysis.get("today_after") or {}
            history_parts.append(f"• 熱量 {after.get('calories',0):.0f}/{float(p.get('daily_calories',0) or 0):.0f} kcal")
            for key,label,unit in (("protein","蛋白質","g"),("carbs","碳水","g"),("fat","脂肪","g"),("veg","蔬菜","拳")):
                item=dp.get(key) or {}
                current=item.get('current',after.get(key,0)); goal=item.get('goal',3 if key=='veg' else 0)
                history_parts.append(f"• {label} {fmtveg(current)}/{fmtveg(goal)}{unit}｜{item.get('status','')}")
            if analysis.get("daily_priority"):
                history_parts.append(f"當時今天最優先：{analysis.get('daily_priority')}")
            if analysis.get("next_meal"):
                history_parts.append(f"當時下一餐建議：{analysis.get('next_meal')}")
            guides=analysis.get("next_meal_guides") or []
            if guides:
                history_parts.append("當時外食下一餐指南：")
                for guide in guides:
                    history_parts.append(f"• {guide.get('title','外食選擇')}：{guide.get('text','')}")
            if (analysis.get('estimate_note') or '').strip():
                history_parts.append(f"估算說明：{analysis.get('estimate_note','')}")
            summary="\n".join(history_parts)
            c=db(); cur=c.execute("""INSERT INTO meals(user_key,meal_type,source,content,analysis,created_at,hunger_before,fullness_after,water_ml,note,calories,protein_g,carbs_g,fat_g,veg_fists,estimate_note)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id""" if USE_POSTGRES else """INSERT INTO meals(user_key,meal_type,source,content,analysis,created_at,hunger_before,fullness_after,water_ml,note,calories,protein_g,carbs_g,fat_g,veg_fists,estimate_note)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(user_key(),meal_type,"photo" if raw else "text",text,summary,db_now_iso(),num("hunger_before"),num("fullness_after"),num("water_ml"),request.form.get("note","").strip(),analysis.get("calories"),analysis.get("protein_g"),analysis.get("carbs_g"),analysis.get("fat_g"),analysis.get("veg_fists"),analysis.get("estimate_note")))
            meal_id=(cur.fetchone()["id"] if USE_POSTGRES else cur.lastrowid)
            for b in raws[:5]:
                # Keep the exact submitted image available for later human review.
                mime="image/png" if b.startswith(b"\x89PNG") else ("image/webp" if b.startswith(b"RIFF") and b[8:12]==b"WEBP" else "image/jpeg")
                data_url=f"data:{mime};base64,"+base64.b64encode(b).decode("ascii")
                c.execute("INSERT INTO meal_photos(meal_id,user_key,data_url,created_at) VALUES(?,?,?,?)",(meal_id,user_key(),data_url,db_now_iso()))
            c.commit(); c.close()
    totals=today_totals(); rem=remaining(p,totals); today_progress=daily_nutrition_progress(p,totals)
    return render_template("meal.html",analysis=analysis,error=error,p=p,totals=totals,remaining=rem,today_progress=today_progress,target_meta=calculate_targets(p),form_data=form_data,usage=today_usage(),limit=int((membership() or {}).get("daily_limit") or 6),form_token=form_token)

def tracked_days(rows):
    return len({r["created_at"][:10] for r in rows})

@app.route("/week")
def week():
    g=guard_member()
    if g:return g
    p=get_profile()
    if not p:return redirect(url_for("assessment"))
    start_day=taipei_now().date()-timedelta(days=6); since,_=taipei_day_utc_bounds(start_day); c=db()
    rows=c.execute("SELECT * FROM meals WHERE user_key=? AND created_at>=? AND deleted_at IS NULL ORDER BY created_at DESC",(user_key(),since)).fetchall()
    water_rows=c.execute("SELECT * FROM hydration_logs WHERE user_key=? AND created_at>=? ORDER BY created_at DESC",(user_key(),since)).fetchall()
    c.close()
    return render_template("week.html",rows=rows,water_rows=water_rows,p=p,photos=photos_by_meal(rows))


@app.route("/water/<int:log_id>/delete",methods=["POST"])
def delete_water(log_id):
    g=guard_member()
    if g:return g
    c=db()
    c.execute("DELETE FROM hydration_logs WHERE id=? AND user_key=?",(log_id,user_key()))
    c.commit(); c.close()
    flash("已刪除這筆飲水紀錄，今日飲水已重新計算。")
    return redirect(request.form.get("next") or url_for("week"))

@app.route("/meal/<int:meal_id>/delete",methods=["POST"])
def delete_meal(meal_id):
    g=guard_member()
    if g:return g
    c=db()
    c.execute("UPDATE meals SET deleted_at=? WHERE id=? AND user_key=? AND deleted_at IS NULL",
              (db_now_iso(),meal_id,user_key()))
    c.commit(); c.close()
    flash("已刪除這筆餐點，今日累計已重新計算。")
    return redirect(request.form.get("next") or url_for("week"))

@app.route("/reset")
def reset():
    session.clear()
    return redirect(url_for("home"))

def admin_ok():
    return bool(os.getenv("ADMIN_TOKEN") and (request.args.get("token") or request.form.get("token"))==os.getenv("ADMIN_TOKEN"))

def consultant_current():
    cid=session.get("consultant_id")
    if not cid:return None
    c=db(); r=c.execute("SELECT * FROM consultants WHERE id=? AND active=1",(cid,)).fetchone(); c.close()
    return dict(r) if r else None

def can_view_client(uid):
    if admin_ok(): return True
    con=consultant_current()
    if not con:return False
    c=db(); r=c.execute("SELECT 1 FROM memberships WHERE line_user_id=? AND consultant_id=?",(uid,con["id"])).fetchone(); c.close()
    return bool(r)

def photos_by_meal(meals):
    result={}
    if not meals:return result
    c=db()
    for m in meals:
        result[m["id"]]=c.execute("SELECT * FROM meal_photos WHERE meal_id=? ORDER BY id",(m["id"],)).fetchall()
    c.close(); return result

@app.route("/join/<code>")
def consultant_join(code):
    c=db(); con=c.execute("SELECT * FROM consultants WHERE invite_code=? AND active=1",(code,)).fetchone(); c.close()
    if not con:return "邀請連結無效或已停用。",404
    session["pending_invite"]=code
    if current_line_user():
        c=db(); m=c.execute("SELECT consultant_id FROM memberships WHERE line_user_id=?",(current_line_user()["userId"],)).fetchone()
        if m and not m["consultant_id"]:
            c.execute("UPDATE memberships SET consultant_id=?,updated_at=? WHERE line_user_id=?",(con["id"],db_now_iso(),current_line_user()["userId"])); c.commit()
        c.close(); session.pop("pending_invite",None); return redirect(url_for("home"))
    return redirect(url_for("line_login"))

@app.route("/consultant/login",methods=["GET","POST"])
def consultant_login():
    if request.method=="POST":
        c=db(); con=c.execute("SELECT * FROM consultants WHERE username=? AND active=1",((request.form.get("username") or "").strip(),)).fetchone(); c.close()
        if con and check_password_hash(con["password_hash"],request.form.get("password") or ""):
            session["consultant_id"]=con["id"]; return redirect(url_for("consultant_dashboard"))
        flash("帳號或密碼不正確。")
    return render_template("consultant_login.html")

@app.route("/consultant/logout")
def consultant_logout():
    session.pop("consultant_id",None); return redirect(url_for("consultant_login"))

@app.route("/consultant")
def consultant_dashboard():
    con=consultant_current()
    if not con:return redirect(url_for("consultant_login"))
    start_utc,end_utc=taipei_day_utc_bounds(); c=db()
    rows=c.execute("""SELECT m.*,p.name,p.weight,p.goal,
      (SELECT COUNT(*) FROM meals x WHERE x.user_key='line-'||m.line_user_id AND x.created_at>=? AND x.created_at<? AND x.deleted_at IS NULL) today_uses,
      (SELECT MAX(created_at) FROM meals x WHERE x.user_key='line-'||m.line_user_id AND x.deleted_at IS NULL) last_meal_at
      FROM memberships m LEFT JOIN profiles p ON p.line_user_id=m.line_user_id WHERE m.consultant_id=? ORDER BY m.updated_at DESC""",(start_utc,end_utc,con["id"])).fetchall(); c.close()
    return render_template("consultant_dashboard.html",rows=rows,consultant=con)

@app.route("/admin")
def admin():
    if not admin_ok(): return "Unauthorized",401
    start_utc,end_utc=taipei_day_utc_bounds(); c=db()
    rows=c.execute("""SELECT m.*,p.name,p.weight,p.goal,c.name consultant_name,
      (SELECT COUNT(*) FROM meals x WHERE x.user_key='line-'||m.line_user_id AND x.created_at>=? AND x.created_at<? AND x.deleted_at IS NULL) today_uses,
      (SELECT MAX(created_at) FROM meals x WHERE x.user_key='line-'||m.line_user_id AND x.deleted_at IS NULL) last_meal_at
      FROM memberships m LEFT JOIN profiles p ON p.line_user_id=m.line_user_id LEFT JOIN consultants c ON c.id=m.consultant_id ORDER BY m.updated_at DESC""",(start_utc,end_utc)).fetchall()
    consultants=c.execute("SELECT * FROM consultants ORDER BY name").fetchall(); c.close()
    return render_template("admin.html",rows=rows,consultants=consultants,token=request.args.get("token"))

@app.route("/admin/consultant/create",methods=["POST"])
def admin_consultant_create():
    if not admin_ok():return "Unauthorized",401
    name=(request.form.get("name") or "").strip(); username=(request.form.get("username") or "").strip(); password=request.form.get("password") or ""
    if not name or not username or len(password)<6:
        flash("顧問姓名、帳號必填，密碼至少 6 碼。")
    else:
        c=db()
        try:
            c.execute("INSERT INTO consultants(name,username,password_hash,invite_code,created_at) VALUES(?,?,?,?,?)",(name,username,generate_password_hash(password),secrets.token_urlsafe(8),db_now_iso())); c.commit()
        except Exception: flash("建立失敗：帳號可能已存在。")
        c.close()
    return redirect(url_for("admin",token=request.form.get("token")))

@app.route("/admin/member/<uid>",methods=["POST"])
def admin_member(uid):
    if not admin_ok(): return "Unauthorized",401
    days=max(1,int(request.form.get("days","30"))); start_text=request.form.get("program_start") or taipei_now().date().isoformat()
    try:
        local=datetime.fromisoformat(start_text).replace(tzinfo=TAIPEI_TZ); start=local.astimezone(timezone.utc).replace(tzinfo=None)
    except: start=utc_now_naive()
    exp=start+timedelta(days=days); consultant_id=request.form.get("consultant_id") or None; c=db()
    c.execute("UPDATE memberships SET status='active',starts_at=?,program_starts_at=?,plan_days=?,expires_at=?,consultant_id=?,updated_at=? WHERE line_user_id=?",
      (start.isoformat(timespec="seconds"),start.isoformat(timespec="seconds"),days,exp.isoformat(timespec="seconds"),consultant_id,db_now_iso(),uid)); c.commit(); c.close()
    return redirect(url_for("admin",token=request.form.get("token")))

@app.route("/client/<uid>")
def client_detail(uid):
    if not can_view_client(uid):return "Unauthorized",401
    days=request.args.get("days","14")
    try: days_i=int(days)
    except: days_i=14
    c=db(); p=c.execute("SELECT * FROM profiles WHERE line_user_id=?",(uid,)).fetchone(); m=c.execute("SELECT * FROM memberships WHERE line_user_id=?",(uid,)).fetchone()
    params=["line-"+uid]; sql="SELECT * FROM meals WHERE user_key=? AND deleted_at IS NULL"
    if days_i>0:
        since=(utc_now_naive()-timedelta(days=days_i)).isoformat(timespec="seconds"); sql+=" AND created_at>=?"; params.append(since)
    sql+=" ORDER BY created_at DESC LIMIT 500"; meals=c.execute(sql,tuple(params)).fetchall()
    water_logs=c.execute("SELECT * FROM hydration_logs WHERE user_key=? ORDER BY created_at DESC LIMIT 200",("line-"+uid,)).fetchall()
    notes=c.execute("SELECT n.*,c.name consultant_name FROM consultant_notes n LEFT JOIN consultants c ON c.id=n.consultant_id WHERE n.line_user_id=? ORDER BY n.created_at DESC",(uid,)).fetchall(); c.close()
    return render_template("client_detail.html",p=p,m=m,meals=meals,water_logs=water_logs,photos=photos_by_meal(meals),notes=notes,days=days_i,token=request.args.get("token"),is_admin=admin_ok(),consultant=consultant_current())

@app.route("/client/<uid>/note",methods=["POST"])
def client_note(uid):
    if not can_view_client(uid):return "Unauthorized",401
    note=(request.form.get("note") or "").strip()
    if note:
        con=consultant_current(); cid=con["id"] if con else None; c=db(); c.execute("INSERT INTO consultant_notes(consultant_id,line_user_id,note,created_at) VALUES(?,?,?,?)",(cid,uid,note,db_now_iso())); c.commit(); c.close()
    return redirect(url_for("client_detail",uid=uid,days=request.form.get("days","14"),token=request.form.get("token")))

@app.route("/admin/client/<uid>")
def admin_client(uid):
    if not admin_ok(): return "Unauthorized",401
    return redirect(url_for("client_detail",uid=uid,token=request.args.get("token"),days=request.args.get("days","14")))

@app.route("/admin/client/<uid>/meal/<int:meal_id>/delete",methods=["POST"])
def admin_delete_meal(uid,meal_id):
    if not admin_ok(): return "Unauthorized",401
    c=db(); c.execute("UPDATE meals SET deleted_at=? WHERE id=? AND user_key=? AND deleted_at IS NULL",(db_now_iso(),meal_id,"line-"+uid)); c.commit(); c.close()
    return redirect(url_for("client_detail",uid=uid,token=request.form.get("token")))

@app.route("/health")
def health(): return {"status":"ok","version":"meal-coach-2.1.2","database":"postgres" if USE_POSTGRES else "sqlite"}

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",5000)),debug=True)
