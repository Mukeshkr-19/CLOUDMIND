PYTHON ?= python3
VENV_PYTHON ?= venv/bin/python

.PHONY: compile test pytest coverage lint type-check compose-check security-audit verify dev-setup validation-report

$(VENV_PYTHON):
	$(PYTHON) -m venv venv
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -r requirements.txt

compile:
	$(PYTHON) -m compileall microservices inframirror tests

test: $(VENV_PYTHON)
	$(VENV_PYTHON) -m unittest discover -s tests

dev-setup: $(VENV_PYTHON)
	$(VENV_PYTHON) -m pip install -r requirements-dev.txt

pytest:
	$(VENV_PYTHON) -m pytest

coverage:
	$(VENV_PYTHON) -m pytest --cov=inframirror.gemini_client --cov=inframirror.evidence_grounding --cov=inframirror.incident_intelligence --cov=inframirror.policy_engine --cov=inframirror.incident_store --cov=inframirror.remediation_guard --cov=inframirror.recovery_verifier --cov-branch --cov-report=term-missing --cov-fail-under=80

lint:
	$(VENV_PYTHON) -m ruff check .

type-check:
	$(VENV_PYTHON) -m mypy inframirror/gemini_client.py inframirror/evidence_grounding.py inframirror/policy_engine.py inframirror/remediation_guard.py

validation-report:
	$(VENV_PYTHON) scripts/run_aiops_scenarios.py --generate-deterministic-report

compose-check:
	docker compose config --quiet

security-audit: $(VENV_PYTHON)
	$(VENV_PYTHON) -m pip install pip-audit
	$(VENV_PYTHON) -m pip_audit -r requirements.txt

verify: compile test compose-check
