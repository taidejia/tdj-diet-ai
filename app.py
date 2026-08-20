
import os, json, sqlite3, base64, requests
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from openai import OpenAI

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "tdj-v2-change-me")
DB = os.getenv("DB_PATH", "tdj_v2.db")

def db():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    return c

def init_db():
    c=db()
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
    c.commit(); c.close()
init_db()

def user_key():
    return session.setdefault("user_key", "web-"+os.urandom(8).hex())

def get_profile():
    c=db(); r=c.execute("SELECT * FROM profiles WHERE user_key=?", (user_key(),)).fetchone(); c.close()
    return dict(r) if r else None

def freq_score(v):
    return {"never":0,"rare":1,"weekly":2,"often":3,"daily":4}.get(v,0)

def assess(p):
    issues=[]
    tasks=[]
    # activity
    steps=p.get("steps") or 0
    sitting=p.get("sitting_hours") or 0
    if steps < 4000 or sitting >= 9:
        issues.append(("日常活動量偏低", 4, "久坐或每日步數偏低，可能讓每日總消耗下降。"))
        target=max(4500, min(7000, steps+1500))
        tasks.append(f"把每日平均步數先提高到約 {target:,} 步，不要求一次做到一萬步。")
    # protein
    prot=p.get("protein","")
    if prot in ("none","half"):
        issues.append(("蛋白質配置不足", 5, "目前每餐蛋白質份量偏少，優先調整比繼續砍飯更重要。"))
        tasks.append("每天至少 2 餐安排一個明確蛋白質主菜，目標每餐約 1 掌心起。")
    # vegetables
    veg=p.get("vegetables","")
    if veg in ("none","few","half"):
        issues.append(("蔬菜量不足", 3, "蔬菜攝取偏少，飽足感與整體飲食品質容易受影響。"))
        tasks.append("至少 2 餐把蔬菜提高到約 1 拳以上，先從最容易做到的一餐開始。")
    # liquid/snack
    liquid=freq_score(p.get("sugary_drinks",""))+freq_score(p.get("alcohol",""))
    snack=freq_score(p.get("snacks",""))+freq_score(p.get("late_snack",""))
    if liquid >= 5:
        issues.append(("飲料／酒精的隱形熱量偏高", 5, "正餐之外的液體熱量可能是目前更值得先處理的來源。"))
        tasks.append("這週先把含糖飲或酒精頻率減少約一半，不需要同時大幅減少正餐。")
    if snack >= 5:
        issues.append(("零食／宵夜頻率偏高", 4, "額外進食頻率高時，常比正餐澱粉更容易推高總攝取。"))
        tasks.append("先挑最常出現的零食或宵夜時段，每週至少減少 2 次。")
    # water
    if (p.get("water") or 0) < 1200:
        issues.append(("飲水偏少", 2, "目前飲水量偏低。"))
        tasks.append("每日飲水先增加 300–500 ml，分散在白天完成。")
    # sleep
    if (p.get("sleep_hours") or 0) < 6 or p.get("sleep_quality")=="poor":
        issues.append(("睡眠可能干擾飲食控制", 3, "睡眠不足或品質差時，飢餓與高熱量食物偏好可能更難管理。"))
        tasks.append("這週先固定一個可做到的睡覺時間，目標平均睡眠至少增加 30 分鐘。")
    # overrestriction
    if (p.get("meals") or 3) <= 1 or p.get("current_diet")=="very_low":
        issues.append(("可能有過度限制飲食", 6, "目前吃得過少時，不適合再用『繼續少吃』當第一步。"))
        tasks.insert(0,"先停止進一步砍餐或砍主食，改為記錄 3–7 天實際攝取與飢餓狀況。")
    # plateau
    if (p.get("diet_weeks") or 0) >= 4 and abs(p.get("four_week_change") or 0) < .5:
        issues.append(("近期體重變化停滯", 4, "已執行一段時間但近四週變化很小，需要核對實際攝取與活動，而不是直接大幅減量。"))
    issues=sorted(issues,key=lambda x:x[1],reverse=True)
    if not issues:
        issues=[("目前沒有明顯單一高風險飲食型態",1,"先用 7 天飲食紀錄找出真正反覆出現的模式。")]
        tasks=["連續記錄 7 天主要餐點、飲料與零食，不用刻意吃得更少。"]
    # dedupe and top3 tasks
    out=[]
    for t in tasks:
        if t not in out: out.append(t)
    return issues[:3], out[:3]


