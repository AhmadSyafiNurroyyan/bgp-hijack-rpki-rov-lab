# Experiments: Hijack, ROV, and NAT64/DNS64 Step by Step

This document is the lab's experiment playbook: **what to run, what you should see, and when an experiment counts as passed.**

- Want to know *why* things are configured the way they are, or stuck on an error? See [implementation-notes.md](implementation-notes.md).
- Want the diagram, addressing plan, and routing policy? See [topology-documentation.md](topology-documentation.md).
- Want to know how the pipeline collects results automatically? See [automation-design.md](automation-design.md).

---

## The story in short

The lab demonstrates two problems in a single topology:

1. **BGP hijack.** An AS announces a prefix it has no right to. Because its upstream does not filter, the bogus announcement spreads. The mitigation is **RPKI Route Origin Validation (ROV)** at the Validator AS.
2. **IPv6-only vs. IPv4-only services.** A client with only IPv6 can still reach an IPv4-only server through **NAT64/DNS64**.

At the end, the two stories are combined: an IPv6-only client going through NAT64 is **hit by the hijack when ROV is off**, and **protected when ROV is on**.

## Who plays what

| AS | Role | Notes |
| :-- | :-- | :-- |
| AS65000 | ISP-Transit | Upstream for all other ASes, **no filtering** |
| AS65010 | Origin AS | Legitimate owner of `203.0.113.0/24`; runs `target-v4` |
| AS65020 | Hijacker AS | Announces `203.0.113.0/25` without authorization; runs `decoy-target` |
| AS65001 | Validator AS | Runs ROV, NAT64/DNS64, and the clients |

One thing people often get wrong: **ROV is performed by `router-validator`, not by Routinator.** Routinator only validates ROAs and sends the resulting VRPs to the router over RTR; it is the router that decides whether to accept or reject a route.

Two web pages let us see the outcome with our own eyes: `target-v4` shows **"Welcome to Origin Server"**, and `decoy-target` shows **"You've been hijacked"**.

---

## Experiment map

