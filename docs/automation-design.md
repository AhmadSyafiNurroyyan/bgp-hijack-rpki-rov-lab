# Analysis & Automation Design

## 1. Objective

This document describes the automation design for experiments involving BGP hijacking/RPKI Route Origin Validation (ROV) and access from IPv6-only networks to IPv4-only services using DNS64/NAT64 in a Containerlab environment.

The pipeline is designed to:

- collect evidence from the control plane, forwarding plane, and application layer;
- execute scenarios sequentially and repeatedly;
- compare actual results against expected outcomes using `pytest`;
- store results in JSON, display a CI summary, and clean up the lab even if the experiment fails.

## 2. Data Sources and Evidence

| Required Data | Source/Command | Node |
|---|---|---|
| BGP session status | `vtysh -c "show bgp summary json"` | All router |
| `/24` and `/25` routes in the BGP RIB | `show bgp ipv4 unicast <prefix>` dan varian JSON bila didukung | `router-validator` |
| Route used for forwarding | `show ip route 203.0.113.10 json` | `router-validator`, `router-origin` |
| Server handling the request | `curl` ke `203.0.113.10`; klasifikasi body | `client-dualstack` |
| DNS64 resolution | `dig AAAA web.lab.test` ke resolver yang ditentukan | `client-v6` |
| End-to-end NAT64 connectivity | `curl` IPv6 ke `web.lab.test` | `client-v6` | 
| RPKI cache status | `show rpki cache-connection` | `router-validator` |
| Packet-level evidence | `tcpdump` pada interface menuju ISP dan Origin | `router-validator`

## 3. Pipeline Workflow

```text
Validate configuration and dependencies
              |
              v
Deploy Containerlab
              |
              v
Poll until BGP sessions + RTR are ready (or timeout)
              |
              v
S0: baseline with hijacker announcement disabled
              |
              v
S1 -> S2: announce /25; test ROV off/on
              |
              v
S3a -> S3b -> S3c: test DNS64/NAT64 states with ROV on
              |
              v
S4a -> S4b: integrated test; finish with ROV on
              |
              v
Write results/latest.json and results/history/<run_id>.json
              |
              v
Run pytest against observed results
              |
              v
Write GitHub Actions Job Summary and upload artifacts
              |
              v
Always destroy Containerlab topology
```

## 4. Script Implementation Details

### 4.1 Command Execution and Error Handling

```python
import subprocess

def run(node, *cmd, timeout=20, check=True):
    result = subprocess.run(
        ["docker", "exec", f"clab-{LAB}-{node}", *cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed on {node}: {cmd}\n"
            f"exit={result.returncode}\n"
            f"stderr={result.stderr.strip()}"
        )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }
```

### 4.2 JSON Parsing

```python
import json

def parse_json_output(output, command):
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON from {command}: {exc}; output={output!r}"
        ) from exc
```

### 4.3 Convergence Polling

```python
import time

def wait_until(predicate, timeout=120, interval=3, description="condition"):
    deadline = time.monotonic() + timeout
    last_error = None

    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception as exc:
            last_error = repr(exc)
        time.sleep(interval)

    raise TimeoutError(
        f"Timed out waiting for {description}; last_error={last_error}"
    )
```

## 5. Results Data Contract

Example structure:

```json
{
  "run_id": "2026-10-10T10-00-00Z",
  "status": "passed",
  "lab": "bgp-rov-lab",
  "environment": {
    "containerlab_version": "record-at-runtime",
    "frr_version": "record-at-runtime"
  },
  "scenarios": [
    {
      "id": "S1",
      "name": "hijack_rov_off",
      "rov_enabled": false,
      "expected_served_by": "decoy",
      "observed_served_by": "decoy",
      "status": "passed",
      "evidence": {
        "validator_rib_25": "captured output or parsed object",
        "validator_fib": "captured output or parsed object",
        "curl_exit_code": 0
      }
    }
  ],
  "errors": []
}
```

The recommended data contract is as follows:

- `run_id`: A unique identifier that is safe to use in a filename.
- `status`: `passed`, `failed`, or `incomplete`.
- `expected_*`: Values defined by the test design.
- `observed_*`: Values actually observed during execution.
- `evidence`: Structured output and/or artifact references.
- `errors`: Command failures, timeouts, or conditions that could not be verified.
- `environment`: Tool-version information that supports result reproduction.

The latest run is stored in `results/latest.json`, while historical runs are archived in `results/history/<run_id>.json`.

## 6. Assertions with pytest

Example test pattern for the data contract above:

```python
import json
from pathlib import Path

import pytest

RESULTS_PATH = Path("results/latest.json")

@pytest.fixture(scope="session")
def results():
    assert RESULTS_PATH.exists(), "Experiment results file is missing"
    return json.loads(RESULTS_PATH.read_text(encoding="utf-8"))

def scenario(results, scenario_id):
    matches = [
        item for item in results["scenarios"]
        if item["id"] == scenario_id
    ]
    assert len(matches) == 1, f"Expected exactly one result for {scenario_id}"
    return matches[0]

def test_run_completed(results):
    assert results["status"] == "passed"

def test_rov_off_reaches_decoy(results):
    item = scenario(results, "S1")
    assert item["expected_served_by"] == "decoy"
    assert item["observed_served_by"] == "decoy"
    assert item["status"] == "passed"

def test_rov_on_reaches_origin(results):
    item = scenario(results, "S2")
    assert item["expected_served_by"] == "origin"
    assert item["observed_served_by"] == "origin"
    assert item["status"] == "passed"

def test_integrated_nat64_follows_rov(results):
    assert scenario(results, "S4a")["observed_served_by"] == "decoy"
    assert scenario(results, "S4b")["observed_served_by"] == "origin"
```

## 7. GitHub Actions Workflow

The workflow job sequence is designed as follows:

1. Check out the repository.
2. Install the pinned Containerlab version and dependencies.
3. Build or pull container images with pinned versions.
4. Deploy the topology.
5. Run the experiment and save results and evidence.
6. Run `pytest`.
7. Write the Job Summary and upload artifacts using the `always()` condition.
8. Destroy the topology using the `always()` condition.

Example workflow fragment for result handling and cleanup:

```yaml
- name: Run experiments
  id: experiments
  run: python3 scripts/run_experiments.py

- name: Check expectations
  if: success()
  run: pytest -q tests/

- name: Write Job Summary
  if: always()
  run: python3 scripts/write_summary.py

- name: Upload experiment artifacts
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: experiment-results
    path: |
      results/
      logs/
    if-no-files-found: warn

- name: Destroy topology
  if: always()
  run: sudo containerlab destroy -t topology.clab.yml --cleanup
```