# TDJ AI 餐盤教練 1.1 LINE 會員權限版
新增 LINE Login、未開通禁用、後台 +30/+60/+90 天、每日預設 6 次 AI 上限、客戶餐點歸戶。
Render 需設定 OPENAI_API_KEY、SECRET_KEY、LINE_LOGIN_CHANNEL_ID、LINE_LOGIN_CHANNEL_SECRET、ADMIN_TOKEN。
LINE Callback URL：`https://你的網址/line/callback`
管理後台：`https://你的網址/admin?token=你的ADMIN_TOKEN`
重要：目前 SQLite 僅適合測試。正式營運前要換持久化資料庫，避免 Render 重新部署造成資料遺失。
