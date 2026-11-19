.PHONY: install lint test check

install:
	pip install -e ".[dev]" -e packages/xcalibur-echolas

lint:
	ruff check .

test:
	pytest

check: lint test
