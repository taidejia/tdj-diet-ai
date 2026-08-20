# TDJ 體態管理飲食評估 AI V2.1

## Render
Build Command:
`pip install -r requirements.txt`

Start Command:
`gunicorn app:app`

## V2
- 5 步驟初次評估
- 不顯示 demo 識別碼
- 飲食／生活／減重史／安全篩檢
- 自動找前三大卡點
- 第一週 3 個任務
- 文字與照片 AI 餐點分析
- 7 天紀錄頁

## AI
在 Render Environment Variables 加入 `OPENAI_API_KEY` 後，餐點 AI 才會真正啟用。

## V2.1
- 結果固定產出 3 個有意義的優先方向；證據不足時不硬湊缺點
- 每個優先方向顯示判斷證據
- 停滯、過度限制、液體熱量、零食宵夜、蛋白質、活動量等交叉排序
- 餐點 AI 會結合個人問卷，不再看到飯就一律叫人減飯
