.PHONY: bootstrap install test compile worker compose-config secrets n8n-import

bootstrap:
	./scripts/bootstrap_dagent.sh

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

n8n-import:
	./scripts/n8nctl import-workflows

secrets:
	./scripts/generate_secrets.sh
