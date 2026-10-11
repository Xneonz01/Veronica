# Thin wrapper around tasks.py (the canonical, cross-platform runner).
# On Windows without GNU make, run `python tasks.py <target>` directly.
.PHONY: check lint format-check typecheck test fe-build

check:
	python tasks.py check

lint:
	python tasks.py lint

format-check:
	python tasks.py format-check

typecheck:
	python tasks.py typecheck

test:
	python tasks.py test

fe-build:
	python tasks.py fe-build
