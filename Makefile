# BGP Hijack, RPKI ROV & NAT64/DNS64 lab.   Run `make help` for the targets.
#
# Run from the repository root. Docker must work without sudo (add your user to the
# docker group); only containerlab is run through $(SUDO). As root, use: make up SUDO=

# LAB must match "name:" in topology.clab.yml (containers are called clab-<LAB>-<node>).
LAB    ?= bgp-rov-lab
TOPO   ?= topology.clab.yml
IMAGE  ?= lab-tools:latest
SUDO   ?= sudo
PYTHON ?= python3

# scripts/run_experiments.py and scripts/helpers.sh read LAB from the environment.
export LAB

.DEFAULT_GOAL := help
.NOTPARALLEL:
.PHONY: help build up experiments check test down clean

help: ## Show this help
	@awk 'BEGIN { FS = ":.*## " } /^[a-z-]+:.*## / { printf "  make %-12s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

build: ## Build the lab-tools image (targets, clients, NAT64/DNS64 gateway)
	docker build -t $(IMAGE) docker/lab-tools

up: build ## Build the image and deploy the topology
	$(SUDO) containerlab deploy -t $(TOPO)

experiments: ## Run scenarios S0-S4 against the running lab, write results/latest.json
	$(PYTHON) scripts/run_experiments.py

check: ## Assert the expected results in results/latest.json with pytest
	$(PYTHON) -m pytest -q tests/

test: experiments check ## Run every scenario, then assert the expected results

down: ## Destroy the lab and remove what containerlab created
	$(SUDO) containerlab destroy -t $(TOPO) --cleanup

clean: ## Remove local run output (results/history, caches); keeps results/latest.json
	rm -rf results/history .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