def evidence_for(p, key):
    ev=[]
    if key=="activity":
        if p.get("steps") is not None: ev.append(f"每日平均步數約 {p.get('steps'):,} 步")
        if p.get("sitting_hours"): ev.append(f"每天久坐約 {p.get('sitting_hours')} 小時")
        if p.get("exercise_days") is not None: ev.append(f"每週運動 {p.get('exercise_days')} 天")
    elif key=="protein":
        ev.append("你填寫的每餐蛋白質份量偏少")
    elif key=="vegetables":
        ev.append("你填寫的每餐蔬菜份量偏少")
    elif key=="liquid":
        ev.append("含糖飲／酒精出現頻率較高")
    elif key=="snack":
        ev.append("零食、甜點或宵夜出現頻率較高")
    elif key=="sleep":
        if p.get("sleep_hours"): ev.append(f"平均睡眠約 {p.get('sleep_hours')} 小時")
        if p.get("sleep_quality")=="poor": ev.append("你回報睡眠品質不佳")
    elif key=="restriction":
        ev.append("目前有跳餐或吃得非常少的情況")
    elif key=="plateau":
        if p.get("diet_weeks"): ev.append(f"已執行飲食控制約 {p.get('diet_weeks')} 週")
        if p.get("four_week_change") is not None: ev.append(f"近 4 週體重變化約 {p.get('four_week_change')} kg")
    elif key=="water":
        if p.get("water") is not None: ev.append(f"每日飲水約 {p.get('water')} ml")
    return ev

def candidate_scores(p):
    c=[]
    def add(key,title,score,why,task):
        c.append({"key":key,"title":title,"score":score,"why":why,"task":task,"evidence":evidence_for(p,key)})
    steps=p.get("steps") or 0
    sitting=p.get("sitting_hours") or 0
    if steps < 5000 or sitting >= 8:
        sev=2 + (2 if steps<3500 else 1 if steps<5000 else 0) + (1 if sitting>=9 else 0)
        add("activity","日常活動量可能是目前的限制之一",sev,
            "你的日常移動量偏低。若飲食已經沒有明顯過量，先增加日常活動通常比繼續砍食物更合理。",
            f"把每日平均步數先提高到約 {max(4500,min(7000,steps+1500)):,} 步，分散完成即可。")
    if p.get("protein") in ("none","half"):
        add("protein","蛋白質配置需要先補起來",5,
            "目前不是先把飯再減少，而是要確認正餐有足夠的蛋白質主菜，讓餐點結構更完整。",
            "每天至少 2 餐安排明確蛋白質主菜，每餐先做到約 1 掌心。")
    if p.get("vegetables") in ("none","few","half"):
        add("vegetables","蔬菜與餐點體積偏少",3,
            "蔬菜量偏少時，正餐的飽足感與飲食品質較難穩定。",
            "每天至少 2 餐把蔬菜提高到約 1 拳以上。")
    liquid=freq_score(p.get("sugary_drinks",""))+freq_score(p.get("alcohol",""))
    if liquid>=4:
        add("liquid","正餐之外的液體熱量值得優先檢查",4+min(2,liquid-4),
            "如果飲料或酒精頻率高，先處理這些通常比直接砍正餐更容易，也更能避免低估總攝取。",
            "這週把含糖飲／酒精頻率先減少約一半，其他正餐暫時不要一起大砍。")
    snack=freq_score(p.get("snacks",""))+freq_score(p.get("late_snack",""))
    if snack>=4:
        add("snack","零食／宵夜可能正在拉高額外攝取",4+min(2,snack-4),
            "額外進食若反覆出現，可能比單看三餐份量更值得優先處理。",
            "找出最常出現的零食或宵夜時段，這週先減少至少 2 次。")
    if (p.get("sleep_hours") or 99)<6 or p.get("sleep_quality")=="poor":
        add("sleep","睡眠可能讓飲食控制變得更困難",3,
            "睡眠不足不代表一定會胖，但可能讓飢餓、疲勞與高熱量食物偏好更難管理。",
            "先把平均睡眠增加約 30 分鐘，或固定一個較穩定的上床時間。")
    if (p.get("water") or 9999)<1200:
        add("water","飲水量偏低",2,"這不是主要減脂手段，但目前飲水確實偏低，可以當作基本生活調整。",
            "每日飲水先增加 300–500 ml。")
    restriction=(p.get("meals") or 3)<=1 or p.get("current_diet")=="very_low"
    if restriction:
        add("restriction","目前更需要避免『越卡越少吃』",8,
            "你已經有吃得非常少或跳餐的情況，這時候再把熱量往下砍，不適合當成第一反應。",
            "先不要再減餐或砍主食；連續記錄 3–7 天實際吃的內容、份量與飢餓感。")
    plateau=(p.get("diet_weeks") or 0)>=4 and abs(p.get("four_week_change") or 0)<.5
    if plateau:
        add("plateau","現在需要先找出停滯原因，而不是直接再減量",6,
            "你已執行一段時間但近 4 週變化很小。下一步應核對真實攝取、週末差異與活動量，再決定調整哪一邊。",
            "接下來完整記錄 7 天，包含正餐、飲料、零食、宵夜與週末，不刻意吃得更少。")
    return sorted(c,key=lambda x:x["score"],reverse=True)

