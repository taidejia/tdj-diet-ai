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

## 2.4.0 — 帳號資格管理
- 總管理後台新增學員狀態分類：有效／已到期／已停用／全部，預設只顯示有效學員。
- 學員可手動停用；資料完整保留。原有「儲存方案／歸屬」在到期或停用學員上可直接重新啟用並設定新方案。
- 學員永久刪除提供二次確認，會刪除該學員會員、飲食、照片、身體紀錄、喝水、斷食、追蹤與顧問筆記等資料。
- 顧問可停用／重新啟用。停用後不能登入，但歷史歸屬與資料保留。
- 顧問永久刪除提供二次確認；會解除名下學員歸屬，但不刪除學員資料。
- 顧問後台學員卡新增有效／已到期／已停用狀態標示。

Render Root Directory: `TDJ_AI_Meal_Coach_2_4_0_RELEASE`
