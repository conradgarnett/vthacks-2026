.PHONY: help setup precache dev backend client test lint demo doctor clean
.DEFAULT_GOAL := help

VENV    := .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
PYTHON_VERSION := 3.12

help:
	@echo "VisionOS -- digital sense of sight"
	@echo ""
	@echo "  make setup     Create venv + install backend and client deps"
	@echo "  make precache  Download ML weights now (do this BEFORE demo day)"
	@echo "  make doctor    Diagnose credentials, weights, and ports"
	@echo "  make dev       Run backend + client (HTTPS, LAN-accessible)"
	@echo "  make test      Run the test suite"
	@echo "  make demo      Deterministic replay mode -- no network, no keys"

# uv pins 3.12 deliberately: torch/ultralytics wheels are still unreliable on
# 3.13, and a wheel build failure mid-hackathon is not a recoverable event.
setup:
	uv venv --python $(PYTHON_VERSION) $(VENV)
	$(VENV)/bin/python -m ensurepip --upgrade 2>/dev/null || true
	uv pip install --python $(PY) -r requirements.txt
	cd client && npm install
	@test -f .env || cp .env.example .env
	@echo ""
	@echo "Setup complete. Next: make precache && make doctor"

precache:
	@echo "Downloading model weights so a dead network can't kill perception..."
	$(PY) -c "from ultralytics import YOLO; YOLO('yolo11n.pt')"
	$(PY) -c "from transformers import pipeline; pipeline('depth-estimation', model='depth-anything/Depth-Anything-V2-Small-hf')"
	@echo "Weights cached."

doctor:
	@$(PY) -m backend.doctor

dev:
	@$(MAKE) -j2 backend client

backend:
	$(PY) -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

client:
	cd client && npm run dev -- --host

test:
	$(PY) -m pytest backend/tests -v

lint:
	$(PY) -m compileall -q backend ../shared

demo:
	VISION_PROVIDER=replay DEMO_MODE=true $(MAKE) dev

clean:
	rm -rf $(VENV) client/node_modules client/dist
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
