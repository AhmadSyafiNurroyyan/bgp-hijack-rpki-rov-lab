import json, pathlib, pytest
P = pathlib.Path("results/latest.json")
@pytest.fixture(scope="session")
def results():
    assert P.exists(), "results/latest.json is missing: run the experiments first"
    return json.loads(P.read_text())
def sc(r, i):
    m = [s for s in r["scenarios"] if s["id"] == i]; assert len(m) == 1, f"Expected exactly one result for {i}"; return m[0]
def test_run_completed(results): assert results["status"] == "passed", results["errors"]
def test_baseline_origin(results): assert sc(results,"S0")["observed_served_by"] == "origin"
def test_rov_off_decoy(results): assert sc(results,"S1")["observed_served_by"] == "decoy"
def test_rov_on_origin(results):
    s = sc(results,"S2"); assert s["observed_served_by"] == "origin"; assert "198.51.100.14" in s["evidence"]["validator_fib_nexthops"]
def test_nat64_states(results):
    assert sc(results,"S3a")["observed_served_by"] == "unresolved"
    assert sc(results,"S3b")["observed_served_by"] == "timeout"
    assert sc(results,"S3c")["observed_served_by"] == "origin"
def test_integrated_nat64_follows_rov(results):
    assert sc(results,"S4a")["observed_served_by"] == "decoy"
    assert sc(results,"S4b")["observed_served_by"] == "origin"
