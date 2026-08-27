# TDJ AI 餐盤教練 2.3.3 RELEASE

基底：2.3.2 RELEASE。

本版只重整「第一次使用」與「我的設定」流程：
- 新會員有有效使用資格、但尚未建立 profile 時，首頁自動導向 `/onboarding`。
- `/onboarding` 為一次性的四步初始設定頁：基本資料 → 體態目標 → 活動/安全資料 → 確認。
- 四種體態目標均提供白話說明，並特別解釋減重與減脂差異。
- 已有 profile 的舊會員不會被重新要求跑初始設定。
- 右上角「每日設定」改為「我的設定」，連到 `/settings`。
- 每日身體反應仍從首頁「今日身體紀錄」進入。
- `/assessment` 保留相容舊連結，會依是否已有 profile 自動轉往 onboarding/settings。
- 原 2.3.2 餐點分析、照片、飲水、168、身體反應、顧問分權等邏輯不重寫。

Render Root Directory：`TDJ_AI_Meal_Coach_2_3_3_RELEASE`
Health：`meal-coach-2.3.3`
