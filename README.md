# TDJ AI 餐盤教練 1.4 — AI 教練決策＋飲水目標版

- 飲水預設改為體重 × 35 ml
- 會員可自行修改每日飲水目標
- AI 同時看今天已吃、剩餘額度、餐別、吃前/吃後、飢餓與飽足
- 不再因單餐未達一天1/3就判定不足
- 已吃完且很飽，不再叫會員立刻硬補食物
- 每次只抓1–2個最值得調整的地方
- 下一餐改成營養範圍＋台灣常見外食例子
- 本餐後剩餘 kcal/P/C/F 由系統自行計算
- 顧客端不再顯示 OpenAI/API 原始錯誤
- 移除已不用的 7 天二次評估舊路由
- 保留 PostgreSQL、LINE 會員、使用期限、每日次數限制、飲水紀錄

Render Start: `gunicorn app:app --timeout 120`
部署後 `/health` 應顯示 version = meal-coach-1.4、database = postgres。
