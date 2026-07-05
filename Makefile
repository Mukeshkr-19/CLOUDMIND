PYTHON ?= python3
VENV_PYTHON ?= venv/bin/python

.PHONY: compile test compose-check security-audit verify

$(VENV_PYTHON):
	$(PYTHON) -m venv venv
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -r requirements.txt

compile:
	$(PYTHON) -m compileall microservices inframirror tests

test: $(VENV_PYTHON)
	$(VENV_PYTHON) -m unittest discover -s tests

compose-check:
	docker compose config --quiet

security-audit: $(VENV_PYTHON)
	$(VENV_PYTHON) -m pip install pip-audit
	$(VENV_PYTHON) -m pip_audit -r requirements.txt

verify: compile test compose-check
