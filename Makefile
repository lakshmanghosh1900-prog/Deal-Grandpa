PYTHON ?= $(shell if test -x .venv/bin/python; then printf .venv/bin/python; else printf python3; fi)
APP = deal-grandpa
DB ?= var/deal_grandpa.sqlite3
FIXTURE ?= tests/fixtures/golden_offers.json
IMAGE ?= deal-grandpa:demo

.PHONY: test run api demo verify-log purge docker-up docker-test

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

docker-test:
	docker compose build
	docker run --rm --network none -e ADVISOR=none -e DEAL_GRANDPA_NOW=2026-10-16T12:00:00Z -e DEAL_GRANDPA_DB=/tmp/deal-grandpa-test.sqlite3 -e DEAL_GRANDPA_FIXTURE=/app/tests/fixtures/golden_offers.json $(IMAGE) sh -c 'python tests/docker_network_probe.py && python -m pytest'