| ID | Experiment | Question it answers |
| :-- | :-- | :-- |
| [E0](#e0-setup-and-deploy) | Setup and deploy | Can the lab be rebuilt from scratch with one command? |
| [E1](#e1-bgp-baseline) | BGP baseline | Are all sessions up and routes normal before the attack? |
| [E2](#e2-hijack-without-rov) | Hijack without ROV | Does the bogus `/25` actually divert traffic? |
| [E3](#e3-hijack-with-rov) | Hijack with ROV | Does ROV stop it? |
| [E4](#e4-nat64dns64-three-conditions) | NAT64/DNS64, three conditions | What fails when one component is down? |
| [E5](#e5-control-client-dualstack) | Dual-stack control | Is the E4 result really caused by NAT64 and nothing else? |
| [E6](#e6-integration) | Integration | Does the IPv6-only client follow the ROV state? |
| [E7](#e7-automation-and-evidence) | Automation and evidence | Can all of the above be repeated and proven by CI? |

---

## Setup before you start

All commands below use two small helper functions so we don't repeat `docker exec ...` everywhere. Replace `LAB` with the `name:` value from `topology.clab.yml`.

```bash
LAB=<lab-name>

# Run a vtysh command on a router:   vt router-validator "show bgp summary"
vt()  { docker exec "clab-$LAB-$1" vtysh -c "$2"; }

# Run a shell command on a node:      run client-v6 "curl -s http://web.lab.test"
run() { docker exec "clab-$LAB-$1" sh -c "$2"; }
```

Neighbor addresses (`<ISP_IP>`, `<ORIGIN_IP>`, etc.) are in the addressing table in [topology-documentation.md](topology-documentation.md).

Before E1, make sure every node uses the lab links, not the Containerlab management network, for its default route (see [pitfall 1](implementation-notes.md#1-the-management-interface-brings-its-own-ipv4-default-route)). Otherwise the clients may reach `203.0.113.10` by a path that has nothing to do with the topology, and `client-v6` is not really IPv6-only.

---

## E0. Setup and deploy

**Goal:** confirm the lab can be rebuilt from scratch.

```bash
containerlab deploy -t topology.clab.yml
```

Tear it down and redeploy to prove reproducibility:

```bash
containerlab destroy -t topology.clab.yml --cleanup
containerlab deploy  -t topology.clab.yml
```

**Pass if**

- [ ] The lab can be redeployed from scratch with a single command.

---

## E1. BGP baseline

**Goal:** establish the normal state before any attack. Without a baseline, the results of E2 and E3 have nothing to be compared against.

**1. All four eBGP sessions must be `Established`**

```bash
vt router-isp       "show bgp summary"
vt router-validator "show bgp summary"
```

| Session | Type |
| :-- | :-- |
| ISP ↔ Origin | customer |
| ISP ↔ Hijacker | customer |
| ISP ↔ Validator | customer |
| Validator ↔ Origin | direct peering |

**2. The Validator receives `203.0.113.0/24` over two paths (ISP and Origin), and there is no `/25` yet**

```bash
vt router-validator "show bgp ipv4 unicast 203.0.113.0/24"   # two paths
vt router-validator "show bgp ipv4 unicast 203.0.113.0/25"   # not present
```

**3. The return path exists.** The Validator's own block (`192.0.2.128/25`) must be visible at the ISP and at Origin, so replies can find their way home.

```bash
vt router-isp    "show bgp ipv4 unicast 192.0.2.128/25"
vt router-origin "show bgp ipv4 unicast 192.0.2.128/25"
```

**4. Before the attack, traffic reaches the real server.**

```bash
run client-dualstack "curl -4 -s --max-time 5 http://203.0.113.10"   # "Welcome to Origin Server"
```

**Pass if**

- [ ] 4 eBGP sessions are `Established` and stable.
- [ ] The Validator learns the `/24` over two paths, there is no `/25` yet, and the return path (`192.0.2.128/25`) is visible at the ISP and at Origin.
- [ ] The baseline `curl -4` lands on `target-v4`.

---

## E2. Hijack without ROV

**Goal:** prove that the attack succeeds when nothing validates routes. This is the "before" state.

**1. Make sure RPKI is ready.** Routinator is running with the local ROA (via SLURM), the RTR session is up, and the router has loaded the VRPs.

```bash
vt router-validator "show rpki cache-connection"   # RTR session to rpki-server:3323
vt router-validator "show rpki prefix-table"       # must contain 203.0.113.0/24, AS65010, max-length 24
```

The local ROA: AS65010 is authorized for `203.0.113.0/24`, max-length 24. So AS65020's `/25` is **doubly invalid** (wrong origin *and* exceeds max-length).

**2. Make sure the ISP really does not filter.**

```bash
vt router-isp "show running-config"   # there must be no route-map/prefix-list on customer neighbors
```

**3. Make sure ROV is off**, i.e. the `*-NOROV` route-maps are attached (how to switch is described in [implementation-notes.md](implementation-notes.md#7-toggling-rov-two-pairs-of-route-maps)).

**4. The Hijacker starts announcing `203.0.113.0/25`** (the exact command is in the `router-hijacker` configuration in the repo).

**5. Observe from client-dualstack and router-validator**

```bash
run client-dualstack "curl -4 -s --max-time 5 http://203.0.113.10"

vt router-validator "show bgp ipv4 unicast 203.0.113.0/25"   # RIB
vt router-validator "show ip route 203.0.113.10"             # FIB
```

**Expected results**

| Observation | Result |
| :-- | :-- |
| HTTP response | **"You've been hijacked"** (lands on `decoy-target`) |
| Validator RIB | `/25` accepted, originated by AS65020, via the ISP |
| Validator FIB | `203.0.113.10` points to the ISP, because *longest prefix match* picks the `/25` |

**A note on realism.** Hijacking via a `/25` is a quirk of this lab. On the real Internet, most networks drop IPv4 prefixes longer than `/24`; a real sub-prefix attack is typically a `/24` against a `/23`. Here the `/25` propagates because ISP-Transit deliberately does no filtering at all, and the documentation range `203.0.113.0/24` is only a single `/24`. Details are in the Limitations section of the repository [README](../README.md#limitations).

**Pass if**

- [ ] The RTR session between `router-validator` and `rpki-server` is up.
- [ ] ROV off: the `/25` is accepted and `curl -4` from client-dualstack lands on the decoy.

---

## E3. Hijack with ROV

**Goal:** prove that ROV rejects the invalid route and traffic returns to the legitimate owner. This is the "after" state.

**1. Turn ROV on**: attach the `*-ROV` route-maps on both neighbors, then force a re-evaluation.

```bash
vt router-validator "clear bgp ipv4 unicast * soft in"
```

**2. Observe**

```bash
# the /25 must disappear from the BGP table
vt router-validator "show bgp ipv4 unicast 203.0.113.0/25"

# ...but remain visible as evidence of the rejection (requires soft-reconfiguration inbound)
vt router-validator "show bgp ipv4 unicast neighbors <ISP_IP> received-routes"

# the path now goes through the peering link, not the ISP
vt router-validator "show ip route 203.0.113.10"

run client-dualstack "curl -4 -s --max-time 5 http://203.0.113.10"
```

**3. Extra check on `router-origin`**: its route to `203.0.113.10` must not point to the ISP.

```bash
vt router-origin "show ip route 203.0.113.10"
```

**Expected results**

| Observation | Result |
| :-- | :-- |
| HTTP response | **"Welcome to Origin Server"** (lands on `target-v4`) |
| Validator BGP table | `/25` absent, but still visible in `received-routes` |
| Validator next hop | towards Origin over the peering link `198.51.100.12/30` |
| `router-origin` | does not route `203.0.113.10` via the ISP |

**Pass if**

- [ ] ROV on: the `/25` is rejected and `curl -4` lands on target-v4.
- [ ] `router-origin` does not route `203.0.113.10` via the ISP.

---

## E4. NAT64/DNS64, three conditions

**Goal:** show that NAT64/DNS64 is **two components that depend on each other**. DNS64 fabricates the address, Tayga translates it. If either one is down, the failure looks different.

Everything is run from `client-v6` with **ROV on**. `target-v4` has only IPv4, so `web.lab.test` has only an A record (`203.0.113.10`).

```bash
run client-v6 "dig AAAA web.lab.test"
run client-v6 "curl -s --max-time 5 http://web.lab.test; echo \"exit=\$?\""
```

| Condition | DNS64 | Tayga | `dig AAAA` | `curl` |
| :-- | :-: | :-: | :-- | :-- |
| **4a** | off | off | no AAAA record | fails (no usable address) |
| **4b** | on | off | synthetic AAAA `2001:db8:6501:64::cb00:710a` | times out |
| **4c** | on | on | synthetic AAAA | **success**, shows "Welcome to Origin Server" |

⚠️ **Exact `curl` exit codes are not verified.** In 4a, `client-v6` still gets the A record but has no IPv4 route, so depending on the resolver library and the `curl` build the failure can appear as `exit=6` (cannot resolve) or `exit=7` (cannot connect). In 4b the timeout (`exit=28`) only happens while the TUN interface stays up, so packets are queued silently; if the interface is removed, the gateway answers "unreachable" and the failure is immediate. Treat *fails / times out / succeeds* as the contract, and record the real codes once you have run it.

Why `cb00:710a`? Because `203.0.113.10` written in hexadecimal is `cb`=203, `00`=0, `71`=113, `0a`=10 (the RFC 6052 rule for the `2001:db8:6501:64::/96` prefix).

One way to turn a component off, as an example: stop the `tayga` process on `gateway-nat64`; for DNS64, remove `dns64` from Unbound's `module-config` and run `unbound-control reload`.

**Repeat several times to check consistency** (the 10 is just an example):

```bash
for i in $(seq 1 10); do
  run client-v6 "curl -s -o /dev/null -w '%{http_code}\n' --max-time 5 http://web.lab.test"
done
```

**Pass if**

- [ ] NAT64/DNS64: the three conditions produce, in order, a resolution failure, a timeout, and a success.

---

## E5. Control: client-dualstack

**Goal:** make sure the E4 result really is due to NAT64, and not to a routing or server problem.

```bash
run client-dualstack "curl -4 -s --max-time 5 http://203.0.113.10"
```

The **`-4`** flag is mandatory. Without it, the dual-stack client tends to pick the synthetic IPv6 address and goes through NAT64 as well, so it stops being a control.

**Pass if**

- [ ] client-dualstack reaches the server directly without NAT64 (control).

---

## E6. Integration

**Goal:** prove that the two halves of the project are connected. The IPv6-only client reaches `web.lab.test` through NAT64, and its fate is decided by the ROV state.

```bash
run client-v6 "curl -s --max-time 5 http://web.lab.test"
```

| ROV state | Result at client-v6 |
| :-- | :-- |
| off | "You've been hijacked" (lands on `decoy-target`) |
| on | "Welcome to Origin Server" (lands on `target-v4`) |

Why does this work in both directions? The Tayga pool (`192.0.2.192/26`) sits inside the `192.0.2.128/25` block that is advertised to the ISP and Origin, so replies from either server, including the decoy, can still find their way back to the gateway.

**Pass if**

- [ ] Integration scenario: client-v6 follows the ROV state (decoy when off, origin when on).

---

## E7. Automation and evidence

All of the phases above are run by scripts and CI (GitHub Actions), so the results are not compiled by hand.

- **Data sources:** BGP state from native `vtysh` JSON output (e.g. `show bgp summary json`), and application-level evidence from `curl` and `dig`. No SNMP.
- **Output:** a Job Summary in GitHub Actions and `results/latest.json`.
- **Scenario IDs.** The pipeline numbers its scenarios `S0` to `S4`: S0 = E1, S1 = E2, S2 = E3, S3a-c = E4, S4a-b = E6. E5 has no scenario of its own: `client-dualstack` with `curl -4` is the observer in S1 and S2.
- **Full design** (script layout, workflow, JSON schema): [automation-design.md](automation-design.md).

**Pass if**

- [ ] CI runs automatically and produces `results/latest.json` and a Job Summary.

---

## Success criteria at a glance

| # | Criterion | Experiment |
| :-: | :-- | :-: |
| 1 | Clean baseline: 4 eBGP sessions `Established`, no `/25`, traffic reaches target-v4 | E1 |
| 2 | RTR session router-validator ↔ rpki-server is up | E2 |
| 3 | ROV off: `/25` accepted, `curl -4` lands on the decoy | E2 |
| 4 | ROV on: `/25` rejected, `curl -4` lands on target-v4 | E3 |
| 5 | router-origin does not route `203.0.113.10` via the ISP | E3 |
| 6 | NAT64/DNS64: resolution failure → timeout → success | E4 |
| 7 | client-dualstack reaches the server directly without NAT64 (control) | E5 |
| 8 | client-v6 follows the ROV state | E6 |
| 9 | Lab redeployable from scratch with one command | E0 |
| 10 | CI produces `results/latest.json` and a Job Summary | E7 |

## Notes on reading the results

- **Timing** results depend on the host (laptop vs. CI runner). Only compare numbers within the same environment.
- ROV is validated against a **self-made ROA** (local, via SLURM), not the global RPKI repositories. The ASNs used are private ASNs, which by definition cannot receive ROAs in the global RPKI.
- All prefixes and ASNs come from documentation/private ranges (RFC 5737, 3849, 6996, 6052). The lab is not connected to the real Internet.
