#!/usr/bin/env python3
"""Runs BGP hijack/ROV and DNS64/NAT64 scenarios S0-S2, S3a-c, and S4a-b.
EXEC_MODE=docker (default, LAB=<lab name>) or netns (sandbox replica without Docker)."""
import json, os, subprocess, time, datetime, pathlib
MODE = os.environ.get("EXEC_MODE", "docker"); LAB = os.environ.get("LAB", "bgp-rov-lab")
RES = pathlib.Path("results"); RES.mkdir(exist_ok=True)

def run(node, *cmd, timeout=20, check=True):
    pre = ["docker","exec",f"clab-{LAB}-{node}"] if MODE=="docker" else ["ip","netns","exec",node]
    r = subprocess.run([*pre, *cmd], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"{node}: {cmd} exit={r.returncode} {r.stderr.strip()}")
    return {"returncode": r.returncode, "stdout": r.stdout.strip(), "stderr": r.stderr.strip()}

def vt(node, cmd, **kw):
    pre = ["vtysh"] + (["-N", node.removeprefix("router-")] if MODE=="netns" else [])
    return run(node, *pre, "-c", cmd, **kw)["stdout"]

def wait_until(pred, timeout=120, interval=3, what="condition"):
    end, err = time.monotonic()+timeout, None
    while time.monotonic() < end:
        try:
            if pred(): return
        except Exception as e: err = repr(e)
        time.sleep(interval)
    raise TimeoutError(f"timeout waiting {what}; last_error={err}")

def sessions_up():
    s = json.loads(vt("router-validator","show bgp summary json"))["ipv4Unicast"]["peers"]
    i = json.loads(vt("router-isp","show bgp summary json"))["ipv4Unicast"]["peers"]
    return len(s)==2 and len(i)==3 and all(p["state"]=="Established" for p in [*s.values(),*i.values()])
def rtr_up():
    c = json.loads(vt("router-validator","show rpki cache-connection json"))["connections"]
    return bool(c) and c[0]["state"]=="connected" and json.loads(vt("router-validator","show rpki prefix-table json"))["ipv4PrefixCount"]>=1

def classify(body): return "decoy" if "hijacked" in body else "origin" if "Origin Server" in body else "unknown"
def curl(node, extra=()):
    r = run(node,"curl","-4","-s","--max-time","5",*extra,"http://203.0.113.10",check=False)
    return r["returncode"], classify(r["stdout"])
def curl_v6():
    r = run("client-v6","curl","-6","-s","--max-time","5","http://web.lab.test",check=False)
    if r["returncode"] in (6, 7):
        return r["returncode"], "unresolved"
    if r["returncode"] == 28:
        return r["returncode"], "timeout"
    return r["returncode"], classify(r["stdout"])
def stop_process(node, name):
    pids = run(node, "sh", "-c",
               f"ps -eo pid=,stat=,comm= | awk '$3 == \"{name}\" && $2 !~ /^Z/ {{print $1}}'")["stdout"].split()
    for pid in pids:
        run(node, "kill", "-9", pid)
    wait_until(lambda: not run(node, "sh", "-c",
                               f"ps -eo pid=,stat=,comm= | awk '$3 == \"{name}\" && $2 !~ /^Z/ {{print $1}}'")["stdout"],
               what=f"{name} stopped")
def process_running(node, name):
    return bool(run(node, "sh", "-c",
                    f"ps -eo pid=,stat=,comm= | awk '$3 == \"{name}\" && $2 !~ /^Z/ {{print $1}}'")["stdout"])
def set_dns64(on):
    stop_process("gateway-nat64", "unbound")
    if on:
        run("gateway-nat64", "sh", "-c",
            "sed 's/module-config: \"dns64 validator iterator\"/module-config: \"dns64 validator iterator\"/' "
            "/etc/lab/unbound.conf | sed 's/control-enable: yes/control-enable: no/' "
            "> /tmp/unbound-active.conf")
    else:
        run("gateway-nat64", "sh", "-c",
            "sed 's/module-config: \"dns64 validator iterator\"/module-config: \"validator iterator\"/' "
            "/etc/lab/unbound.conf | sed 's/control-enable: yes/control-enable: no/' "
            "> /tmp/unbound-active.conf")
    run("gateway-nat64", "unbound", "-c", "/tmp/unbound-active.conf")
    wait_until(lambda: process_running("gateway-nat64", "unbound"),
               what=f"DNS64 {'enabled' if on else 'disabled'}")
def set_tayga(on):
    stop_process("gateway-nat64", "tayga")
    if on:
        run("gateway-nat64", "tayga", "-c", "/etc/lab/tayga.conf")
        run("gateway-nat64", "ip", "-6", "route", "replace", "2001:db8:6501:64::/96", "dev", "nat64")
        run("gateway-nat64", "ip", "route", "replace", "192.0.2.192/26", "dev", "nat64")
        wait_until(lambda: process_running("gateway-nat64", "tayga"),
                   what="Tayga enabled")
