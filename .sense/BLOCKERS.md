# Blockers

None. The five-attempt rule never triggered.

Fallbacks used (see DECISIONS.md D29, D31):
- Playwright not installed: API-driven end-to-end (`demo:verify`) plus Testing Library tests against the real server.
- Webcam model assets not vendored: switch scanning + keyboard + dwell + scripted driver; webcam device reports itself unavailable.
