# Source this file:  . scripts/helpers.sh
# Same helpers as experiments.md ("Setup before you start").
LAB=${LAB:-bgp-rov-lab}
vt()  { docker exec "clab-$LAB-$1" vtysh -c "$2"; }
run() { docker exec "clab-$LAB-$1" sh -c "$2"; }
