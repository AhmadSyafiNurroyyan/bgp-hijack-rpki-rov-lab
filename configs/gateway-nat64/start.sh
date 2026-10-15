#!/bin/sh
# Starts NAT64 (Tayga) and DNS64 (NSD + Unbound) on gateway-nat64.
# E4 toggles: stop only the tayga process (keep the nat64 interface up), or edit module-config in unbound.conf and run: unbound-control reload
set -e
CONF=/etc/lab
mkdir -p /var/spool/tayga

# --- NAT64 -------------------------------------------------------------
tayga -c $CONF/tayga.conf --mktun
ip link set nat64 up
ip addr add 192.168.255.1 dev nat64
ip route add 64:ff9b::/96   dev nat64
ip route add 192.0.2.192/26 dev nat64
tayga -c $CONF/tayga.conf          # daemonizes by default

# --- DNS64 -------------------------------------------------------------
nsd -c $CONF/nsd/nsd.conf          # authoritative lab.test on 127.0.0.1:5353
unbound -c $CONF/unbound.conf      # DNS64 resolver on [2001:db8:6501:ff::2]:53