def synthesize(p):
    cand=candidate_scores(p)
    # Always produce up to 3 meaningful priorities. If there are fewer, use observation priorities rather than inventing a deficit.
    fillers=[
      {"key":"pattern","title":"先確認真實飲食是否和問卷一致","score":1,
       "why":"目前問卷沒有顯示更多明顯問題，因此不硬湊『缺點』。接下來用實際餐點紀錄確認份量、烹調與週末差異。",
       "task":"至少記錄 3 天完整飲食，其中包含 1 個你最容易失控或外食較多的日子。",
       "evidence":["問卷目前沒有足夠證據支持更多明顯飲食問題"]},
      {"key":"consistency","title":"先看一週的一致性，不用追求單餐完美","score":1,
       "why":"體態變化看的是一段時間的總體模式。單餐吃得很乾淨，不能代表整週攝取一定合適。",
       "task":"記錄餐點時連飲料、零食與額外醬料一起寫，先建立真實基準。",
       "evidence":["需要用連續紀錄補足問卷無法看到的實際份量"]}]
    for x in fillers:
        if len(cand)>=3: break
        cand.append(x)
    priorities=cand[:3]

    # Contextual summary
    if priorities and priorities[0]["key"]=="restriction":
        summary="你目前最不需要做的，是因為體重卡住就繼續少吃。先確認實際攝取與身體狀況，再決定下一步。"
    elif priorities and priorities[0]["key"] in ("liquid","snack"):
        summary="目前比較值得先處理的是正餐以外反覆出現的攝取，而不是一開始就把每餐主食砍掉。"
    elif priorities and priorities[0]["key"]=="protein":
        summary="目前餐點結構比單純『吃更少』更值得先調整，先把蛋白質配置補完整。"
    elif priorities and priorities[0]["key"]=="plateau":
        summary="你現在的重點是找出為什麼卡住，不是直接把飲食再往下壓。"
    else:
        summary="目前沒有證據顯示你需要大幅減少食量；先處理最明顯的生活限制，再用實際飲食紀錄驗證。"
    return summary, priorities

def safety(p):
    red=[]
    cond=(p.get("conditions") or "").lower()
    if p.get("pregnant")=="yes": red.append("懷孕")
    if p.get("breastfeeding")=="yes": red.append("哺乳")
    for k,label in [("kidney","腎臟疾病"),("insulin","使用胰島素"),("eating","進食障礙相關風險")]:
        if k in cond or (k=="eating" and p.get("risk_eating")=="yes"): red.append(label)
    return red

@app.route("/")
def home():
    return render_template("home.html", profile=get_profile())

