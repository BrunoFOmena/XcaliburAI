.PHONY: install lint test check e2e

install:
	pip install -e ".[dev]" -e packages/xcalibur-echolas

lint:
	ruff check .

test:
	pytest

check: lint test

e2e:
	pytest -m e2e -v
