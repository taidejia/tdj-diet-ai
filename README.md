# TDJ AI 餐盤教練 1.6

本版在 1.4 基礎上新增：
- 分析按鈕送出後立即鎖定，避免連點。
- 每次表單使用一次性 token；同一頁重複送出不會新增第二筆餐點。
- AI 呼叫預設 60 秒 timeout（可用 `OPENAI_TIMEOUT_SECONDS` 調整），並在 Render Log 記錄分析耗時。
- 使用者可在「最近紀錄」刪除誤記餐點。
- 管理後台客戶頁可刪除餐點。
- 採軟刪除：刪除資料不再計入今日熱量、P/C/F、飲水、分析次數與最近紀錄，但資料庫仍保留 `deleted_at`。
- 健康檢查版本：`meal-coach-1.6`。

Render Build：`pip install -r requirements.txt`
Start：`gunicorn app:app`

環境變數沿用 1.4；另可選：`OPENAI_TIMEOUT_SECONDS=30`。


## 1.6
- 支援只上傳照片、不填文字直接分析。
- 照片/文字分析預設 timeout 60 秒。
- AI 失敗或 timeout 不建立餐點、不扣每日分析次數，表單內容保留可重試。
- 前端分析中鎖定按鈕，避免連點重複送出。


## 1.6.3 Photo Pipeline
- Default model changed to gpt-5.4-mini for faster multimodal analysis (OPENAI_MODEL can still override it).
- Increased output budget to avoid an empty visible answer when structured output is incomplete.
- Never calls json.loads on an empty response.
- Logs PHOTO_PREP, OPENAI_REQUEST, PARSE, response status/incomplete details, and total request timing.
- Nutrition formulas/database schema/UI are otherwise unchanged.