def set_rov(on):
    s = "ROV" if on else "NOROV"
    run("router-validator","vtysh",*(["-N","validator"] if MODE=="netns" else []),
        "-c","conf t","-c","router bgp 65001","-c","address-family ipv4 unicast",
        "-c",f"neighbor 198.51.100.9 route-map FROM-ISP-{s} in","-c",f"neighbor 198.51.100.14 route-map FROM-PEER-{s} in",
        "-c","end","-c","clear bgp ipv4 unicast * soft in")
def hijack(on):
    run("router-hijacker","vtysh",*(["-N","hijacker"] if MODE=="netns" else []),"-c","conf t","-c","router bgp 65020",
        "-c","address-family ipv4 unicast","-c",("" if on else "no ")+"network 203.0.113.0/25","-c","end",
        check=on)  # "no network" fails when the statement does not exist yet (clean deploy): that is fine
    wait_until(
        lambda: ("Network not in table" not in vt("router-hijacker", "show bgp ipv4 unicast 203.0.113.0/25"))
        == on,
        what=f"hijack {'enabled' if on else 'disabled'}",
    )
    if on:
        wait_until(
            lambda: "65020" in vt("router-validator", "show bgp ipv4 unicast 203.0.113.0/25"),
            what="hijack propagation to validator",
        )
    else:
        wait_until(
            lambda: "65020" not in vt("router-validator", "show bgp ipv4 unicast 203.0.113.0/25"),
            what="hijack withdrawal from validator",
        )

def route_next_hops():
    fib = json.loads(vt("router-validator", "show ip route 203.0.113.10 json"))
    return [n["ip"] for n in list(fib.values())[0][0]["nexthops"]]

def wait_for_forwarding_path(expected):
    if expected == "origin":
        ready = lambda: "198.51.100.14" in route_next_hops()
    else:
        ready = lambda: bool(route_next_hops()) and "198.51.100.14" not in route_next_hops()
    wait_until(ready,
               what=f"{expected} forwarding path")

def scenario(sid, name, rov, expected, after_wait):
    time.sleep(after_wait)
    wait_for_forwarding_path(expected)
    deadline = time.monotonic() + 30
    rc, got = curl("client-dualstack")
    while got != expected and time.monotonic() < deadline:
        time.sleep(1)
        rc, got = curl("client-dualstack")
    rib25 = vt("router-validator","show bgp ipv4 unicast 203.0.113.0/25")
    nh = route_next_hops()
    return {"id":sid,"name":name,"rov_enabled":rov,"expected_served_by":expected,"observed_served_by":got,
            "status":"passed" if got==expected else "failed",
            "evidence":{"curl_exit_code":rc,"validator_rib_25":rib25,"validator_fib_nexthops":nh}}
def scenario_v6(sid, name, rov, expected, after_wait):
    time.sleep(after_wait)
    if expected in ("origin", "decoy"):
        wait_for_forwarding_path(expected)
    deadline = time.monotonic() + 30
    rc, got = curl_v6()
    while got != expected and time.monotonic() < deadline:
        time.sleep(1)
        rc, got = curl_v6()
    return {"id":sid,"name":name,"rov_enabled":rov,"expected_served_by":expected,
            "observed_served_by":got,"status":"passed" if got==expected else "failed",
            "evidence":{"curl_exit_code":rc,"dns64_aaaa":run(
                "client-v6","dig","+short","AAAA","web.lab.test",check=False)["stdout"]}}

def main():
    out = {"run_id": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ"), "lab": LAB, "status":"incomplete","scenarios":[],"errors":[]}
    try:
        wait_until(sessions_up, what="BGP sessions"); wait_until(rtr_up, what="RTR + VRP")
        set_rov(False); hijack(False); time.sleep(5)
        out["scenarios"].append(scenario("S0","baseline",False,"origin",3))
        set_rov(False); hijack(True)
        out["scenarios"].append(scenario("S1","hijack_rov_off",False,"decoy",5))
        set_rov(True)
        out["scenarios"].append(scenario("S2","hijack_rov_on",True,"origin",5))
        set_dns64(False); set_tayga(False)
        out["scenarios"].append(scenario_v6("S3a","dns64_nat64_off",True,"unresolved",2))
        set_dns64(True)
        out["scenarios"].append(scenario_v6("S3b","dns64_only",True,"timeout",2))
        set_tayga(True)
        out["scenarios"].append(scenario_v6("S3c","dns64_nat64_on",True,"origin",2))
        set_rov(False); hijack(True)
        out["scenarios"].append(scenario_v6("S4a","integrated_rov_off",False,"decoy",5))
        set_rov(True)
        out["scenarios"].append(scenario_v6("S4b","integrated_rov_on",True,"origin",5))
        hijack(False)
        out["status"] = "passed" if all(s["status"]=="passed" for s in out["scenarios"]) else "failed"
    except Exception as e:
        out["errors"].append(repr(e))
    (RES/"history").mkdir(exist_ok=True)
    for p in (RES/"latest.json", RES/"history"/f"{out['run_id']}.json"): p.write_text(json.dumps(out, indent=2))
    print(json.dumps({k:out[k] for k in ("run_id","status","errors")}), [ (s["id"],s["observed_served_by"],s["status"]) for s in out["scenarios"]])
if __name__ == "__main__": main()
