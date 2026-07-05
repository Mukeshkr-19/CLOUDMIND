PYTHON ?= python3
VENV_PYTHON ?= venv/bin/python

.PHONY: compile test compose-check security-audit verify

compile:
	$(PYTHON) -m compileall microservices inframirror tests

test:
	$(VENV_PYTHON) -m unittest discover -s tests

compose-check:
	docker compose config --quiet

security-audit:
	$(VENV_PYTHON) -m pip install pip-audit
	$(VENV_PYTHON) -m pip_audit -r requirements.txt

verify: compile test compose-check
