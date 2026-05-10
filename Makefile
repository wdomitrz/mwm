PYTHON ?= uv run --with typer python3
PYRIGHT_FILES := m3.py template.py typings

.PHONY: all lint fix test

all: fix lint test

lint:
	ruff check .
	basedpyright --project pyproject.toml --level error $(PYRIGHT_FILES)

fix:
	ruff check --extend-select I --fix-only --fix .
	ruff format .

test:
	$(PYTHON) -m doctest README.md $(wildcard *.py)
