# TDJ AI 餐盤教練 1.0
功能：每日熱量/P/C/F起始目標、照片/文字餐點分析、當餐營養素估算、蔬菜份量、每日累計與剩餘額度、吃前/吃後建議、最近7天紀錄。
Render Build: `pip install -r requirements.txt`
Start: `gunicorn app:app`
Environment: `OPENAI_API_KEY`, `SECRET_KEY`，可選 `OPENAI_MODEL`
照片分析屬估算；提供食物重量或營養標示時較準。
