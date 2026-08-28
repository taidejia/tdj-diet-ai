# 2.3.5 build checks

- Base: 2.3.4 RELEASE
- Python syntax compile: PASS (`python -m py_compile app.py`)
- Multi-image helper presence: PASS
- Per-image evidence + final merge fields present: PASS
- Version/health string updated to `meal-coach-2.3.5`: PASS
- Single-image path remains without extra pre-read calls: PASS by code inspection
- Multi-image pre-reads run concurrently (up to 5 images): PASS by code inspection
- Flask/Jinja runtime test: NOT RUN in this environment because Flask is not installed here.
- Live OpenAI multimodal test: NOT RUN because deployment API credentials are not available in this environment.

Recommended deployment test:
1. Upload the Korean nutrition-label photo alone: confirm it still parses.
2. Upload the Korean label + the two Chinese-label photos together: confirm all three items are represented in the final components/totals unless the model can justify a true duplicate.
3. Upload the same food from two angles + one nutrition-label photo for that same product: confirm it counts once, not three times.
