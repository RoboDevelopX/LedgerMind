PY ?= python3
VENV ?= .venv
BIN := $(VENV)/bin
PORT ?= 8000

.PHONY: setup dev test demo check bench eval eval-memory eval-cat docker deploy clean

setup:  ## create a virtualenv and install everything
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -r requirements-dev.txt
	@test -f .env || cp .env.example .env
	@echo "Ready. Add NEBIUS_API_KEY to .env for live Nemotron (optional), then: make demo"

dev:  ## run the app with auto-reload
	$(BIN)/uvicorn ledgermind.server:app --reload --port $(PORT)

test:  ## offline test suite (models mocked)
	$(BIN)/pytest -q

demo:  ## load the 12-month demo ledger and start the app
	$(BIN)/python scripts/load_demo.py --reset
	$(BIN)/uvicorn ledgermind.server:app --port $(PORT)

check:  ## verify the API key and Nemotron model IDs against Token Factory
	$(BIN)/python scripts/check_models.py

bench:  ## Nano vs Super vs Ultra latency/cost on the same task (live)
	$(BIN)/python scripts/benchmark.py

eval:  ## finance Q&A eval with tool and number checks
	$(BIN)/python eval/run_eval.py

eval-memory:  ## memory-extraction precision/recall per model (live)
	$(BIN)/python eval/memory_eval.py

eval-cat:  ## categorisation accuracy per model: rules vs model vs both (live)
	$(BIN)/python eval/categorisation_eval.py

docker:  ## build and run the container on http://localhost:$(PORT)
	docker build -t ledgermind .
	docker run --rm -p $(PORT):8000 --env-file .env -v ledgermind-data:/data ledgermind

deploy:  ## build, push and run on a Nebius AI Cloud serverless endpoint (see docs/deploy_nebius.md)
	./scripts/deploy_nebius.sh

clean:
	rm -rf $(VENV) .pytest_cache ledgermind.db data
