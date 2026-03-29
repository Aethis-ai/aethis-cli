.PHONY: install test test-e2e lint clean

install:
	pip install -e ".[dev]"

test:
	python -m pytest -v

test-e2e:
	python -m pytest tests/e2e/ -m manual -v -s

lint:
	python -m ruff check aethis_cli/ tests/

clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache __pycache__
	find . -type d -name __pycache__ -exec rm -rf {} +
