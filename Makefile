SOURCEPATH := $(PWD)/fastapi_utils
VIRTUALENV := $(PWD)/.venv

export PATH := $(VIRTUALENV)/bin:$(PATH)

# Fix Make < 3.81 (macOS and old Linux distros)
ifeq ($(filter undefine,$(value .FEATURES)),)
SHELL = env PATH="$(PATH)" /bin/bash
endif

.PHONY: .env .venv

build:
	python3 -m build

.env:
	echo 'PYTHONPATH="$(SOURCEPATH)"' > .env

.venv:
	python3 -m venv $(VIRTUALENV)
	pip install --upgrade pip

install-code-ext:
	if which code >/dev/null 2>&1; then code --install-extension ms-python.python --force; fi
	if which code >/dev/null 2>&1; then code --install-extension charliermarsh.ruff --force; fi

install-hook:
	echo "make lint" > .git/hooks/pre-commit
	chmod +x .git/hooks/pre-commit

install-dev: .venv .env install-hook install-code-ext
	if [ -f requirements-dev.txt ]; then pip install -r requirements-dev.txt; fi

lint:
	ruff check
	ruff format --check

format:
	ruff check --select I --fix
	ruff format

test:
	pytest --disable-warnings -x -n auto --cov-report=xml --cov=$(SOURCEPATH) tests/

coverage: test coverage.xml
	coverage report --omit=tests/* -m --fail-under=100

clean:
	rm -rf .pytest_cache .ruff_cache .coverage* coverage.* *.egg-info dist
	find $(SOURCEPATH) -name __pycache__ | xargs rm -rf
	find tests -name __pycache__ | xargs rm -rf
