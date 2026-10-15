PYTHON ?= $(shell if test -x .venv/bin/python; then printf .venv/bin/python; else printf python3; fi)
APP = deal-grandpa
DB ?= var/deal_grandpa.sqlite3
FIXTURE ?= tests/fixtures/golden_offers.json

.PHONY: test run api demo verify-log purge docker-up

test:
	ADVISOR=none $(PYTHON) -m pytest

run:
	PYTHONPATH=src $(PYTHON) -m deal_grandpa.cli demo --fixture $(FIXTURE) --query "I need a phone under 30000 with 8GB RAM 128GB storage"

api:
	PYTHONPATH=src $(PYTHON) -m deal_grandpa.api.app

demo: run

verify-log:
	PYTHONPATH=src $(PYTHON) -m deal_grandpa.cli verify-log --db $(DB)

purge:
	PYTHONPATH=src $(PYTHON) -m deal_grandpa.cli purge --db $(DB)

docker-up:
	docker compose up --build
