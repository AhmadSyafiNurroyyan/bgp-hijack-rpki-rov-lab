# Implementation Notes: Why These Tools, and the Pitfalls Worth Knowing

Read this before building the lab. It covers two things:

1. **Why** certain tools were chosen, so the decisions can be re-evaluated.
2. **Pitfalls** that are known to bite, with ways to avoid them.

The experiment steps are in [experiments.md](experiments.md). Topology and addressing are in [topology-documentation.md](topology-documentation.md).

**How to read the status labels:**

| Label | Meaning |
| :-- | :-- |
| ✅ | Settled behavior (a fact or a design decision) |
| ⚠️ | **Not yet verified** on the versions in use. Check the official documentation before relying on it |

> If you have tried a ⚠️ item and the result is confirmed, change the label to ✅ and note the version you used. A document that is honest about what has and hasn't been tested is far more useful than one that sounds certain about everything.

---

# Part 1. Why these tools

| Component | Tool | Why it was chosen | Common alternatives |
| :-- | :-- | :-- | :-- |
| Routing daemon | **FRRouting (FRR)** | Open source, built-in RPKI/RTR client, `vtysh` CLI in a Cisco IOS style (but FRR is **not** a Cisco product) | BIRD |
| Lab orchestration | **Containerlab** | The whole topology in one YAML file, deployed with one command | GNS3, EVE-NG |
| RPKI validator | **Routinator** (NLnet Labs) | Validates ROAs and sends VRPs to the router over RTR | rpki-client, FORT |
| NAT64 | **Tayga** | Userspace + TUN, no special kernel module needed on the host | Jool |
| DNS64 | **Unbound** | Native `dns64` module | BIND 9 |
| Container runtime | **Docker** | Prerequisite for Containerlab | n/a |
| Testing and CI | **Python + pytest + GitHub Actions** | Results can be verified automatically on every change; design in [automation-design.md](automation-design.md) | n/a |
| Lab host | Local Linux laptop/VM; optionally Oracle Cloud Always Free (ARM, 2 OCPU/12 GB) | Lightweight lab (10 nodes, small images), fits within the free-tier limits | n/a |

> The "Common alternatives" column only lists other options that are commonly used for the same role. It does not claim that each one was compared individually.

### The two decisions people ask about most

**Tayga, not Jool.** Jool runs as a kernel module, so the host has to load it, and containers do not have a kernel of their own. Tayga runs in userspace with a TUN device, so all it needs is `/dev/net/tun` and the `NET_ADMIN` capability in the container. The price: userspace translation is generally slower than in-kernel translation. For a lab whose purpose is to prove a concept, that does not matter.

**Routinator validates, the router rejects.** This is the most common misunderstanding about RPKI. Routinator only produces the list of VRPs (pairs of prefix, max-length, and authorized ASN) and sends it over RTR. The decision to "reject invalid routes" is carried out by `router-validator` through a route-map. That is why the ROV toggle lives in FRR, not in Routinator.

### One note if you run the lab on Oracle Always Free

⚠️ An ARM instance means every image must have an `arm64` variant, including the custom `gateway-nat64` image, which has to be built on the same architecture. Check that `arm64` variants exist for the FRR and Routinator images before deploying.

---

---

# Part 2. Known pitfalls

