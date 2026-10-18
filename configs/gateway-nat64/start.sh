#!/bin/sh
# Starts NAT64 (Tayga) and DNS64 (NSD + Unbound) on gateway-nat64.
# E4 toggles: stop only the tayga process (keep the nat64 interface up), or edit module-config in unbound.conf and run: unbound-control reload
set -e
CONF=/etc/lab
mkdir -p /var/spool/tayga

attempts=0
while ! ip -6 addr show dev eth1 | grep -q '2001:db8:6501:ff::2/64'; do
    attempts=$((attempts + 1))
    if [ "$attempts" -ge 30 ]; then
        echo "gateway link address 2001:db8:6501:ff::2/64 is not ready" >&2
        exit 1
    fi
    sleep 1
done
sleep 2

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
