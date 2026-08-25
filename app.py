
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




def daily_nutrition_progress(p, totals):
    """Return body-composition-oriented daily progress.
    Protein and vegetables are active targets.
    Carbs and fat are treated primarily as allocation/upper-space, not something to force-fill.
    """
    protein_now=float(totals.get("protein",0) or 0)
    carbs_now=float(totals.get("carbs",0) or 0)
    fat_now=float(totals.get("fat",0) or 0)
    veg_now=float(totals.get("veg_fists",0) or 0)

    protein_goal=float(p.get("daily_protein",0) or 0)
    carbs_goal=float(p.get("daily_carbs",0) or 0)
    fat_goal=float(p.get("daily_fat",0) or 0)
    veg_goal=3.0

    # Protein: actively encourage enough intake.
    protein_gap=max(0, protein_goal-protein_now)
    if protein_goal<=0:
        protein_status="無法判斷"
        protein_note="尚未設定蛋白質目標。"
    elif protein_now < protein_goal*0.65:
        protein_status="目前偏少"
        protein_note=f"今天累積約 {protein_now:.0f}g，距離建議目標仍差約 {protein_gap:.0f}g；後續餐次優先補充蛋白質。"
    elif protein_now < protein_goal*0.9:
        protein_status="接近目標"
        protein_note=f"今天累積約 {protein_now:.0f}g，距離建議目標約差 {protein_gap:.0f}g；後續餐次再補一些即可。"
    else:
        protein_status="已達建議量"
        protein_note=f"今天蛋白質約 {protein_now:.0f}g，已接近或達到建議目標，不需要為了湊數字額外硬補。"

    # Carbs: allocation space, not mandatory to fill.
    carb_gap=max(0, carbs_goal-carbs_now)
    if carbs_goal<=0:
        carbs_status="無法判斷"
        carbs_note="尚未設定碳水化合物參考量。"
    elif carbs_now > carbs_goal*1.1:
        carbs_status="偏多"
        carbs_note=f"今天碳水約 {carbs_now:.0f}g，已高於參考配置；後續主食份量可先減少。"
    elif carbs_now >= carbs_goal*0.65:
        carbs_status="目前可接受"
        carbs_note=f"今天碳水約 {carbs_now:.0f}g，目前仍在可接受配置內；不需要為了吃滿 {carbs_goal:.0f}g 刻意補主食。"
    else:
        carbs_status="目前偏少"
        carbs_note=f"今天碳水約 {carbs_now:.0f}g，仍有約 {carb_gap:.0f}g 的配置空間；可依飢餓感與後續餐次安排，不用硬補滿。"

    # Fat: mostly an upper-space consideration.
    fat_gap=max(0, fat_goal-fat_now)
    if fat_goal<=0:
        fat_status="無法判斷"
        fat_note="尚未設定脂肪參考量。"
    elif fat_now > fat_goal*1.05:
        fat_status="偏多"
        fat_note=f"今天脂肪約 {fat_now:.0f}g，已超過參考量；後續先避開炸物、肥肉、堅果與濃醬。"
    elif fat_now >= fat_goal*0.75:
        fat_status="已接近今日建議量"
        fat_note=f"今天脂肪約 {fat_now:.0f}g，已接近今日建議量；後續不需要特別補脂肪。"
    else:
        fat_status="目前可接受"
        fat_note=f"今天脂肪約 {fat_now:.0f}g，目前仍在可接受範圍；不需要為了吃滿 {fat_goal:.0f}g 特別補油脂。"

    # Vegetables: active target, using fists rather than fake fiber grams.
    veg_gap=max(0, veg_goal-veg_now)
    if veg_now < 1:
        veg_status="明顯不足"
        veg_note=f"今天目前約 {veg_now:.1f} 拳蔬菜；後續餐次優先補足，下一餐至少安排 1–2 拳。"
    elif veg_now < veg_goal:
        veg_status="目前偏少"
        veg_note=f"今天目前約 {veg_now:.1f} 拳蔬菜，距離約 {veg_goal:.0f} 拳參考量仍差約 {veg_gap:.1f} 拳。"
    else:
        veg_status="已達建議量"
        veg_note=f"今天蔬菜約 {veg_now:.1f} 拳，已達基本建議量。"

    return {
        "protein":{"current":protein_now,"goal":protein_goal,"status":protein_status,"note":protein_note},
        "carbs":{"current":carbs_now,"goal":carbs_goal,"status":carbs_status,"note":carbs_note},
        "fat":{"current":fat_now,"goal":fat_goal,"status":fat_status,"note":fat_note},
        "veg":{"current":veg_now,"goal":veg_goal,"status":veg_status,"note":veg_note},
    }


