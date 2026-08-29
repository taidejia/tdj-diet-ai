# TDJ AI 餐盤教練 2.3.9 RELEASE

基於 2.3.8，只修正版本顯示同步機制，不改既有餐點分析、長餐點重試、多圖辨識、後台每日分組、LINE、顧問權限、飲食與身體追蹤功能。

## 2.3.9 變更
- 新增單一 `APP_VERSION = "2.3.9"`。
- 前台左上角版本號由 `app_version` 自動顯示，不再寫死在模板。
- `/health` 也由同一個 `APP_VERSION` 產生版本字串。
- 後續升版只需更新一個版本變數，即可同步前台與 health。

## Render
- Root Directory: `TDJ_AI_Meal_Coach_2_3_9_RELEASE`
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn app:app`
