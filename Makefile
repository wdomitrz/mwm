PYTHON ?= python3
PYRIGHT ?= uv run --with basedpyright basedpyright
PYRIGHT_FILES := m3.py

.PHONY: all lint fix test

all: fix lint test

lint:
	ruff check .
	$(PYRIGHT) --project pyproject.toml --level error $(PYRIGHT_FILES)

fix:
	ruff check --extend-select I --fix-only --fix .
	ruff format .

test:
	$(PYTHON) -m doctest README.md $(wildcard *.py)
