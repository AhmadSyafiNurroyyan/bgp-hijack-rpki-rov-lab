# Technical Documentation of the Network Topology

## 1. Purpose and Scope

This document describes the network topology design for two experiments planned within a single Containerlab environment:

1. **BGP hijacking and RPKI Route Origin Validation (ROV):** to observe how unauthorized prefix announcements can affect traffic paths and how ROV helps reject routes with an RPKI-invalid status.
2. **IPv6-only access to IPv4-only services:** to explain the roles of DNS64 and NAT64 in allowing IPv6-only clients to access services available only over IPv4.

This document focuses on the topology design, node roles, addressing, eBGP relationships, routing policies, and design decisions.

## 2. Topology Diagram

%%{init: {"flowchart": {"curve": "linear"}}}%%
flowchart LR
    subgraph ORIGIN["AS Origin · AS65010"]
        direction TB
        R_ORG(["<b>Router Origin</b><br/>FRR · eBGP"])
        WEB["<b>Web Server Origin</b><br/>IPv4: 203.0.113.10/24"]
    end

    subgraph ISP["ISP Transit · AS65000"]
        direction TB
        R_ISP(["<b>Router ISP</b><br/>FRR · eBGP"])
    end

    subgraph HIJACK["AS Hijacker · AS65020"]
        direction TB
        R_HJ(["<b>Router Hijacker</b><br/>FRR · eBGP"])
        DECOY["<b>Decoy Target</b><br/>IPv4: 203.0.113.10/25"]
    end

    subgraph VAL["AS Validator · AS65001"]
        direction TB
        R_VAL(["<b>Router Validator</b><br/>FRR · eBGP + RPKI ROV"])
        GW["<b>Gateway Tayga NAT64 + Unbound DNS64</b>"]
        C6["<b>Client-v6</b>"]
        CDS["<b>DualStack Client</b>"]
    end

    RPKI[("<b>Routinator</b><br/>192.0.2.1/30")]

    R_ORG ---|"eBGP 198.51.100.0/30 · adv 203.0.113.0/24"| R_ISP
    R_ISP ---|"eBGP 198.51.100.4/30 · adv 203.0.113.0/25"| R_HJ
    R_ISP ---|"eBGP 198.51.100.8/30"| R_VAL
    R_ORG ---|"eBGP peering 198.51.100.12/30"| R_VAL
    RPKI ---|"RTR TCP 3323"| R_VAL

    R_VAL --- GW
    R_VAL --- C6
    R_VAL --- CDS

    style ORIGIN fill:#c8e6c9,stroke:#2d5f3f,stroke-width:2px
    style ISP fill:#fff9c4,stroke:#a68b00,stroke-width:2px
    style HIJACK fill:#ffcdd2,stroke:#c62828,stroke-width:2px
    style VAL fill:#bbdefb,stroke:#1976d2,stroke-width:2px

## 3. AS Roles

| AS | Name | Role | BGP Relationships |
| :--- | :--- | :--- | :--- |
| AS65000 | ISP-Transit | Provider that intentionally does not apply route filtering | Upstream for AS Origin, AS Hijacker, and AS Validator |
| AS65010 | AS Origin | Legitimate owner of `203.0.113.0/24`; hosts the origin web server target | ISP customer and peer of AS Validator |
| AS65020 | AS Hijacker | Announces the unauthorized `203.0.113.0/25`; hosts the decoy target | ISP customer |
| AS65001 | AS Validator | Observing network running ROV, DNS64/NAT64, and test clients | ISP customer and peer of AS Origin |

## 4. eBGP Links

The topology uses four eBGP sessions.

| Link | Subnet | Side A | Side B | Announced Prefix |
| :--- | :--- | :--- | :--- | :--- |
| ISP ↔ Origin | `198.51.100.0/30` | ISP `.1` | Origin `.2` | Origin: `203.0.113.0/24` |
| ISP ↔ Hijacker | `198.51.100.4/30` | ISP `.5` | Hijacker `.6` | Hijacker: `203.0.113.0/25` |
| ISP ↔ Validator | `198.51.100.8/30` | ISP `.9` | Validator `.10` | Validator: `192.0.2.128/25` |
| Validator ↔ Origin (peering) | `198.51.100.12/30` | Validator `.13` | Origin `.14` | Origin: `203.0.113.0/24`; Validator: `192.0.2.128/25` |

## 5. Addressing Scheme

### 5.1 AS Origin (AS65010)

| Link | Subnet | Addresses |
| :--- | :--- | :--- |
| router-origin ↔ origin web server target | `203.0.113.0/24` | Router `.1`, origin web server target `.10` |

### 5.2 AS Hijacker (AS65020)

| Link | Subnet | Addresses |
| :--- | :--- | :--- |
| router-hijacker ↔ decoy-target | `203.0.113.0/25` | Router `.1`, decoy-target `.10` |

