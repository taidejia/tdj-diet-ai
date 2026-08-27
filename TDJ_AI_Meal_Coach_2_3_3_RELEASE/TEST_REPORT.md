# 2.3.3 build checks

- Python `py_compile`: PASS
- Jinja template parse: PASS
- ZIP integrity: performed after packaging
- Flask runtime: not claimed unless available in build environment

Recommended deployment checks:
1. 新的 active LINE 會員、尚無 profile → `/` 自動進 `/onboarding`。
2. 四步流程未填 required 欄位不能下一步。
3. 四個目標卡均可選，減重/減脂說明清楚。
4. 完成後建立 profile 並進結果頁。
5. 舊會員直接進首頁，不被重新導向 onboarding。
6. 右上角顯示「我的設定」，修改後可重新計算目標。
7. 首頁「今日身體紀錄」與既有 2.3.2 功能正常。
