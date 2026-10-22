# Linux/macOS task runner. On Windows use tasks.ps1 (same targets).
PY ?= .venv/bin/python
PYLAYA ?= .venv-laya/bin/python
CONFIG ?= config/default.yaml

.PHONY: setup setup-laya laya-serve verify-api verify-laya fetch-data test lint backtest shadow report

setup:
	python3.12 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.lock
	$(PY) -m pip install --no-deps -e .

setup-laya:
	python3.12 -m venv .venv-laya
	$(PYLAYA) -m pip install -r requirements-laya.lock --extra-index-url https://download.pytorch.org/whl/cpu

laya-serve:
	LAYA_HOST=127.0.0.1 LAYA_PORT=8000 LAYA_MODELS=english LAYA_DEFAULT_MODEL=english LAYA_DEVICE=cpu \
	  .venv-laya/bin/laya-serve

verify-api:
	$(PY) -m wallex_agent verify-api
	$(PY) -m wallex_agent verify-llm

verify-laya:
	$(PY) -m wallex_agent verify-laya

fetch-data:
	$(PY) -m wallex_agent fetch-data --config $(CONFIG)

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check src tests

backtest:
	$(PY) -m wallex_agent backtest --config $(CONFIG)

shadow:
	$(PY) -m wallex_agent shadow --config $(CONFIG)

report:
	$(PY) -m wallex_agent report