The `origin web server target` and `decoy-target` intentionally use the same IPv4 address, `203.0.113.10`, for the hijacking test scenario.

### 5.3 AS Validator (AS65001)

The IPv4 block used by AS Validator is `192.0.2.0/24`. This is a documentation address block and is used only in the lab environment.

| Block | Range | Purpose | Advertised? |
| :--- | :--- | :--- | :--- |
| `192.0.2.0/25` | `.0`–`.127` | Infrastructure, including the link to the RPKI service | No |
| `192.0.2.128/25` | `.128`–`.255` | Block advertised to the ISP and Origin | Yes |
| `192.0.2.128/26` | `.128`–`.191` | Dual-stack client LAN, part of the `/25` | Included in the `/25` |
| `192.0.2.192/26` | `.192`–`.255` | Tayga dynamic pool, part of the `/25` | Included in the `/25` |

| Link | Subnet | Addresses |
| :--- | :--- | :--- |
| router-validator ↔ rpki-server | `192.0.2.0/30` | rpki-server `.1`, router `.2`; RTR TCP port `3323` |
| router-validator ↔ gateway-nat64-dns64 (IPv4) | `192.168.1.0/30` | Router `.1`, gateway `.2` |
| router-validator ↔ gateway-nat64-dns64 (IPv6) | `2001:db8:6501:ff::/64` | Router `::1`, gateway `::2` (DNS64 resolver) |
| router-validator ↔ client-v6 | `2001:db8:6501:1::/64` | Router `::1`, client-v6 `::2` |
| router-validator ↔ client-dualstack (IPv4) | `192.0.2.128/26` | Router `.129`, client-dualstack `.130` |
| router-validator ↔ client-dualstack (IPv6) | `2001:db8:6501:2::/64` | Router `::1`, client-dualstack `::2` |

All IPv6 prefixes in this design are within `2001:db8:6501::/48`. There are no IPv6 BGP sessions; internal IPv6 connectivity within AS Validator uses static routing.

### 5.4 Static Routes on router-validator

| Route | Next Hop | Purpose |
| :--- | :--- | :--- |
| `2001:db8:6501:64::/96` | `2001:db8:6501:ff::2` | Directs traffic for synthesized addresses to the NAT64 gateway |
| `192.0.2.192/26` | `192.168.1.2` | Directs return traffic for the Tayga pool to the gateway |
| `192.0.2.128/25` | `blackhole` | Provides a route anchor so the `/25` can be advertised; see the implementation design document |

### 5.5 Routes on gateway-nat64-dns64

| Route | Next Hop |
| :--- | :--- |
| IPv4 default route | `192.168.1.1` |
| `2001:db8:6501::/48` | `2001:db8:6501:ff::1` |

## 6. Routing Policies

| Router | Outbound (permitted advertised prefixes) | Inbound |
| :--- | :--- | :--- |
| router-isp | All routes, without filtering (intentional for the experiment) | All routes, without filtering |
| router-origin | Only `203.0.113.0/24` | From ISP: reject `203.0.113.0/25`; common practice also filters more-specific prefixes belonging to its own address space |
| router-hijacker | Only `203.0.113.0/25` | Unrestricted, according to the lab scenario |
| router-validator | Only `192.0.2.128/25` | Reject RPKI-invalid routes; local preference for peering with Origin is higher than for the ISP provider |

## 7. Design Decisions

- **ISP-Transit without filtering:** intentionally represents an upstream provider that does not apply ingress filtering, allowing the impact of unauthorized prefix announcements to be observed.
- **Validator ↔ Origin peering:** provides an alternative path that bypasses the ISP. This model is simpler for a two-party lab; an IXP simulation can be considered as a future extension.
- **Inbound filtering on Origin:** prevents the Origin router from accepting a more-specific announcement for its own prefix from the ISP.
- **ROV only on Validator:** enables comparison of routing behavior when validation is enabled and disabled.
- **Services on a single node:** Tayga (NAT64) and Unbound (DNS64) are planned to run on one Linux host. The gateway acts as a service host, not as a router running a routing protocol.
- **Clients connected to the Validator router:** the NAT64 gateway is a separate service; the router directs traffic to the synthesized prefix and provides a path to the DNS64 resolver.
- **No switch/VLAN/NOC PC:** Containerlab uses point-to-point connections between nodes. Additional Layer 2 components are not required for the current experimental objectives.

## 8. Node Inventory

| AS / Function | Nodes |
| :--- | :--- |
| AS65000 (ISP-Transit) | `router-isp` |
| AS65010 (AS Origin) | `router-origin`, `origin web server target` |
| AS65020 (AS Hijacker) | `router-hijacker`, `decoy-target` |
| AS65001 (AS Validator) | `router-validator`, `gateway-nat64-dns64`, `client-v6`, `client-dualstack` |
| RPKI service | `rpki-server` |

**Total: 10 nodes.**
