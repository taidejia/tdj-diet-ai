import os, json, base64, time, gc, traceback
from datetime import date
import app as core

POLL_SECONDS=float(os.getenv("JOB_POLL_SECONDS","1.5"))

def totals_for(user_key, day):
    start,end=core.taipei_day_utc_bounds(day); c=core.db()
    rows=c.execute("SELECT calories,protein_g,carbs_g,fat_g,veg_fists,water_ml,meal_type FROM meals WHERE user_key=? AND created_at>=? AND created_at<? AND deleted_at IS NULL ORDER BY created_at",(user_key,start,end)).fetchall()
    wr=c.execute("SELECT amount_ml FROM hydration_logs WHERE user_key=? AND created_at>=? AND created_at<?",(user_key,start,end)).fetchall(); c.close()
    return {"calories":sum((r["calories"] or 0) for r in rows),"protein":sum((r["protein_g"] or 0) for r in rows),"carbs":sum((r["carbs_g"] or 0) for r in rows),"fat":sum((r["fat_g"] or 0) for r in rows),"veg":sum((r["veg_fists"] or 0) for r in rows),"water":sum((r["water_ml"] or 0) for r in rows)+sum((r["amount_ml"] or 0) for r in wr),"meal_count":len(rows),"last_meal_type":rows[-1]["meal_type"] if rows else None}

def claim_job():
    if not core.USE_POSTGRES: raise RuntimeError("Background worker requires DATABASE_URL/PostgreSQL")
    c=core.db()
    # psycopg transaction + SKIP LOCKED makes this safe if more workers are added later.
    row=c.execute("SELECT id FROM analysis_jobs WHERE status='queued' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
    if not row: c.commit(); c.close(); return None
    jid=row["id"]
    c.execute("UPDATE analysis_jobs SET status='processing',started_at=? WHERE id=?",(core.db_now_iso(),jid)); c.commit()
    job=c.execute("SELECT * FROM analysis_jobs WHERE id=?",(jid,)).fetchone(); c.close(); return dict(job)

def profile_for(user_key):
    c=core.db(); r=c.execute("SELECT * FROM profiles WHERE user_key=?",(user_key,)).fetchone(); c.close()
    return dict(r) if r else None

def job_photos(job_id):
    c=core.db(); rows=c.execute("SELECT data_url FROM analysis_job_photos WHERE job_id=? ORDER BY id",(job_id,)).fetchall(); c.close()
    out=[]
    for r in rows:
        try: out.append(base64.b64decode((r["data_url"] or "").split(",",1)[1]))
        except: pass
    return out

def fmtveg(v):
    v=float(v or 0); return str(int(v)) if v.is_integer() else str(v)

def process(job):
    payload=json.loads(job["payload"] or "{}")
    p=profile_for(job["user_key"])
    if not p: raise RuntimeError("找不到學員初始設定")
    record_day=date.fromisoformat(payload.get("record_day"))
    raws=job_photos(job["id"])
    pre=totals_for(job["user_key"],record_day)
    analysis,error=core.ai_analyze(payload.get("text") or "",raws,p,payload.get("timing") or "before",payload.get("hunger_before"),payload.get("fullness_after"),pre,payload.get("meal_type") or "其他")
    if not analysis: raise RuntimeError(error or "AI 未回傳分析結果")
    analysis["source_label"]="包裝營養標示" if analysis.get("nutrition_label_used") else ("照片估算" if raws else "文字估算")
    dp=analysis.get("daily_progress") or {}; after=analysis.get("today_after") or {}
    parts=[]
    if payload.get("is_backfill"): parts.append("紀錄方式：隔日補登")
    parts += [f"當時體態目標：{p.get('goal','減脂')}",f"餐點判斷：{analysis.get('overall','')}",f"本餐估算：約 {analysis.get('calories_estimate',analysis.get('calories',0)):.0f} kcal",f"本餐營養：蛋白質 {analysis.get('protein_g',0):.0f}g｜碳水 {analysis.get('carbs_g',0):.0f}g｜脂肪 {analysis.get('fat_g',0):.0f}g｜蔬菜 {fmtveg(analysis.get('veg_fists',0))}拳"]
    if analysis.get('good'): parts.append(f"這餐有做到的地方：{analysis.get('good')}")
    parts.append(f"這餐最需要調整：{analysis.get('advice','')}")
    if analysis.get('daily_priority'): parts.append(f"當時今天最優先：{analysis.get('daily_priority')}")
    if analysis.get('next_meal'): parts.append(f"當時下一餐建議：{analysis.get('next_meal')}")
    if analysis.get('estimate_note'): parts.append(f"估算說明：{analysis.get('estimate_note')}")
    recorded_at=core.meal_recorded_at(record_day)
    source=("photo" if raws else "text")+("_backfill" if payload.get("is_backfill") else "")
    c=core.db(); cur=c.execute("""INSERT INTO meals(user_key,meal_type,source,content,analysis,created_at,hunger_before,fullness_after,water_ml,note,calories,protein_g,carbs_g,fat_g,veg_fists,estimate_note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id""",(job["user_key"],payload.get("meal_type") or "其他",source,payload.get("text") or "","\n".join(parts),recorded_at,payload.get("hunger_before"),payload.get("fullness_after"),payload.get("water_ml"),payload.get("note") or "",analysis.get("calories"),analysis.get("protein_g"),analysis.get("carbs_g"),analysis.get("fat_g"),analysis.get("veg_fists"),analysis.get("estimate_note")))
    meal_id=cur.fetchone()["id"]
    photos=c.execute("SELECT data_url FROM analysis_job_photos WHERE job_id=? ORDER BY id",(job["id"],)).fetchall()
    for ph in photos: c.execute("INSERT INTO meal_photos(meal_id,user_key,data_url,created_at) VALUES(?,?,?,?)",(meal_id,job["user_key"],ph["data_url"],recorded_at))
    c.execute("DELETE FROM analysis_job_photos WHERE job_id=?",(job["id"],))
    c.execute("UPDATE analysis_jobs SET status='done',result_meal_id=?,finished_at=?,error=NULL WHERE id=?",(meal_id,core.db_now_iso(),job["id"]))
    c.commit(); c.close(); raws.clear(); gc.collect()
    core.app.logger.info("ANALYSIS_JOB done id=%s meal_id=%s",job["id"],meal_id)

def fail(job, exc):
    msg=str(exc)[:500]
    c=core.db(); c.execute("UPDATE analysis_jobs SET status='failed',error=?,finished_at=? WHERE id=?",(msg,core.db_now_iso(),job["id"])); c.execute("DELETE FROM analysis_job_photos WHERE job_id=?",(job["id"],)); c.execute("DELETE FROM submission_tokens WHERE token=? AND user_key=?",(job["submission_token"],job["user_key"])); c.commit(); c.close()
    core.app.logger.error("ANALYSIS_JOB failed id=%s error=%s",job["id"],msg)

if __name__=='__main__':
    print('TDJ background analysis worker 2.4.13 started', flush=True)
    while True:
        job=None
        try:
            job=claim_job()
            if job: process(job)
            else: time.sleep(POLL_SECONDS)
        except Exception as e:
            traceback.print_exc()
            if job:
                try: fail(job,e)
                except Exception: traceback.print_exc()
            time.sleep(2)
