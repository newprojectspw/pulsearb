PY ?= .venv/bin/python

.PHONY: check lint typecheck test venv

venv:
	uv venv --python 3.12 .venv
	uv pip install --python $(PY) -e ".[dev]"

lint:
	$(PY) -m ruff check src tests scripts

typecheck:
	$(PY) -m mypy

test:
	$(PY) -m pytest

check: lint typecheck test
