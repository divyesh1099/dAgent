.PHONY: install test compile worker compose-config secrets

install:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -e 'worker[dev]'

test:
	. .venv/bin/activate && pytest worker/tests -q

compile:
	python3 -m compileall worker/src

worker:
	. .venv/bin/activate && uvicorn dagent_worker.main:app --host 127.0.0.1 --port 8765

compose-config:
	docker compose --env-file docker/automation-stack/.env.example -f docker/automation-stack/compose.yml config

secrets:
	./scripts/generate_secrets.sh

