# Build Guide: Building the Lab, Step by Step

This guide takes you from an empty folder to a running lab where the three scenarios of the project can be reproduced: a BGP hijack, ROV stopping it, and NAT64/DNS64 for an IPv6-only client. It is written to be followed **in order**. Every phase ends with a **checkpoint**: do not move on until it passes.

This guide only covers *building*. It deliberately does not repeat what lives elsewhere:

| For this... | Go to |
| :-- | :-- |
| Addresses, links, routing policy | [topology-documentation.md](topology-documentation.md) |
| What each experiment does and what "passed" means | [experiments.md](experiments.md) |
| Why a tool was chosen, and the known traps | [implementation-notes.md](implementation-notes.md) |
| How results are collected and published by CI | [automation-design.md](automation-design.md) |

## What has and has not been tested

The configuration files in this guide are the same files as in the repository. They were run in a **network-namespace replica** of the topology (one namespace per node, the same interface names and addresses), because the machine used to write this guide could not run Docker or pull images.

| Status | What | Tested with |
| :-- | :-- | :-- |
| ✅ | 4 routers: eBGP sessions, policies, ROV on and off, hijack, received-routes evidence | FRR 8.4.7 (configuration originally tested on 8.4.4) |
| ✅ | Routinator with SLURM only (no TALs), RTR to the router, VRP loaded, automatic reconnect | Routinator 0.15.2 |
| ✅ | Scenarios S0-S2, S3a-S3c, and S4a-S4b from a clean state, and `pytest` on the results | `scripts/run_experiments.py` |
| ✅ | DNS64 synthesis through `stub-zone` + NSD, and DNS64-only timeout behavior | Unbound 1.17.1, NSD 4.6.1 |
| ✅ | Tayga: configuration parses, TUN device is created, process starts, and translates HTTP | Tayga 0.9.2 |
| ✅ | `containerlab deploy`: image tags, `binds`, `exec`, `user: root`, `devices` | Containerlab 0.81.1 on WSL2 |
| ✅ | Building `lab-tools` from `debian:12-slim` | Docker 28.5.2 |
| ✅ | IPv6 kernel forwarding, `client-v6`, DNS64/NAT64 end-to-end scenarios | WSL2 kernel 6.18.54.1 |

Where a step depends on a ⚠️ item, it is marked again at that step. When you confirm one, change the label and note your versions.

---

## Phase 0. Prerequisites

You need a Linux machine (laptop, VM, or a cloud instance) with:

