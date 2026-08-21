# TDJ AI 餐盤教練 1.5.1 — 照片分析修正版

- 延續 1.5：防重複送出、軟刪除、累計回復、AI timeout。
- 上傳照片先自動旋轉校正、縮到長邊 1280px、轉標準 JPEG、品質 82。
- 避免 iPhone/不同圖片格式被錯誤硬標成 JPEG。
- 文字 AI timeout 預設 30 秒；照片預設 45 秒。
- Render Log 會用 WARNING 顯示照片壓縮前後大小與 AI 分析耗時。

Render Build: `pip install -r requirements.txt`
Start: `gunicorn app:app --timeout 120`

可選環境變數：
- OPENAI_TIMEOUT_SECONDS=30
- OPENAI_PHOTO_TIMEOUT_SECONDS=45