def next_meal_from_daily_progress(progress, remaining, meal_type=None, timing="before", fullness_after=None):
    try:
        fullness=int(fullness_after) if fullness_after not in (None,"") else None
    except:
        fullness=None

    protein_need = progress["protein"]["status"] in ("目前偏少","接近目標")
    veg_need = progress["veg"]["status"] in ("明顯不足","目前偏少")
    fat_high = progress["fat"]["status"] in ("已接近今日建議量","偏多")
    carbs_high = progress["carbs"]["status"]=="偏多"

    cal=max(0,int(round(remaining.get("calories",0) or 0)))
    p=max(0,int(round(remaining.get("protein",0) or 0)))
    c=max(0,int(round(remaining.get("carbs",0) or 0)))
    f=max(0,int(round(remaining.get("fat",0) or 0)))

    intro=""
    if timing=="after" and fullness is not None and fullness>=7:
        intro="這餐已經吃完而且飽足感夠，現在不用立刻補吃。"
    else:
        intro="如果今天後續還會進食，"

    priorities=[]
    if protein_need:
        priorities.append("優先補蛋白質")
    if veg_need:
        priorities.append("把蔬菜補回來")
    if not priorities:
        priorities.append("依飢餓感安排即可")

    cautions=[]
    if fat_high:
        cautions.append("先避開炸物、肥肉、堅果與濃醬")
    if carbs_high:
        cautions.append("主食份量先減少")

    # Construct practical next-meal range without exceeding remaining space.
    if cal <= 0:
        return intro+" 今天的熱量參考空間已用完；如果真的餓，先以無糖飲品、清湯或少量蔬菜為主。"

    meal_hi=min(cal, 500)
    meal_lo=min(meal_hi, 300 if meal_hi>=300 else max(150, int(meal_hi*0.65)))

    target_p=min(p, 30)
    target_c=min(c, 45 if carbs_high else 60)
    target_f=min(f, 8 if fat_high else 12)

    detail=[]
    if protein_need and target_p>0:
        detail.append(f"蛋白質約 20–{target_p}g" if target_p>=20 else f"蛋白質約 {target_p}g 內")
    if veg_need:
        detail.append("蔬菜 1–2 拳")
    if not carbs_high and target_c>0:
        detail.append(f"主食依飢餓感安排，碳水約 20–{target_c}g 內")
    if target_f>=0:
        detail.append(f"脂肪控制在約 {target_f}g 內")

    sentence = intro+" "+"、".join(priorities)+"。"
    sentence += f" 若還會吃一餐，可抓約 {meal_lo}–{meal_hi} kcal"
    if detail:
        sentence += "，"+"、".join(detail)
    sentence += "。"
    if cautions:
        sentence += " "+"；".join(cautions)+"。"
    sentence += " 剩餘額度是參考空間，不代表一定要全部吃完。"
    return sentence


def next_meal_guidance(rem, meal_type=None, timing="before", fullness_after=None):
    cal=max(0,int(round(rem.get("calories",0) or 0)))
    p=max(0,int(round(rem.get("protein",0) or 0)))
    c=max(0,int(round(rem.get("carbs",0) or 0)))
    f=max(0,int(round(rem.get("fat",0) or 0)))
    meal_type=meal_type or ""
    try: fullness=int(fullness_after) if fullness_after not in (None,"") else None
    except: fullness=None

    if cal <= 0:
        return "今天的熱量參考額度已用完；如果仍然餓，先以無糖飲品、清湯或少量低熱量蔬菜為主，不需要為了湊營養素硬吃。"

    # If dinner/supper is already eaten and user is full, avoid prescribing another full meal.
    if timing=="after" and fullness is not None and fullness>=7 and meal_type in ("晚餐","宵夜"):
        parts=["你已經吃完而且飽足度高，現在不用再補吃。"]
        if p>0 or c>0 or f>0:
            parts.append(f"今天目前約還有 {cal} kcal 的參考空間，但不代表一定要吃完。")
        if f <= 8:
            parts.append("如果晚一點真的餓，優先選低脂蛋白質或蔬菜，例如無糖豆漿、茶葉蛋白、嫩豆腐、燙青菜；先避開炸物、堅果與濃醬。")
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

每日目標：{p.get('daily_calories')} kcal，蛋白質 {p.get('daily_protein')}g，碳水 {p.get('daily_carbs')}g，脂肪 {p.get('daily_fat')}g。
本餐前今日累計：{totals.get('calories',0)} kcal，P {totals.get('protein',0)}g，C {totals.get('carbs',0)}g，F {totals.get('fat',0)}g。
本餐前剩餘：約 {rem['calories']:.0f} kcal，P {rem['protein']:.0f}g，C {rem['carbs']:.0f}g，F {rem['fat']:.0f}g。
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