They are ordered the way you will probably run into them while building the lab. Commands that use `vt` and `run` rely on the two helper functions defined in [experiments.md](experiments.md#setup-before-you-start).

## 1. The management interface brings its own IPv4 default route

⚠️ **Symptom:** `client-v6` is meant to be IPv6-only, but `ip -4 addr` shows an IPv4 address on `eth0` (for example `172.20.20.x`). Clients and the gateway reach `203.0.113.10` through the Docker management network instead of the lab router, which shows up as odd timeouts, or as experiment E4a not failing the way it should.

**Cause:** Containerlab attaches every container's `eth0` to its management network (IPv4 by default), and the container gets a default route through it. The links defined in the topology file start at `eth1`.

**Fix:** after deploy, point the default route at the lab router, and strip IPv4 from the IPv6-only client. Examples (verify interface names, and run them from the topology's `exec:` or from the setup script):

```bash
run client-dualstack "ip route replace default via 192.0.2.129"
run gateway-nat64    "ip route replace default via 192.168.1.1"
run client-v6        "ip -4 route flush default; ip -4 addr flush dev eth0"
```

Also confirm forwarding is on wherever traffic is routed (`router-*` and `gateway-nat64`): `net.ipv4.ip_forward=1` and `net.ipv6.conf.all.forwarding=1`. Router containers do not always have it enabled by default, so check with `sysctl` before debugging anything else.

## 2. FRR refuses eBGP without a route-map

✅ **Symptom:** the eBGP session is `Established`, but no prefixes are exchanged. In `show bgp summary`, the prefix column shows `(Policy)`.

**Cause:** modern FRR by default neither accepts nor sends eBGP routes without a route-map (`bgp ebgp-requires-policy`).

**Fix:**

- On `router-isp`, turn it off. This matches the "no filtering" narrative.
- On the other routers, write explicit route-maps (inbound and outbound). The routing policy table in [topology-documentation.md](topology-documentation.md#6-routing-policies) requires them anyway.

```
router bgp 65000
 no bgp ebgp-requires-policy
```

## 3. Advertising the `/25` from the Validator

⚠️ **Symptom:** `network 192.0.2.128/25` is configured, but the prefix is never announced.

**Cause:** BGP only announces prefixes that exist in the routing table. On the Validator there are only two `/26` pieces (the client LAN and the Tayga pool), not the `/25` itself.

**Fix:** create an "anchor" so the `/25` exists in the routing table. The more specific `/26`s still win for real traffic, so the anchor does not interfere.

```
ip route 192.0.2.128/25 blackhole
!
router bgp 65001
 address-family ipv4 unicast
  network 192.0.2.128/25
```

Verify the syntax on the FRR version you are using.

## 4. FRR's RPKI client: module and cache configuration

⚠️ **Symptom:** `show rpki ...` commands are rejected as unknown, or `show rpki cache-connection` shows no connection to the RTR server.

**Cause:** RPKI support in FRR is a loadable `bgpd` module, and the router also has to be told where the RTR server is. Routinator alone does nothing until the router connects to it.

**Fix (illustration, verify on your FRR version):**

```
# /etc/frr/daemons
bgpd_options="   -A 127.0.0.1 -M rpki"
```

```
! frr.conf on router-validator
rpki
 rpki cache 192.0.2.1 3323 preference 1
exit
```

Check first that the FRR image you use was built with RTR support. Then confirm with `show rpki cache-connection` (connected) and `show rpki prefix-table` (VRPs loaded) before running any ROV experiment.

## 5. Routinator: RTR, port, and local ROA

⚠️ **Symptom:** the router cannot connect to `rpki-server`, or the VRP list is empty.

**Causes and fixes:**

- The RTR server listens only on `localhost` by default. In the `rpki-server` container, set:
  ```
  rtr-listen = ["0.0.0.0:3323"]
  ```
- Port **3323** is the Routinator documentation's convention, not an IANA port. The official port is 323, which is privileged.
- The local ROA is supplied through **SLURM**. In an isolated lab, Routinator needs to be configured so it does not depend on the TALs and RIR repositories.

Verify the exact options in the [Routinator documentation](https://routinator.docs.nlnetlabs.nl/en/stable/rtr-service.html) **before starting the RPKI experiments**.

## 6. Rejected routes "vanish" from the evidence

⚠️ **Symptom:** ROV is on and the `/25` is rejected, but there is no trace at all that the route ever arrived. It is hard to prove that ROV is actually working.

**Cause:** a route rejected by a route-map never enters the RIB.

**Fix:** enable `soft-reconfiguration inbound` so the route remains visible in `received-routes`.

```
router bgp 65001
 address-family ipv4 unicast
  neighbor <ISP_IP> soft-reconfiguration inbound
```

```bash
vt router-validator "show bgp ipv4 unicast neighbors <ISP_IP> received-routes"
```

## 7. Toggling ROV: two pairs of route-maps

✅ **Design:** prepare two pairs of route-maps per neighbor, then swap the one that is attached.

| Neighbor | ROV off | ROV on |
| :-- | :-- | :-- |
| ISP | `FROM-ISP-NOROV` | `FROM-ISP-ROV` |
| Peer (Origin) | `FROM-PEER-NOROV` | `FROM-PEER-ROV` |

The two variants differ in **exactly one clause**: the `-ROV` variant has `match rpki invalid` → `deny`. The peer's (Origin's) local-pref stays above the provider's (ISP's) in both variants, so that when ROV is on, the path through the peering link is really the one chosen.

⚠️ The skeleton below is only an illustration (the local-pref numbers are examples). Verify the syntax on the FRR you are using:

```
route-map FROM-ISP-ROV deny 10
 match rpki invalid
route-map FROM-ISP-ROV permit 20
 set local-preference 100
!
route-map FROM-ISP-NOROV permit 10
 set local-preference 100
!
route-map FROM-PEER-ROV deny 10
 match rpki invalid
route-map FROM-PEER-ROV permit 20
 set local-preference 200
!
route-map FROM-PEER-NOROV permit 10
 set local-preference 200
```

To swap: attach the desired route-map to the neighbor, then force a re-evaluation.

```bash
vt router-validator "clear bgp ipv4 unicast * soft in"
```

## 8. Tayga: TUN, capabilities, and routing

⚠️ **Symptom:** Tayga will not start, or starts but no traffic passes.

**What is required:**

- `/dev/net/tun` and the `NET_ADMIN` capability in the container.
- IPv4 and IPv6 forwarding enabled on the gateway.
- A `tayga.conf` file with these keys:

```
tun-device   <tun-interface-name>
ipv4-addr    <Tayga's own IPv4, outside the dynamic-pool>
prefix       2001:db8:6501:64::/96
dynamic-pool 192.0.2.192/26
data-dir     <state-directory>
```

- Create and bring up the TUN interface, then point two routes at it:

```bash
tayga --mktun
ip link set <tun-interface-name> up
ip route add 2001:db8:6501:64::/96   dev <tun-interface-name>
ip route add 192.0.2.192/26 dev <tun-interface-name>
```

- The gateway also needs two other routes: an IPv4 default route to the router (`192.168.1.1`, see pitfall 1), and a route for `2001:db8:6501::/48` via `2001:db8:6501:ff::1`.

**Check first, before you start:**

- Is the `tayga` package available in the Alpine repositories? If not, use a Debian base image or build from source.
- Confirm the configuration key names with `man tayga.conf`, including whether your setup also needs `ipv6-addr` (Tayga's own IPv6 address, used for ICMPv6 errors).
- Keep the TUN interface up when you "turn Tayga off" for experiment E4b (stop only the process). That is what produces a silent timeout instead of an immediate "unreachable".

## 9. Unbound DNS64: local answers are not necessarily synthesized

⚠️ **Symptom:** `dig AAAA web.lab.test` returns nothing even though DNS64 is on.

**Basic configuration:**

```
server:
  interface: 2001:db8:6501:ff::2
  module-config: "dns64 validator iterator"
  dns64-prefix: 2001:db8:6501:64::/96
  access-control: 2001:db8:6501::/48 allow
  local-zone: "lab.test." static
  local-data: "web.lab.test. A 203.0.113.10"
```

`.test` is reserved for testing by RFC 6761. `.local` is avoided because it is used by mDNS.

**Suspected cause:** local answers (`local-data`) are likely **not synthesized** by the DNS64 module. The module synthesizes from an A sub-query that it hands to the *remaining modules* (`validator`, `iterator`), while `local-data` is answered earlier, before the module chain runs.

**If that turns out to be true:** use a `stub-zone` pointing to a small authoritative DNS server on the same gateway.

This **needs to be tested**. Do not trust this suspicion until it is confirmed.

**Toggling DNS64 for E4:** removing `dns64` from `module-config` needs a reload or restart of Unbound. `unbound-control` only works if `remote-control: control-enable: yes` is configured, so either enable it or restart the process.

**Prefix choice.** Because `203.0.113.10` is a documentation address, this lab uses the network-specific prefix `2001:db8:6501:64::/96` rather than the well-known NAT64 prefix. Unbound and Tayga use the same prefix, producing `2001:db8:6501:64::cb00:710a`; this was verified end to end on WSL2.

## 10. client-v6 has no DHCP/RA

✅ **Symptom:** `client-v6` cannot resolve any name.

**Cause:** there is no DHCP or Router Advertisement in the lab, so nothing tells the client the resolver's address.

**Fix:** set a static resolver.

```bash
echo "nameserver 2001:db8:6501:ff::2" > /etc/resolv.conf
```

## 11. Containerlab: permissions for the gateway

⚠️ **Symptom:** Tayga fails to create the TUN interface inside the container.

**Fix:** include the capability and the device in the gateway node's definition.

```yaml
gateway-nat64:
  kind: linux
  cap-add:
    - NET_ADMIN
  devices:
    - /dev/net/tun:/dev/net/tun
```

Verify the field names in the Containerlab documentation for the version you are using.

---

## Found a new pitfall?

Add it to Part 2 using the same pattern: **symptom → cause → fix**, plus a ✅ or ⚠️ label. If you open an issue on this repo, include the versions of FRR, Routinator, Tayga, Unbound, and Containerlab you are using, because much of the behavior in this document depends on versions.