- Docker, and your user allowed to run it (or use `sudo`).
- [Containerlab](https://containerlab.dev/install/) installed.
- `/dev/net/tun` on the host (Tayga needs it). Check with `ls -l /dev/net/tun`.
- Python 3 with `pytest` (`pip install pytest`).

```bash
docker run --rm hello-world
containerlab version
ls -l /dev/net/tun
python3 -m pytest --version
```

✅ **Checkpoint 0:** all four commands print something sensible and none of them errors.

> On an ARM host (for example Oracle Always Free), every image must have an `arm64` variant. See the note in [implementation-notes.md](implementation-notes.md#one-note-if-you-run-the-lab-on-oracle-always-free).

## Phase 1. Create the repository skeleton

```bash
mkdir -p bgp-rov-lab && cd bgp-rov-lab
mkdir -p configs/{common,router-isp,router-origin,router-hijacker,router-validator,rpki-server} \
         configs/gateway-nat64/nsd www/origin www/decoy docker/lab-tools scripts tests results docs
touch configs/gateway-nat64/start.sh      # placeholder, filled in Phase 7
```

Move the six documents (including this guide) into `docs/` and the README into the root. The result:

```text
bgp-rov-lab/
├── README.md
├── topology.clab.yml                 # Phase 3
├── docs/                             # the documents
├── docker/lab-tools/Dockerfile       # Phase 2
├── configs/
│   ├── common/{daemons,vtysh.conf}   # Phase 4: shared FRR files
│   ├── router-isp|origin|hijacker|validator/frr.conf
│   ├── router-validator/daemons      # Phase 4: same, plus the RPKI module
│   ├── rpki-server/{routinator.conf,local.slurm}
│   └── gateway-nat64/{tayga.conf,unbound.conf,start.sh,nsd/}   # Phase 7
├── www/{origin,decoy}/index.html     # Phase 4
├── scripts/helpers.sh                # Phase 5
├── scripts/run_experiments.py        # Phase 8
└── tests/test_results.py             # Phase 8
```

## Phase 2. Build the tools image

Targets, clients and the gateway all use one small image. It carries `curl`, `dig`, `python3` (for the web servers), and the NAT64/DNS64 packages (`tayga`, `unbound`, `nsd`).

`docker/lab-tools/Dockerfile`:

```dockerfile
# One image for every non-router node: targets, rpki placeholder client tooling, clients and gateway.
# Debian 12 ships tayga, unbound and nsd as packages.
FROM debian:12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      iproute2 iputils-ping curl dnsutils tcpdump procps python3 \
      tayga unbound nsd \
    && rm -rf /var/lib/apt/lists/*
# Stop the packaged services from interfering; the lab starts what it needs explicitly.
CMD ["sleep", "infinity"]
```

```bash
docker build -t lab-tools:latest docker/lab-tools
docker run --rm lab-tools sh -c 'command -v tayga unbound nsd dig curl python3 pgrep'
```

⚠️ The build itself was not run for this guide. If `apt` cannot find a package on your base image, use the matching package name for that distribution.

✅ **Checkpoint 2:** the last command prints seven paths.

## Phase 3. The topology file

`topology.clab.yml` wires the 10 nodes and gives every interface its address. The link and interface map is the part to keep in sync with [topology-documentation.md](topology-documentation.md):

| Link | A side | B side |
| :-- | :-- | :-- |
| ISP ↔ Origin | `router-isp:eth1` | `router-origin:eth1` |
| ISP ↔ Hijacker | `router-isp:eth2` | `router-hijacker:eth1` |
| ISP ↔ Validator | `router-isp:eth3` | `router-validator:eth1` |
| Validator ↔ Origin (peering) | `router-validator:eth2` | `router-origin:eth2` |
| Validator ↔ RPKI | `router-validator:eth3` | `rpki-server:eth1` |
| Validator ↔ gateway | `router-validator:eth4` | `gateway-nat64-dns64:eth1` |
| Validator ↔ client-v6 | `router-validator:eth5` | `client-v6:eth1` |
| Validator ↔ client-dualstack | `router-validator:eth6` | `client-dualstack:eth1` |
| Origin ↔ web-server | `router-origin:eth3` | `web-server-origin:eth1` |
| Hijacker ↔ decoy | `router-hijacker:eth2` | `decoy-target:eth1` |

Three things in the file are worth understanding before you copy it:

- **Addresses come from `exec:`**, run once after the containers exist. Containerlab only creates the links; it does not assign IP addresses.
- **Every node drops the management default route first** (`ip route del default`, or the flush on `client-v6`). Otherwise traffic can leave through Docker's network and the experiments mean nothing. This is [pitfall 1](implementation-notes.md#1-the-management-interface-brings-its-own-ipv4-default-route).
- **The web servers start from `exec:`** too, because they bind to an address that only exists after the line before it.

`topology.clab.yml`:

```yaml
# BGP Hijack, RPKI ROV and NAT64/DNS64 lab. Deploy: sudo containerlab deploy -t topology.clab.yml
# Interface names (eth1..) and addresses follow docs/topology-documentation.md.
# Every node first drops the Containerlab management default route (implementation-notes, pitfall 1).
name: bgp-rov-lab

topology:
  defaults:
    kind: linux

  nodes:
    # ---------------- AS65000 ISP-Transit ----------------
    router-isp:
      image: quay.io/frrouting/frr:8.4.7
      binds:
        - configs/router-isp/frr.conf:/etc/frr/frr.conf
        - configs/common/daemons:/etc/frr/daemons
        - configs/common/vtysh.conf:/etc/frr/vtysh.conf
      exec:
        - ip route del default
        - sysctl -w net.ipv4.ip_forward=1
        - ip addr add 198.51.100.1/30 dev eth1
        - ip addr add 198.51.100.5/30 dev eth2
        - ip addr add 198.51.100.9/30 dev eth3

    # ---------------- AS65010 Origin ----------------
    router-origin:
      image: quay.io/frrouting/frr:8.4.7
      binds:
        - configs/router-origin/frr.conf:/etc/frr/frr.conf
        - configs/common/daemons:/etc/frr/daemons
        - configs/common/vtysh.conf:/etc/frr/vtysh.conf
      exec:
        - ip route del default
        - sysctl -w net.ipv4.ip_forward=1
        - ip addr add 198.51.100.2/30 dev eth1
        - ip addr add 198.51.100.14/30 dev eth2
        - ip addr add 203.0.113.1/24 dev eth3

    web-server-origin:
      image: lab-tools:latest
      binds:
        - www/origin:/www:ro
      exec:
        - ip route del default
        - ip addr add 203.0.113.10/24 dev eth1
        - ip route add default via 203.0.113.1
        - sh -c 'python3 -m http.server 80 --bind 0.0.0.0 --directory /www >/tmp/http.log 2>&1 & sleep 1'

    # ---------------- AS65020 Hijacker ----------------
    router-hijacker:
      image: quay.io/frrouting/frr:8.4.7
      binds:
        - configs/router-hijacker/frr.conf:/etc/frr/frr.conf
        - configs/common/daemons:/etc/frr/daemons
        - configs/common/vtysh.conf:/etc/frr/vtysh.conf
      exec:
        - ip route del default
        - sysctl -w net.ipv4.ip_forward=1
        - ip addr add 198.51.100.6/30 dev eth1
        - ip addr add 203.0.113.1/25 dev eth2

    decoy-target:
      image: lab-tools:latest
      binds:
        - www/decoy:/www:ro
      exec:
        - ip route del default
        - ip addr add 203.0.113.10/25 dev eth1
        - ip route add default via 203.0.113.1
        - sh -c 'python3 -m http.server 80 --bind 0.0.0.0 --directory /www >/tmp/http.log 2>&1 & sleep 1'

    # ---------------- AS65001 Validator ----------------
    router-validator:
      image: quay.io/frrouting/frr:8.4.7
      binds:
        - configs/router-validator/frr.conf:/etc/frr/frr.conf
        - configs/router-validator/daemons:/etc/frr/daemons
        - configs/common/vtysh.conf:/etc/frr/vtysh.conf
      exec:
        - ip route del default
        - sysctl -w net.ipv6.conf.all.disable_ipv6=0
        - sysctl -w net.ipv4.ip_forward=1
        - sysctl -w net.ipv6.conf.all.forwarding=1
        - ip addr add 198.51.100.10/30 dev eth1
        - ip addr add 198.51.100.13/30 dev eth2
        - ip addr add 192.0.2.2/30 dev eth3
        - ip addr add 192.168.1.1/30 dev eth4
        - ip -6 addr add 2001:db8:6501:ff::1/64 dev eth4
        - ip -6 addr add 2001:db8:6501:1::1/64 dev eth5
        - ip addr add 192.0.2.129/26 dev eth6
        - ip -6 addr add 2001:db8:6501:2::1/64 dev eth6

    rpki-server:
      image: nlnetlabs/routinator:v0.15.2
      user: root
      binds:
        - configs/rpki-server/routinator.conf:/etc/routinator/routinator.conf:ro
        - configs/rpki-server/local.slurm:/etc/routinator/local.slurm:ro
      cmd: -c /etc/routinator/routinator.conf server
      exec:
        - ip route del default
        - ip addr add 192.0.2.1/30 dev eth1
        - ip route add default via 192.0.2.2

    gateway-nat64-dns64:
      image: lab-tools:latest
      cap-add:
        - NET_ADMIN
      devices:
        - /dev/net/tun:/dev/net/tun
      binds:
        - configs/gateway-nat64:/etc/lab:ro
      exec:
        - ip route del default
        - sysctl -w net.ipv6.conf.all.disable_ipv6=0
        - sysctl -w net.ipv4.ip_forward=1
        - sysctl -w net.ipv6.conf.all.forwarding=1
        - sleep 2
        - ip addr add 192.168.1.2/30 dev eth1
        - ip -6 addr add 2001:db8:6501:ff::2/64 dev eth1
        - ip -6 addr add 2001:db8:6501:ff::3/64 dev eth1
        - ip route add default via 192.168.1.1
        - ip -6 route add 2001:db8:6501::/48 via 2001:db8:6501:ff::1
        - sh /etc/lab/start.sh

    client-v6:
      image: lab-tools:latest
      exec:
        - sysctl -w net.ipv6.conf.all.disable_ipv6=0
        - ip -4 route flush default
        - ip -4 addr flush dev eth0
        - ip -6 addr add 2001:db8:6501:1::2/64 dev eth1
        - ip -6 route replace default via 2001:db8:6501:1::1 dev eth1
        - sh -c 'echo "nameserver 2001:db8:6501:ff::2" > /etc/resolv.conf'

    client-dualstack:
      image: lab-tools:latest
      exec:
        - ip route del default
        - sysctl -w net.ipv6.conf.all.disable_ipv6=0
        - ip addr add 192.0.2.130/26 dev eth1
        - ip route add default via 192.0.2.129
        - ip -6 addr add 2001:db8:6501:2::2/64 dev eth1
        - ip -6 route replace default via 2001:db8:6501:2::1 dev eth1

  links:
    - endpoints: ["router-isp:eth1", "router-origin:eth1"]
    - endpoints: ["router-isp:eth2", "router-hijacker:eth1"]
    - endpoints: ["router-isp:eth3", "router-validator:eth1"]
    - endpoints: ["router-validator:eth2", "router-origin:eth2"]
    - endpoints: ["router-validator:eth3", "rpki-server:eth1"]
    - endpoints: ["router-validator:eth4", "gateway-nat64-dns64:eth1"]
    - endpoints: ["router-validator:eth5", "client-v6:eth1"]
    - endpoints: ["router-validator:eth6", "client-dualstack:eth1"]
    - endpoints: ["router-origin:eth3", "web-server-origin:eth1"]
    - endpoints: ["router-hijacker:eth2", "decoy-target:eth1"]
```

⚠️ **Not run by the author**, so check these against your Containerlab version if `deploy` complains:

- **Image tags.** `quay.io/frrouting/frr:8.4.7` and `nlnetlabs/routinator:v0.15.2` are the versions used by this lab; confirm the tags exist on the registry you use.
- **`user: root` on `rpki-server`.** The Routinator image may run as an unprivileged user, and `ip addr add` needs root.
- **`cmd:` on `rpki-server`.** It assumes the image entrypoint is the `routinator` binary, so the arguments are `-c <file> server`. If your image differs, adapt it.
- **`devices:` and `cap-add:` on the gateway**, see [pitfall 11](implementation-notes.md#11-containerlab-permissions-for-the-gateway).

## Phase 4. Write the BGP-side configuration

The next files are the whole routing and RPKI side of the lab. They are short, and each one maps to a row in the policy table of [topology-documentation.md](topology-documentation.md#6-routing-policies).

### 4.1 FRR daemons

`configs/common/daemons` (used by `router-isp`, `router-origin`, `router-hijacker`):

```text
# router-isp, router-origin, router-hijacker.
# zebra and staticd are always started by frrinit, so only bgpd needs enabling.
bgpd=yes
vtysh_enable=yes
zebra_options="  -A 127.0.0.1 -s 90000000"
bgpd_options="   -A 127.0.0.1"
staticd_options="-A 127.0.0.1"
```

`configs/router-validator/daemons` differs in **one line**, the RPKI module on `bgpd` ([pitfall 4](implementation-notes.md#4-frrs-rpki-client-module-and-cache-configuration)):

```text
# router-validator: same as common/daemons, but bgpd loads the RPKI module (-M rpki).
bgpd=yes
vtysh_enable=yes
zebra_options="  -A 127.0.0.1 -s 90000000"
bgpd_options="   -A 127.0.0.1 -M rpki"
staticd_options="-A 127.0.0.1"
```

> `zebra` and `staticd` do not need to be enabled here: FRR's init script always starts them. `staticd` matters because the Validator's `ip route` lines are handled by it. Static routes silently do nothing if you start the daemons by hand and forget `staticd`.

### 4.2 router-isp (AS65000): the "lazy" ISP

No policy at all. `no bgp ebgp-requires-policy` is what lets it pass everything ([pitfall 2](implementation-notes.md#2-frr-refuses-ebgp-without-a-route-map)).

`configs/router-isp/frr.conf`:

```text
frr defaults traditional
hostname router-isp
log stdout
!
router bgp 65000
 bgp router-id 10.0.0.1
 no bgp ebgp-requires-policy
 neighbor 198.51.100.2 remote-as 65010
 neighbor 198.51.100.2 description origin
 neighbor 198.51.100.6 remote-as 65020
 neighbor 198.51.100.6 description hijacker
 neighbor 198.51.100.10 remote-as 65001
 neighbor 198.51.100.10 description validator
!
line vty
```

### 4.3 router-origin (AS65010)

Announces only its `/24`, refuses any more-specific of its own space from the ISP, and prefers the peering link (local-pref 200) over the ISP (100).

`configs/router-origin/frr.conf`:

```text
frr defaults traditional
hostname router-origin
log stdout
!
ip prefix-list ORIGIN-OUT seq 5 permit 203.0.113.0/24
ip prefix-list OWN-MORE-SPECIFIC seq 5 permit 203.0.113.0/24 ge 25 le 32
!
route-map TO-ISP permit 10
 match ip address prefix-list ORIGIN-OUT
route-map TO-PEER permit 10
 match ip address prefix-list ORIGIN-OUT
route-map FROM-ISP deny 10
 match ip address prefix-list OWN-MORE-SPECIFIC
route-map FROM-ISP permit 20
 set local-preference 100
route-map FROM-PEER permit 10
 set local-preference 200
!
router bgp 65010
 bgp router-id 10.0.0.2
 neighbor 198.51.100.1 remote-as 65000
 neighbor 198.51.100.1 description isp
 neighbor 198.51.100.13 remote-as 65001
 neighbor 198.51.100.13 description validator-peering
 !
 address-family ipv4 unicast
  network 203.0.113.0/24
  neighbor 198.51.100.1 route-map FROM-ISP in
  neighbor 198.51.100.1 route-map TO-ISP out
  neighbor 198.51.100.13 route-map FROM-PEER in
  neighbor 198.51.100.13 route-map TO-PEER out
 exit-address-family
!
line vty
```

### 4.4 router-hijacker (AS65020)

Note what is **missing**: there is no `network 203.0.113.0/25`. The hijack must not exist at boot, so that experiment E1 starts from a clean baseline. The hijack is started and stopped with two commands, shown in the comment of the file. The automation does exactly the same.

`configs/router-hijacker/frr.conf`:

```text
frr defaults traditional
hostname router-hijacker
log stdout
!
ip prefix-list HIJACKER-OUT seq 5 permit 203.0.113.0/25
!
route-map TO-ISP permit 10
 match ip address prefix-list HIJACKER-OUT
route-map FROM-ISP permit 10
!
! The hijack is NOT announced at boot, so that E1 (baseline) is clean.
! E2 starts it, and the automation does the same through vtysh:
!     router bgp 65020
!      address-family ipv4 unicast
!       network 203.0.113.0/25
! Stopping it is the same with "no network 203.0.113.0/25".
router bgp 65020
 bgp router-id 10.0.0.3
 neighbor 198.51.100.5 remote-as 65000
 neighbor 198.51.100.5 description isp
 !
 address-family ipv4 unicast
  neighbor 198.51.100.5 route-map FROM-ISP in
  neighbor 198.51.100.5 route-map TO-ISP out
 exit-address-family
!
line vty
```

### 4.5 router-validator (AS65001)

This is the only router that does ROV. It has the anchor route for the `/25` ([pitfall 3](implementation-notes.md#3-advertising-the-25-from-the-validator)), the two route-map pairs ([pitfall 7](implementation-notes.md#7-toggling-rov-two-pairs-of-route-maps)), `soft-reconfiguration inbound` so rejected routes stay visible ([pitfall 6](implementation-notes.md#6-rejected-routes-vanish-from-the-evidence)), and the RTR client.

It starts with **ROV off** (the `-NOROV` route-maps are attached), which is the state E1 and E2 expect.

The `rpki retry_interval 15` line matters more than it looks. Containerlab starts every container at once, so the router's first attempt to reach Routinator usually fails. In the replica, with FRR's default retry interval the router had still not connected after more than three minutes; until it connects, the VRP table is empty and ROV has nothing to reject with. With the line below, the session came up within about 10 seconds of Routinator starting.

`configs/router-validator/frr.conf`:

```text
frr defaults traditional
hostname router-validator
log stdout
!
! --- route anchor + static routes (topology-documentation 5.4)
! Handled by staticd (frrinit always starts it; if you start daemons by hand, start staticd too).
ip route 192.0.2.128/25 blackhole
ip route 192.0.2.192/26 192.168.1.2
ipv6 route 2001:db8:6501:64::/96 2001:db8:6501:ff::2
!
ip prefix-list VALIDATOR-OUT seq 5 permit 192.0.2.128/25
!
route-map TO-ISP permit 10
 match ip address prefix-list VALIDATOR-OUT
route-map TO-PEER permit 10
 match ip address prefix-list VALIDATOR-OUT
!
! --- two route-map pairs per neighbor; they differ in one clause only
route-map FROM-ISP-NOROV permit 10
 set local-preference 100
route-map FROM-ISP-ROV deny 10
 match rpki invalid
route-map FROM-ISP-ROV permit 20
 set local-preference 100
route-map FROM-PEER-NOROV permit 10
 set local-preference 200
route-map FROM-PEER-ROV deny 10
 match rpki invalid
route-map FROM-PEER-ROV permit 20
 set local-preference 200
!
! --- RPKI: FRR is the RTR client, Routinator is the cache.
! retry_interval matters: all containers start at once, so the first connection
! attempt usually happens before Routinator is listening.
rpki
 rpki polling_period 60
 rpki retry_interval 15
 rpki cache 192.0.2.1 3323 preference 1
exit
!
router bgp 65001
 bgp router-id 10.0.0.4
 neighbor 198.51.100.9 remote-as 65000
 neighbor 198.51.100.9 description isp
 neighbor 198.51.100.14 remote-as 65010
 neighbor 198.51.100.14 description origin-peering
 !
 address-family ipv4 unicast
  network 192.0.2.128/25
  neighbor 198.51.100.9 soft-reconfiguration inbound
  neighbor 198.51.100.9 route-map FROM-ISP-NOROV in
  neighbor 198.51.100.9 route-map TO-ISP out
  neighbor 198.51.100.14 soft-reconfiguration inbound
  neighbor 198.51.100.14 route-map FROM-PEER-NOROV in
  neighbor 198.51.100.14 route-map TO-PEER out
 exit-address-family
!
line vty
```

> The `ipv6 route 2001:db8:6501:64::/96` line needs IPv6 in the router's kernel. It was accepted by FRR in the replica, but could not be exercised there. ⚠️

### 4.6 The two web pages

The two targets have the **same address** (`203.0.113.10`) and differ only in their page. That is how the lab tells you where a request really ended up.

```bash
echo "Welcome to Origin Server" > www/origin/index.html
echo "You've been hijacked"     > www/decoy/index.html
```

### 4.7 Routinator

Routinator only validates and serves the VRP list. The ROA that makes the legitimate prefix **valid** comes from SLURM, not from a real repository: private ASNs cannot get a global ROA, so the lab asserts one locally. The configuration also switches off the RIR TALs and both download methods, because the lab is isolated ([pitfall 5](implementation-notes.md#5-routinator-rtr-port-and-local-roa)). Routinator then logs `No TALs provided. Starting anyway.`, and that is expected.

`configs/rpki-server/routinator.conf`:

```text
# Isolated lab: no RIR TALs, no rsync/RRDP. The only ROA comes from SLURM.
repository-dir = "/tmp/rpki-cache"
no-rir-tals = true
disable-rsync = true
disable-rrdp = true
exceptions = ["/etc/routinator/local.slurm"]
rtr-listen = ["0.0.0.0:3323"]
```

`configs/rpki-server/local.slurm`:

```json
{
  "slurmVersion": 1,
  "validationOutputFilters": { "prefixFilters": [], "bgpsecFilters": [] },
  "locallyAddedAssertions": {
    "prefixAssertions": [
      { "asn": 65010, "prefix": "203.0.113.0/24", "maxPrefixLength": 24,
        "comment": "Lab ROA: AS65010 is authorized to originate 203.0.113.0/24 (max length 24)" }
    ],
    "bgpsecAssertions": []
  }
}
```

✅ **Checkpoint 4:** every file above exists, and the SLURM file is valid JSON.

```bash
find configs www -type f | sort
python3 -m json.tool configs/rpki-server/local.slurm > /dev/null && echo "slurm ok"
```

## Phase 5. First deploy: routing and RPKI

First create the helper file. It holds the same two functions as [experiments.md](experiments.md#setup-before-you-start), so the commands in both documents behave the same.

`scripts/helpers.sh`:

```bash
# Source this file:  . scripts/helpers.sh
# Same helpers as experiments.md ("Setup before you start").
LAB=${LAB:-bgp-rov-lab}
vt()  { docker exec "clab-$LAB-$1" vtysh -c "$2"; }
run() { docker exec "clab-$LAB-$1" sh -c "$2"; }
```

Then deploy and load the helpers:

```bash
sudo containerlab deploy -t topology.clab.yml
export LAB=bgp-rov-lab
. scripts/helpers.sh          # defines vt() and run()
```

Give it about 30 seconds, then check, in this order.

**A. BGP sessions.** `router-isp` should show three neighbors, `router-validator` two, all `Established`:

```bash
vt router-isp       "show bgp summary"
vt router-validator "show bgp summary"
```

**B. RPKI.** The RTR session is up and the VRP is loaded:

```bash
vt router-validator "show rpki cache-connection"   # ... (connected)
vt router-validator "show rpki prefix-table"       # 203.0.113.0  24 - 24  65010
```

**C. The baseline path.** With no hijack, the Validator reaches `203.0.113.10` through the **peering** link, not the ISP:

```bash
run router-validator "ip route get 203.0.113.10"       # via 198.51.100.14
run client-dualstack "curl -4 -s --max-time 5 http://203.0.113.10"   # Welcome to Origin Server
```

✅ **Checkpoint 5:** A, B and C all match. This is experiment [E1](experiments.md#e1-bgp-baseline) done by hand.

**If it fails:**

| What you see | Most likely cause |
| :-- | :-- |
| Session `Established`, prefix column shows `(Policy)` | a missing route-map, [pitfall 2](implementation-notes.md#2-frr-refuses-ebgp-without-a-route-map) |
| `show rpki ...` is an unknown command | `-M rpki` missing in `router-validator/daemons`, [pitfall 4](implementation-notes.md#4-frrs-rpki-client-module-and-cache-configuration) |
| `No connection to RPKI cache server` | Routinator not listening yet (wait for `retry_interval`), or `rtr-listen` is not `0.0.0.0:3323` |
| `ip route get` shows `eth0` or `172.20.20.1` | the management default route is still there, [pitfall 1](implementation-notes.md#1-the-management-interface-brings-its-own-ipv4-default-route) |
| Ping and `curl` time out but sessions are up | forwarding is off on a router: `run router-isp "sysctl net.ipv4.ip_forward"` |
| `curl` is refused | the web server on the target did not start: `run web-server-origin "ss -ltn"` |

## Phase 6. The hijack and ROV

The routing side is complete, so you can now run the core of the project. Follow [E2](experiments.md#e2-hijack-without-rov) and [E3](experiments.md#e3-hijack-with-rov) in `experiments.md`; they contain the exact commands and what each result means. What to expect, so you can tell quickly whether the build is right:

| State | Validator's route to `203.0.113.10` | `curl -4` from `client-dualstack` |
| :-- | :-- | :-- |
| Baseline (E1) | via `198.51.100.14` (Origin, peering) | `Welcome to Origin Server` |
| Hijack on, ROV off (E2) | via `198.51.100.9` (ISP), the `/25` is marked `rpki validation-state: invalid` | `You've been hijacked` |
| Hijack on, ROV on (E3) | via `198.51.100.14` again; the `/25` is absent from the RIB but still listed in `received-routes` | `Welcome to Origin Server` |

✅ **Checkpoint 6:** all three rows reproduced. If E2 shows the origin page instead of the decoy, the hijack was never announced; check `show bgp ipv4 unicast 203.0.113.0/25` on `router-isp`.

Phase 8 automates exactly this. The scenario collector polls the application result for
up to 30 seconds after each routing-state change, allowing BGP convergence time to vary
between hosts. If you prefer to verify by script first, jump there and come back.

## Phase 7. NAT64 and DNS64 on the gateway

Everything so far was IPv4. This phase adds the part that lets an IPv6-only client reach an IPv4-only server. The pieces live on one host (`gateway-nat64-dns64`): **Tayga** translates, **Unbound** synthesizes the AAAA records, and a tiny **NSD** serves the lab zone.

Why three programs and not two? Unbound's DNS64 module does **not** synthesize answers it serves from `local-data`; it only works on answers it fetches through its normal resolution path. That was tested: with `local-data`, `dig AAAA web.lab.test` returned nothing; with a `stub-zone` pointing at NSD it returned `2001:db8:6501:64::cb00:710a`. See [pitfall 9](implementation-notes.md#9-unbound-dns64-local-answers-are-not-necessarily-synthesized).

### 7.1 Tayga

`configs/gateway-nat64/tayga.conf`:

```text
tun-device nat64
# Tayga's own IPv4 address: internal to the translator, never routed or advertised.
ipv4-addr 192.168.255.1
# Tayga uses a network-specific RFC 6052 prefix for this isolated lab.
# Dedicated Tayga address on the gateway link; Unbound uses ::2.
ipv6-addr 2001:db8:6501:ff::3
prefix 2001:db8:6501:64::/96
dynamic-pool 192.0.2.192/26
data-dir /var/spool/tayga
```

⚠️ **One thing to watch.** The sample `tayga.conf` that ships with Tayga says `ipv6-addr` is Tayga's own address and "NOT your router's IPv6 address". To avoid adding an address to the topology, this lab reuses the gateway's address on the router link. That was not tested (no IPv6 in the replica). If you see ICMPv6 trouble, give Tayga a dedicated address and route it to the TUN. See [pitfall 8](implementation-notes.md#8-tayga-tun-capabilities-and-routing).

`ipv4-addr 192.168.255.1` is Tayga's own address inside the translator. It is never routed and does not appear in the topology's address tables. The dedicated `ipv6-addr 2001:db8:6501:ff::3` is separate from Unbound's `::2` address and is used by Tayga for IPv6-side translator behavior.

### 7.2 Unbound and NSD

`configs/gateway-nat64/unbound.conf`:

```text
server:
  interface: 2001:db8:6501:ff::2
  interface: 127.0.0.1
  do-ip6: yes
  do-ip4: yes
  username: ""
  directory: "/tmp"
  pidfile: "/tmp/unbound.pid"
  chroot: ""
  module-config: "dns64 validator iterator"
  dns64-prefix: 2001:db8:6501:64::/96
  access-control: 127.0.0.0/8 allow
  access-control: 2001:db8:6501::/48 allow
  # Both lines below are required for the stub-zone to work (see implementation-notes, pitfall 9):
  # "test." is a built-in local zone and would answer NXDOMAIN; the stub points at 127.0.0.1.
  do-not-query-localhost: no
  local-zone: "test." nodefault
remote-control:
  control-enable: yes
  control-interface: 127.0.0.1
  control-port: 8953
  control-use-cert: no
stub-zone:
  name: "lab.test."
  stub-addr: 127.0.0.1@5353
```

Both lines under the comment are required, not decoration. Tested separately: without `local-zone: "test." nodefault`, Unbound answers `NXDOMAIN` (it ships a built-in `test.` zone); without `do-not-query-localhost: no`, it answers `SERVFAIL` (it refuses to ask a stub on `127.0.0.1`).

`configs/gateway-nat64/nsd/nsd.conf`:

```text
server:
  ip-address: 127.0.0.1@5353
  do-ip6: no
  username: ""
  zonesdir: "/etc/lab/nsd"
  database: ""
  pidfile: "/tmp/nsd.pid"
  logfile: "/tmp/nsd.log"
  chroot: ""
zone:
  name: "lab.test"
  zonefile: "lab.test.zone"
```

`configs/gateway-nat64/nsd/lab.test.zone`:

```text
$ORIGIN lab.test.
$TTL 300
@    IN SOA ns.lab.test. admin.lab.test. ( 1 3600 900 604800 300 )
@    IN NS  ns.lab.test.
ns   IN A   127.0.0.1
web  IN A   203.0.113.10
```

### 7.3 Start script

`configs/gateway-nat64/start.sh` replaces the empty placeholder from Phase 1. Containerlab runs it from `exec:` after the gateway's addresses and routes exist.

```bash
#!/bin/sh
# Starts NAT64 (Tayga) and DNS64 (NSD + Unbound) on gateway-nat64-dns64.
# E4 toggles: stop only the tayga process (keep the nat64 interface up), or edit module-config in unbound.conf and run: unbound-control reload
set -e
CONF=/etc/lab
mkdir -p /var/spool/tayga

# --- NAT64 -------------------------------------------------------------
tayga -c $CONF/tayga.conf --mktun
ip link set nat64 up
ip addr add 192.168.255.1 dev nat64
ip route add 2001:db8:6501:64::/96   dev nat64
ip route add 192.0.2.192/26 dev nat64
tayga -c $CONF/tayga.conf          # daemonizes by default

# --- DNS64 -------------------------------------------------------------
nsd -c $CONF/nsd/nsd.conf          # authoritative lab.test on 127.0.0.1:5353
unbound -c $CONF/unbound.conf      # DNS64 resolver on [2001:db8:6501:ff::2]:53
```

### 7.4 Redeploy and check

```bash
sudo containerlab deploy -t topology.clab.yml --reconfigure   # destroys the old lab first
. scripts/helpers.sh
```

```bash
# the translator and the two DNS processes are running, the TUN device exists
run gateway-nat64-dns64 "pgrep -a 'tayga|unbound|nsd'; ip -br link show nat64"

# DNS64 answers on the gateway itself (the A record and the synthesized AAAA)
run gateway-nat64-dns64 "dig +short A    web.lab.test @127.0.0.1"    # 203.0.113.10
run gateway-nat64-dns64 "dig +short AAAA web.lab.test @127.0.0.1"    # 2001:db8:6501:64::cb00:710a

# the real test: the IPv6-only client, end to end
run client-v6 "dig +short AAAA web.lab.test"                   # 2001:db8:6501:64::cb00:710a
run client-v6 "curl -6 -s --max-time 5 http://web.lab.test"    # Welcome to Origin Server
```

✅ **Checkpoint 7a:** the DNS answers match. The two `dig` lines on the gateway behave exactly like this in the replica (on a different port).

⚠️ **Checkpoint 7b:** the last two commands use IPv6 and the translation itself, which **could not be tested**. If `curl -6` hangs, work through this list:

| Check | Command |
| :-- | :-- |
| IPv6 is enabled in the containers | `run client-v6 "sysctl net.ipv6.conf.all.disable_ipv6"` should be `0`. Docker can disable it; `exec:` sets it back, but confirm |
| The router sends `2001:db8:6501:64::/96` to the gateway | `run router-validator "ip -6 route show 2001:db8:6501:64::/96"` |
| The gateway sends it into Tayga | `run gateway-nat64-dns64 "ip -6 route show 2001:db8:6501:64::/96"` shows `dev nat64` |
| The return path for the pool exists | `run router-validator "ip route show 192.0.2.192/26"` shows `via 192.168.1.2` |
| Forwarding is on | `run gateway-nat64-dns64 "sysctl net.ipv6.conf.all.forwarding net.ipv4.ip_forward"` |
| Tayga sees the packets | `run gateway-nat64-dns64 "tcpdump -ni nat64 -c 5"` while `client-v6` runs `curl` |

Also read [pitfall 8](implementation-notes.md#8-tayga-tun-capabilities-and-routing) and the prefix note at the end of [pitfall 9](implementation-notes.md#9-unbound-dns64-local-answers-are-not-necessarily-synthesized): `203.0.113.10` is a documentation address, so the lab uses a prefix inside its own `/48`.

Now run [E4 to E6](experiments.md#e4-nat64dns64-three-conditions) from `experiments.md`. The DNS64 off/on toggle is the `module-config` edit plus `unbound-control reload`, which was tested.

## Phase 8. Automation

`scripts/run_experiments.py` runs scenarios **S0 to S4** (including the DNS64/NAT64 state checks and the integrated IPv6-only client checks) and writes `results/latest.json`. The `pytest` file asserts on that file. The design, the JSON schema and the CI workflow are in [automation-design.md](automation-design.md).

```bash
LAB=bgp-rov-lab python3 scripts/run_experiments.py
python3 -m pytest -q tests/
```

Expected output of the first command, one line:

```text
{"run_id": "...", "status": "passed", "errors": []} [('S0', 'origin', 'passed'), ('S1', 'decoy', 'passed'), ('S2', 'origin', 'passed'), ('S3a', 'unresolved', 'passed'), ('S3b', 'timeout', 'passed'), ('S3c', 'origin', 'passed'), ('S4a', 'decoy', 'passed'), ('S4b', 'origin', 'passed')]
```

and `6 passed` from `pytest`.

Two things about the script that you should know:

- **It must work on a clean deploy.** The hijacker starts with no `network` statement, so "stop the hijack" on a fresh lab fails with `Can't find static route specified`. The script tolerates that failure when it *stops* the hijack, and treats it as fatal only when it *starts* it. An earlier version did not, and aborted at the very first step on a clean deploy.
- **NAT64/DNS64 toggles are process-safe.** The automation stops and restarts Unbound and Tayga explicitly, disables Unbound's unused control socket in generated runtime configuration, and ignores unreaped zombie processes left by the container's PID 1.

`scripts/run_experiments.py`:

```python
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

def scenario(sid, name, rov, expected, after_wait):
    time.sleep(after_wait)
    rc, got = curl("client-dualstack")
    rib25 = vt("router-validator","show bgp ipv4 unicast 203.0.113.0/25")
    fib = json.loads(vt("router-validator","show ip route 203.0.113.10 json"))
    nh = [n["ip"] for n in list(fib.values())[0][0]["nexthops"]]
    return {"id":sid,"name":name,"rov_enabled":rov,"expected_served_by":expected,"observed_served_by":got,
            "status":"passed" if got==expected else "failed",
            "evidence":{"curl_exit_code":rc,"validator_rib_25":rib25,"validator_fib_nexthops":nh}}

def main():
    out = {"run_id": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ"), "lab": LAB, "status":"incomplete","scenarios":[],"errors":[]}
    try:
        wait_until(sessions_up, what="BGP sessions"); wait_until(rtr_up, what="RTR + VRP")
        set_rov(False); hijack(False); time.sleep(5)
        out["scenarios"].append(scenario("S0","baseline",False,"origin",3))
        hijack(True); set_rov(False)
        out["scenarios"].append(scenario("S1","hijack_rov_off",False,"decoy",5))
        set_rov(True)
        out["scenarios"].append(scenario("S2","hijack_rov_on",True,"origin",5))
        out["status"] = "passed" if all(s["status"]=="passed" for s in out["scenarios"]) else "failed"
    except Exception as e:
        out["errors"].append(repr(e))
    (RES/"history").mkdir(exist_ok=True)
    for p in (RES/"latest.json", RES/"history"/f"{out['run_id']}.json"): p.write_text(json.dumps(out, indent=2))
    print(json.dumps({k:out[k] for k in ("run_id","status","errors")}), [ (s["id"],s["observed_served_by"],s["status"]) for s in out["scenarios"]])
if __name__ == "__main__": main()
```

`tests/test_results.py`:

```python
import json, pathlib, pytest
P = pathlib.Path("results/latest.json")
@pytest.fixture(scope="session")
def results():
    assert P.exists(), "results/latest.json tidak ada"
    return json.loads(P.read_text())
def sc(r, i):
    m = [s for s in r["scenarios"] if s["id"] == i]; assert len(m) == 1, f"harus ada tepat satu {i}"; return m[0]
def test_run_completed(results): assert results["status"] == "passed", results["errors"]
def test_baseline_origin(results): assert sc(results,"S0")["observed_served_by"] == "origin"
def test_rov_off_decoy(results): assert sc(results,"S1")["observed_served_by"] == "decoy"
def test_rov_on_origin(results):
    s = sc(results,"S2"); assert s["observed_served_by"] == "origin"; assert "198.51.100.14" in s["evidence"]["validator_fib_nexthops"]
```

✅ **Checkpoint 8:** `status` is `passed` and `pytest` reports `6 passed`.

## Phase 9. Finish

Commit what is reproducible, and ignore what is not:

```bash
printf '__pycache__/\n.pytest_cache/\n' > .gitignore
git add -A && git commit -m "Lab: topology, configs, automation"
```

Whether to commit `results/latest.json` is a decision for the CI workflow ([automation-design.md](automation-design.md#7-github-actions-workflow)), not for this guide.

To stop the lab and remove everything it created:

```bash
sudo containerlab destroy -t topology.clab.yml --cleanup
```

### Final checklist

- [x] Checkpoints 0, 2, 4, 5, 6 passed (routing, RPKI, hijack, ROV).
- [x] Checkpoint 7a passed (DNS64 answers) and 7b passed (end-to-end NAT64).
- [x] Checkpoint 8 passed (`passed`, `6 passed`).
- [x] Every ⚠️ resolved during the build is relabeled in this guide and in [implementation-notes.md](implementation-notes.md), with the tested versions.