只輸出 JSON：
{{
"dish_name":"餐點名稱",
"components":[{{"name":"組成","portion":"份量範圍","calories_low":數字,"calories_high":數字,"note":"估算依據"}}],
"calories_low":數字,"calories_estimate":數字,"calories_high":數字,
"protein_g":數字,"carbs_g":數字,"fat_g":數字,"veg_fists":數字,
"confidence":"高/中/低",
"protein_status":"足夠/可接受/偏少/偏多/無法判斷",
"carbs_status":"足夠/可接受/偏少/偏多/無法判斷",
"fat_status":"足夠/可接受/偏少/偏多/無法判斷",
"veg_status":"足夠/可接受/偏少/偏多/無法判斷",
"overall":"一句話整體判斷",
"good":"做得好的地方",
"advice":"現在最值得做的一件事",
"next_meal":"依剩餘額度給下一餐範圍與2個台灣常見例子",
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

    low=max(0,data["calories_low"]); est=max(0,data["calories_estimate"]); high=max(low,data["calories_high"])
    est=min(max(est,low),high)

    # Cross-check the center estimate against P/C/F. If they disagree materially,
    # use macro-derived kcal when it still falls inside the photo-estimated range.
    macro_kcal=data["protein_g"]*4 + data["carbs_g"]*4 + data["fat_g"]*9
    if macro_kcal>0 and est>0:
        gap=abs(est-macro_kcal)/max(est,macro_kcal)
        if gap>0.15 and low <= macro_kcal <= high:
            est=macro_kcal
            data["estimate_note"]=(data.get("estimate_note","")+" 中心熱量已用三大營養素交叉校正。").strip()

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

    # Daily progress is intentionally separate from the single-meal judgement.
    # A meal can contain a reasonable protein portion while the whole day is still short.
    veg_target=3.0
    def nutrient_progress(label, current, target, unit="g", lower_ratio=0.90, upper_ratio=1.10):
        current=float(current or 0); target=float(target or 0)
        if target <= 0:
            return {"label":label,"current":round(current,1),"target":0,"unit":unit,"status":"無法判斷","message":"目前沒有可用的每日目標。"}
        diff=target-current
        ratio=current/target
        if ratio < lower_ratio:
            status="不足"
            message=f"目前仍差約 {max(0,round(diff))}{unit}，後續餐次需要補足。"
        elif ratio <= upper_ratio:
            status="接近目標" if current < target else "已達目標"
            message=(f"目前約還有 {max(0,round(diff))}{unit} 的參考空間。" if diff>0 else "今天已接近或達到目標，不需要為了湊數字硬吃。")
        else:
            status="偏多"
            message=f"目前已超過每日參考目標約 {round(current-target)}{unit}，後續餐次不需再刻意補充。"
        return {"label":label,"current":round(current,1),"target":round(target,1),"unit":unit,"status":status,"message":message}

    daily_progress=[
        nutrient_progress("蛋白質",after["protein"],p.get("daily_protein"),"g"),
        nutrient_progress("碳水化合物",after["carbs"],p.get("daily_carbs"),"g"),
        nutrient_progress("脂肪",after["fat"],p.get("daily_fat"),"g",0.85,1.05),
        nutrient_progress("蔬菜",after["veg"],veg_target,"拳",0.85,1.15),
    ]
    # More useful vegetable wording for a photo-based coaching product.
    vp=daily_progress[-1]
    if after["veg"] < veg_target:
        vp["status"]="不足"
        vp["message"]=f"今天目前約 {after['veg']:g} 拳，建議後續再補約 {max(0,veg_target-after['veg']):g} 拳蔬菜。"
    elif after["veg"] <= 5:
        vp["status"]="足夠"; vp["message"]="今天的蔬菜量已達一般體態管理的起始參考量。"
    else:
        vp["status"]="充足"; vp["message"]="今天蔬菜量充足，後續依飢餓感與整體餐盤安排即可。"
    data["daily_progress"]=daily_progress

    # Clarify meal-level protein wording whenever today's total is still below target.
    if (p.get("daily_protein") or 0) > after["protein"]:
        data["good"]=(data.get("good") or "").rstrip("。") + f"。這餐的蛋白質來源有吃到，但今天累計仍只有約 {after['protein']}g，距離每日目標仍差約 {remaining_after['protein']}g。"

    # Do not trust a free-form AI range here. Python enforces today's true remaining caps.
    daily_totals_after={
        "calories":after["calories"],
        "protein":after["protein"],
        "carbs":after["carbs"],
        "fat":after["fat"],
        "veg_fists":round((totals.get("veg_fists",0) or 0)+(data.get("veg_fists",0) or 0),1)
    }
    progress=daily_nutrition_progress(p,daily_totals_after)
    data["daily_progress"]=progress
    data["next_meal"]=next_meal_from_daily_progress(
        progress,
        remaining_after,
        meal_type=meal_type,
        timing=timing,
        fullness_after=fullness_after
    )

    # Keep single-meal praise separate from the whole-day status.
    if data.get("daily_progress"):
        dp=data["daily_progress"]
        if dp["protein"]["status"]=="目前偏少":
            data["good"]=(data.get("good","").rstrip("。")+"。這餐本身有吃到蛋白質，但今天整體蛋白質仍偏少；"+dp["protein"]["note"]).strip()
    # Align the immediate advice with the actual remaining situation.
    if timing=="after" and fullness_after not in (None,""):
        try:
            full_i=int(fullness_after)
        except:
            full_i=None
        if full_i is not None and full_i>=7:
            if remaining_after["fat"] <= 8:
                data["advice"]="這餐已經吃完而且飽足感夠，不用立刻補吃。今天後續若還會吃，優先低脂蛋白質＋蔬菜，先不要再加炸物、堅果、濃醬或大量主食。"
            elif data["veg_fists"]==0:
                data["advice"]="這餐已經吃完，不用立刻硬補；下一次進食把蔬菜補回來，其他份量依今天剩餘額度安排即可。"

    app.logger.info("TOTAL_ANALYSIS elapsed=%.3fs",time.perf_counter()-started)
    return data,None

def today_totals():
    today=datetime.now().date().isoformat(); c=db()
    rows=c.execute("SELECT calories,protein_g,carbs_g,fat_g,veg_fists,water_ml FROM meals WHERE user_key=? AND substr(created_at,1,10)=? AND deleted_at IS NULL",(user_key(),today)).fetchall()
    water_rows=c.execute("SELECT amount_ml FROM hydration_logs WHERE user_key=? AND substr(created_at,1,10)=?",(user_key(),today)).fetchall()
    c.close()
    return {
        "calories":round(sum((r["calories"] or 0) for r in rows),1),
        "protein":round(sum((r["protein_g"] or 0) for r in rows),1),
        "carbs":round(sum((r["carbs_g"] or 0) for r in rows),1),
        "fat":round(sum((r["fat_g"] or 0) for r in rows),1),
        "veg":round(sum((r["veg_fists"] or 0) for r in rows),1),
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
            return render_template("meal.html",analysis=None,error="請至少上傳一張餐點照片，或輸入餐點內容。",p=p,totals=totals,remaining=rem,form_data=form_data,usage=today_usage(),limit=limit,form_token=secrets.token_urlsafe(24))
        def num(n):
            try:return int(request.form.get(n,"") or 0) or None
            except:return None
        pre_totals=today_totals()
        try:analysis,error=ai_analyze(text,raws,p,timing,num("hunger_before"),num("fullness_after"),pre_totals,meal_type)
        except Exception as e:
            app.logger.exception("AI meal analysis failed")
            error="餐點分析目前暫時無法使用，請稍後再試。文字內容已保留；照片需要重新選擇。"
        if not analysis:
            c=db(); c.execute("DELETE FROM submission_tokens WHERE token=? AND user_key=?",(submitted_token,user_key())); c.commit(); c.close()
            form_token=secrets.token_urlsafe(24)
        if analysis:
            ra=analysis.get("remaining_after") or {}
            dp=analysis.get("daily_progress") or []
            if isinstance(dp, dict):
                _order=[("protein","蛋白質"),("carbs","碳水化合物"),("fat","脂肪"),("veg","蔬菜")]
                _parts=[]
                for _key,_label in _order:
                    _x=dp.get(_key) or {}
                    _unit="拳" if _key=="veg" else "g"
                    _current=_x.get("current",0)
                    _goal=_x.get("goal",0)
                    _status=_x.get("status","")
                    _note=_x.get("note","")
                    _parts.append(f"{_label}：{_current:g}/{_goal:g}{_unit}｜{_status}｜{_note}")
                progress_text="\n".join(_parts)
            elif isinstance(dp, list):
                progress_text="\n".join([f"{x.get('label','')}：{x.get('current',0):g}/{x.get('target',0):g}{x.get('unit','')}｜{x.get('status','')}｜{x.get('message','')}" for x in dp if isinstance(x,dict)])
            else:
                progress_text=""
            summary=(f"整體：{analysis.get('overall','')}\n"
                     f"做得好：{analysis.get('good','')}\n"
                     f"最優先調整：{analysis.get('advice','')}\n"
                     f"本餐後今日剩餘：熱量約 {ra.get('calories',0)} kcal｜蛋白質約 {ra.get('protein',0)}g｜碳水約 {ra.get('carbs',0)}g｜脂肪約 {ra.get('fat',0)}g\n"
                     f"今日整體營養進度：\n{progress_text}\n"
                     f"下一餐：{analysis.get('next_meal','')}")
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
def health(): return {"status":"ok","version":"meal-coach-1.7.5.1","database":"postgres" if USE_POSTGRES else "sqlite"}

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",5000)),debug=True)
