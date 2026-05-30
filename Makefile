PY ?= python
RUFF ?= ruff
BLACK ?= black
MYPY ?= mypy
PYTEST ?= pytest

PKG_DIRS := packages apps

.PHONY: help install lint format-check types test verify clean

help:
	@echo "Targets:"
	@echo "  install       - pip install -e '.[dev]'"
	@echo "  lint          - ruff check ."
	@echo "  format-check  - black --check ."
	@echo "  types         - mypy --strict $(PKG_DIRS)"
	@echo "  test          - pytest -q"
	@echo "  verify        - lint + format-check + types + test (the CI gate)"
	@echo "  clean         - drop caches and build artefacts"

install:
	$(PY) -m pip install -e ".[dev]"

lint:
	$(RUFF) check .

format-check:
	$(BLACK) --check .

types:
	$(MYPY) --strict $(PKG_DIRS)

test:
	$(PYTEST) -q

verify: lint format-check types test

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis \
	       *.egg-info **/__pycache__
