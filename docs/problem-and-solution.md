# The Internet Is Sophisticated—So Why Can It Still Take a Wrong Turn?

> **Aligned with SDG 9: Industry, Innovation and Infrastructure**
>
> **Two networking challenges: when routing sends traffic in the wrong direction, and when IPv6 clients need to access services that still use IPv4.**

The internet often feels simple: open a browser, type an address, and a page appears. We rarely think about the journey data packets take behind the scenes. Yet for a request to reach its destination server, many different networks must connect and determine where traffic should be forwarded.

This cooperation does not always run smoothly. A network may announce an incorrect route, while the limited supply of IPv4 addresses encourages more networks to adopt IPv6. But the transition is not happening everywhere at once. As a result, clients already using IPv6 may still need to access services that support only IPv4.

This project explores these two challenges through network simulations. It focuses on designing scenarios that help explain the problems and evaluate potential solutions: **RPKI Route Origin Validation (ROV)** to help validate route announcements, and **DNS64/NAT64** to bridge communication between IPv6-only clients and IPv4-only services.

## 1. When the Internet Follows the Wrong Directions: BGP Hijacking

### Why Route Announcements Need to Be Checked

The internet consists of many independent networks called **Autonomous Systems (ASes)**. To reach one another, these networks exchange information about available paths using the **Border Gateway Protocol (BGP)**.

BGP allows networks to announce IP prefixes that can be reached through them. However, a route announcement does not automatically prove that the announcing network is authorized to originate that prefix. Without additional checks and policies, incorrect routing information may be accepted and influence traffic paths.

Imagine someone claiming to know the fastest route to an address. If that guidance is trusted without being checked, people may end up in the wrong place. In internet routing, the result can be traffic passing through an unintended network or failing to reach its intended destination.

Two terms related to this problem are:

- **BGP hijacking:** when an AS announces a prefix it is not authorized to originate, potentially redirecting traffic to the wrong path or network.
- **Route leak:** when routing information is propagated more broadly, or in a way it should not be, to networks that were not meant to receive it.

These are not the same thing, but both show why route announcements and routing policies matter. Such incidents are not always caused by deliberate attacks; configuration mistakes can cause similar disruptions.

### A Real-World Routing Problem

