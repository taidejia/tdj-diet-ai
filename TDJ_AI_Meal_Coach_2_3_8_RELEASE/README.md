# TDJ AI 餐盤教練 2.3.8 RELEASE

基於 2.3.7，修正「一次輸入很多餐點品項時，AI JSON 可能因固定輸出上限被截斷」的問題。

## 2.3.8 變更
- 餐點分析輸出額度改為依文字品項數／多圖複雜度動態調整。
- 一般餐保留較精簡額度；6、10、16 個以上品項會逐級提高。
- 若第一次回傳為不完整或 JSON 無法解析，系統會自動再試一次，使用更大的輸出額度。
- 不要求使用者重新按「分析這一餐」。
- 不刪減長餐點的食品品項來換取較短輸出。
- 保留 2.3.7 既有多圖逐張辨識、後台每日分組、LINE、顧問權限、飲食與身體追蹤功能。

## Render
Root Directory: `TDJ_AI_Meal_Coach_2_3_8_RELEASE`

Build Command: `pip install -r requirements.txt`

Start Command: `gunicorn app:app`
