.PHONY: watch
watch:
	axe src/**/*.py -- uv run measuring-spoon -- build --show

.PHONY: build
build:
	axe src/**/*.py -- uv run measuring-spoon -- build

.PHONY: setup
setup:
	uv sync
	uv run pre-commit install