![BGP hijacking(./assets/ipv4-routing-table.png)

In its analysis of a **route leak on May 1, 2025**, APNIC reported that AS22773 propagated approximately **4,651 additional routes over about three hours**. Of those, **4,644 routes, or around 99.85%**, would be classified as _RPKI-invalid_ by routers performing validation. This suggests that RPKI validation can help networks with rejection policies filter many invalid routes. The incident is presented here as an example of a routing problem—not as evidence that route leaks and BGP hijacking are the same event.

![ROV adoption(./assets/ROAs-published.png)

APNIC Labs data dated **October 30, 2025** also showed progress in the publication of **Route Origin Authorizations (ROAs)** in Indonesia. Coverage increased from 66.03% to 90.55% over three months, approaching the Southeast Asian average of around 90.68%. However, adoption of **Route Origin Validation (ROV)**—the process through which networks check validation results and apply policies to invalid routes—was still around 21.72%.

In other words, publishing ROAs is not enough on its own. Networks also need to perform validation and apply appropriate routing policies to handle announcements classified as invalid.

ROV is not a solution to every type of routing disruption. It checks prefix-origin validity using RPKI data, but it does not, by itself, guarantee that an entire AS path is safe or problem-free. This limitation matters when evaluating the benefits and scope of ROV.

### The Question This Project Addresses

**How can a multi-AS simulation demonstrate the impact of BGP hijacking when routes are propagated without filtering, and how can RPKI ROV help reject prefix announcements classified as invalid?**

### Proposed Solution: Simulating BGP Hijacking with RPKI ROV

To explore this problem, the project designs a network simulation using **FRRouting and Containerlab**. The topology consists of four ASes with distinct roles:

- **ISP-Transit:** forwards route announcements without filtering so the propagation of a hijacked route can be observed.
- **Origin AS:** the network that legitimately announces the destination prefix and has a matching ROA.
- **Hijacker AS:** the network that announces a prefix it is not authorized to originate.
- **Validator AS:** the network running ROV, connected to ISP-Transit, and directly peered with the Origin AS.

RPKI validation uses **Routinator** with local ROAs. Routers in the Validator AS receive validation information through the **RTR protocol**.

The design compares two conditions:

1. **ROV disabled:** a hijacked route announcement may be accepted if no other policy rejects it, potentially directing traffic to the wrong destination.
2. **ROV enabled with a `reject-invalid` policy:** announcements classified as RPKI-invalid are rejected, allowing the router to consider available valid routes.

For this comparison to be meaningful, the ROA configuration must determine the validation status of the hijacked route. A more-specific prefix is not automatically invalid; the result depends on the prefix, origin AS, and the maximum prefix length permitted by the ROA.

With this design, the project focuses on how problematic route announcements can affect routing and how origin validation may help limit their impact. Configuration details and test results are covered in a separate experiment document.

---

## 2. IPv6 Is Ready, but Some Services Still Use IPv4

### A Transition That Is Not Happening Everywhere at Once

![IPv4 prefix report(./assets/ipv4-prefixes-originated.png)

IPv4 has a limited address space, and the global pool of IPv4 addresses available for new allocations was exhausted years ago. As a result, IPv6 has become an important part of internet infrastructure development. However, IPv6 adoption has not happened simultaneously across all networks and services. Some servers are still accessible only over IPv4.

It is like a device that speaks a new language trying to communicate with a service that still speaks the old one. Both may work perfectly well, but they cannot communicate directly without a mechanism to bridge the gap.

IPv4 allocation constraints make this transition even more relevant. Under APNIC policy, IPv4 allocations to new members are limited, including a maximum **/23 block per delegation** as described in the policy context. Operators that need additional addresses may have to find alternatives, such as obtaining addresses from other members.

Proposal **prop-165** proposed allowing IPv6-only networks to receive one IPv4 **/24** block for transition purposes, including access to legacy IPv4-only services. According to APNIC's report on APNIC 62 in September 2026, the proposal did not reach consensus. Regardless of how allocation policy evolves, the technical challenge of connecting IPv6-only clients to IPv4-only services remains.

### The Role of DNS64 and NAT64

Two mechanisms can help enable communication across IP versions:

- **DNS64:** synthesizes DNS responses containing IPv6 addresses based on the IPv4 address information of destinations that only have IPv4.
- **NAT64:** translates traffic between IPv6-only clients and IPv4-only servers through a gateway that supports this translation.

In a suitable setup, DNS64 helps an IPv6 client obtain a destination address it can use over IPv6, while NAT64 translates traffic so communication can reach an IPv4 server. They work together: DNS64 helps the client discover a usable destination address, while NAT64 handles traffic translation.

### The Question This Project Addresses

**How can DNS64 and NAT64 be designed so that IPv6-only clients can access IPv4-only services as the availability of new IPv4 addresses becomes increasingly limited?**

### Proposed Solution: A NAT64/DNS64 Gateway

The project designs a gateway node running two components:

- **Tayga** for NAT64.
- **Unbound** as the DNS resolver providing DNS64 functionality.

In this design, an IPv6-only client accesses a target server that supports only IPv4 through the gateway. A dual-stack client is also included as a control to help compare connection behavior.

The planned tests distinguish three conditions:

1. Both DNS64 and NAT64 are disabled.
2. DNS64 is enabled, but NAT64 is disabled.
3. Both DNS64 and NAT64 are enabled.

This comparison is intended to clarify the role of each mechanism and the conditions required for an IPv6-only client to reach an IPv4-only service. Configuration details and test results are documented separately.

### Bringing the Two Challenges Together

The two experiments are also designed to be connected within a single network scenario. An IPv6-only client can access an IPv4 server through the NAT64 gateway, while the route toward the destination network is influenced by BGP announcements.

By connecting these components, the project can explore two layers of networking challenges: how traffic is translated between IPv6 and IPv4, and how traffic may be affected when a network receives problematic routing information. This integration is part of the experiment design; its results must be assessed through separate testing.

---

## 3. Project Scope and Contribution

This project has two main areas of focus:

1. **Routing security:** designing a multi-AS simulation to explore the impact of BGP hijacking and the role of RPKI ROV in rejecting route announcements classified as invalid.
2. **IPv4–IPv6 interoperability:** designing a DNS64/NAT64 gateway so IPv6-only clients can access services that support only IPv4.

Both areas relate to **SDG 9: Industry, Innovation and Infrastructure**, particularly efforts to understand and evaluate more resilient network infrastructure that can support evolving connectivity needs.

Rather than changing configurations on the live internet, this project uses simulations so scenarios can be designed and observed in a controlled environment. This problem-and-solution overview provides the foundation for separate documentation covering configuration, testing procedures, results, and evaluation.

## 4. References

The following references provide context for the problems and the choice of proposed solutions. Figures and policy status reflect the dates specified in the respective sources.

- [APNIC — Analysis of a route leak](https://blog.apnic.net/2025/05/06/analysis-of-a-route-leak/)
- [Anapaya — Border Gateway Protocol hijacking: examples and solutions](https://www.anapaya.net/blog/border-gateway-protocol-hijacking-examples-and-solutions)
- [APNIC — Securing Indonesia’s Internet: ROA gains and the road to robust routing](https://blog.apnic.net/2025/11/27/securing-indonesias-internet-roa-gains-and-the-road-to-robust-routing)
- [MikroTik Forum — Please support NAT64/DNS64 as soon as possible](https://forum.mikrotik.com/t/please-support-nat64-dns64-as-soon-as-possible/264846)
- [APNIC — APNIC 62 policy SIG reviews seven policy proposals, with two reaching consensus](https://blog.apnic.net/2026/09/25/apnic-62-policy-sig-reviews-seven-policy-proposals-with-two-reaching-consensus/)
- [Hurricane Electric — Prefix report](https://bgp.he.net/report/prefixes%20)
