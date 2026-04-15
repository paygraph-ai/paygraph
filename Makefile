.DEFAULT_GOAL := help

.PHONY: help format lint test check

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

format:  ## Auto-format with ruff (style + import sorting)
	uv run ruff check --select I --fix src/ tests/
	uv run ruff format src/ tests/

lint:    ## Lint with ruff
	uv run ruff check src/ tests/

test:    ## Run pytest
	uv run pytest tests/ -v

check: lint test  ## CI entrypoint (lint + test)
