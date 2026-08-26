# TDJ AI 餐盤教練 2.1.0 RELEASE

以 2.0.4 定案版為基底新增：
- 原始餐點照片留存：前台最近紀錄與後台學員頁皆可回看，同餐最多 5 張。
- 30/60/90 天方案開始日、目前第幾天與剩餘天數。
- 後台 7/14/21/30 天/全部區間切換。
- 顧問帳號、顧問登入、學員歸屬與後端權限隔離。
- 每位顧問專屬邀請連結；已綁定學員不會因誤點其他邀請自動換顧問。
- TDJ 總管理員可查看全部學員、指定/轉移顧問。
- 顧問內部備註。

## Render
Root Directory: `TDJ_AI_Meal_Coach_2_1_0_RELEASE`
Build: `pip install -r requirements.txt`
Start: `gunicorn app:app`

沿用原本環境變數：`DATABASE_URL`、`OPENAI_API_KEY`、`LINE_LOGIN_CHANNEL_ID`、`LINE_LOGIN_CHANNEL_SECRET`、`ADMIN_TOKEN`、`SECRET_KEY`。

## 後台
總管理員仍由 `/admin?token=你的ADMIN_TOKEN` 進入。可在總後台建立顧問，建立後會顯示該顧問的專屬邀請連結。顧問由 `/consultant/login` 登入。

## 照片儲存
2.1.0 為了不要求你另外申請第三方圖片服務，照片與餐點關聯後保存在既有持久化資料庫的 `meal_photos` 表，前後台都直接顯示，不需要到別處查看。若未來學員/照片量大幅增加，再遷移到物件儲存即可，畫面與使用流程不需改變。