@app.route("/assessment", methods=["GET","POST"])
def assessment():
    if request.method=="POST":
        f=request.form
        fields=["name","sex","age","height","weight","body_fat","waist","goal","goal_weight","recent_change",
        "occupation","sitting_hours","steps","exercise_days","exercise_type","exercise_minutes","sleep_hours","sleep_quality",
        "meals","first_meal","last_meal","eating_out","breakfast","late_snack","sugary_drinks","coffee","alcohol","snacks",
        "trigger_food","starch","protein","vegetables","water","past_success","max_loss","regain","methods","current_diet",
        "diet_weeks","four_week_change","pregnant","breastfeeding","postpartum","conditions","meds","risk_eating"]
        nums={"age":int,"height":float,"weight":float,"body_fat":float,"waist":float,"goal_weight":float,"recent_change":float,
              "sitting_hours":float,"steps":int,"exercise_days":int,"exercise_minutes":int,"sleep_hours":float,"meals":int,
              "water":int,"max_loss":float,"diet_weeks":int,"four_week_change":float}
        data={}
        for x in fields:
            v=f.get(x,"").strip()
            if x in nums:
                try: v=nums[x](v) if v else None
                except: v=None
            data[x]=v
        data["user_key"]=user_key(); now=datetime.now().isoformat(timespec="seconds")
        c=db(); old=c.execute("SELECT id FROM profiles WHERE user_key=?",(user_key(),)).fetchone()
        if old:
            sets=",".join(f"{k}=?" for k in fields)+",updated_at=?"
            c.execute(f"UPDATE profiles SET {sets} WHERE user_key=?", [data[k] for k in fields]+[now,user_key()])
        else:
            cols=["user_key"]+fields+["created_at","updated_at"]
            c.execute(f"INSERT INTO profiles ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                      [data["user_key"]]+[data[k] for k in fields]+[now,now])
        c.commit(); c.close()
        return redirect(url_for("result"))
    return render_template("assessment.html", profile=get_profile() or {})

@app.route("/result")
def result():
    p=get_profile()
    if not p:return redirect(url_for("assessment"))
    summary,priorities=synthesize(p); red=safety(p)
    bmi=None
    if p.get("height") and p.get("weight"): bmi=round(p["weight"]/((p["height"]/100)**2),1)
    return render_template("result.html",p=p,summary=summary,priorities=priorities,red=red,bmi=bmi)

def ai_analyze(text=None, image_bytes=None, p=None):
    key=os.getenv("OPENAI_API_KEY")
    if not key:
        return "AI 尚未啟用。網站流程已可測試；設定 OPENAI_API_KEY 後即可進行真正的文字／照片餐點分析。"
    client=OpenAI(api_key=key)
    profile=f"""使用者資料：目標={p.get('goal')}; 身高={p.get('height')}cm; 體重={p.get('weight')}kg;
活動步數={p.get('steps')}; 每餐蛋白質習慣={p.get('protein')}; 澱粉={p.get('starch')}; 蔬菜={p.get('vegetables')};
注意：不可從照片假裝知道精確克數、油量或熱量。請以份量區間與飲食結構分析。"""
    prompt="""你是體態管理飲食紀錄助理，不診斷疾病、不開醫療飲食。請用繁體中文、口語但專業。
分析順序：
1. 辨識餐點（不確定就明說）
2. 用掌心/拳頭/碗等生活化單位估蛋白質、澱粉、蔬菜與脂肪來源，不假裝知道精確克數或熱量
3. 結合個人問卷，判斷這餐是否真的碰到他的主要卡點；不要看到飯就叫人減飯
4. 說明這餐做得好的地方
5. 只挑「最值得調整的一件事」
6. 給下一餐非常具體的方向
7. 若目前資訊不足，直接要求繼續記錄，不硬下結論
不要把單餐好壞等同減脂成敗，也不要使用羞辱、恐嚇或保證瘦身的語氣。"""
    content=[{"type":"input_text","text":profile+"\n"+prompt+"\n使用者輸入："+(text or "請分析照片")}]
    if image_bytes:
        b64=base64.b64encode(image_bytes).decode()
        content.append({"type":"input_image","image_url":"data:image/jpeg;base64,"+b64})
    r=client.responses.create(model=os.getenv("OPENAI_MODEL","gpt-5-mini"),input=[{"role":"user","content":content}])
    return r.output_text

@app.route("/meal",methods=["GET","POST"])
def meal():
    p=get_profile()
    if not p:return redirect(url_for("assessment"))
    analysis=None
    if request.method=="POST":
        text=request.form.get("content","").strip()
        meal_type=request.form.get("meal_type","其他")
        img=request.files.get("photo")
        b=img.read() if img and img.filename else None
        try: analysis=ai_analyze(text,b,p)
        except Exception as e: analysis="AI 分析暫時無法完成："+str(e)
        c=db(); c.execute("INSERT INTO meals(user_key,meal_type,source,content,analysis,created_at) VALUES(?,?,?,?,?,?)",
            (user_key(),meal_type,"photo" if b else "text",text,analysis,datetime.now().isoformat(timespec="seconds")))
        c.commit(); c.close()
    return render_template("meal.html",analysis=analysis)

@app.route("/week")
def week():
    p=get_profile()
    if not p:return redirect(url_for("assessment"))
    since=(datetime.now()-timedelta(days=7)).isoformat()
    c=db(); rows=c.execute("SELECT * FROM meals WHERE user_key=? AND created_at>=? ORDER BY created_at DESC",(user_key(),since)).fetchall(); c.close()
    issues,tasks=assess(p)
    return render_template("week.html",rows=rows,issues=issues,tasks=tasks)

@app.route("/reset")
def reset():
    session.clear()
    return redirect(url_for("home"))

@app.route("/health")
def health(): return {"status":"ok","version":"2"}

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",5000)),debug=True)
