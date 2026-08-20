# TDJ 體態管理飲食評估 AI — V1

這是一個可直接執行的 Flask 網頁＋LINE Messaging API webhook 原型。

## 已完成
- 初次體態評估表
- 健康紅旗攔截
- 文字飲食分析
- 餐點照片 AI 分析（需 OpenAI API key）
- 每餐紀錄 SQLite
- 近 7 天摘要
- LINE OA 接收文字
- LINE OA 接收照片並下載內容
- LINE webhook 簽章驗證
- 手機版介面
- 沒有 OpenAI key 時仍可用基礎文字規則測試

## 本機啟動
1. 安裝 Python 3.11+
2. 在資料夾中執行：
   pip install -r requirements.txt
3. 複製 `.env.example` 為 `.env`
4. 執行：
   python app.py
5. 瀏覽器開啟：http://127.0.0.1:5000

## 啟用 AI 照片分析
在 `.env` 填：
OPENAI_API_KEY=你的金鑰
OPENAI_MODEL=可接受圖片輸入的模型名稱

## 接 LINE OA
在 `.env` 填：
LINE_CHANNEL_ACCESS_TOKEN=...
LINE_CHANNEL_SECRET=...
PUBLIC_BASE_URL=https://你的公開網址

LINE Developers Console 的 Webhook URL 設：
https://你的公開網址/line/webhook
並啟用 Use webhook。

### 重要
LINE 只會把 webhook 傳到可公開連線且有 HTTPS 的網址，所以本機 `127.0.0.1` 無法直接讓真實 LINE OA 使用。需要部署到 Render、Railway、Fly.io、Cloud Run 等任一公開主機。

## 使用者身分
網頁測試版用 `external_id`。LINE 版則直接以 LINE userId 當 external_id。
正式版建議再做「LINE LIFF 初次評估頁」，就能自動帶 userId，不必讓客人自己輸入識別碼。

## 下一版建議
- LIFF 自動綁定 LINE userId
- Rich Menu：開始評估／拍照分析／文字輸入／本週分析／體重回報／真人顧問
- 每餐結構 JSON 化，週報可統計蛋白質/蔬菜/含糖飲等出現頻率
- 體重、體脂、腰圍趨勢圖
- 顧問後台與人工複審佇列
- 使用者資料刪除、隱私告知與同意
