#!/bin/bash
# fw01 entrypoint - corrected to peer with SnakeT2R over OSPFv2/PIM.
# Lines marked  # FIX:  were added or changed.
set -e

log() { echo "[fw01] $(date '+%H:%M:%S') $*"; }

# FIX: link to SnakeT2R Gi2.224. Two ways to wire it in Docker:
#   untagged - put 192.168.1.114/29 on the Docker network itself (VLAN 0)
#   tagged   - Docker network gives the parent some other IP (T2R_PARENT_IP)
#              and this script builds VLAN 224 on top (router uses dot1Q 224)
T2R_IP="192.168.1.114/29"
T2R_PARENT_IP="${T2R_PARENT_IP:-10.10.224.2}"
T2R_VLAN="${T2R_VLAN:-224}"

# ---- 1. Wait for interfaces --------------------------------------
log "Waiting for network interfaces..."
sleep 3

# ---- 2. Rename interfaces by assigned IPv4 address ---------------
rename_by_ip() {
    local ip=$1 newname=$2
    local iface
    iface=$(ip -o addr show | awk -v ip="$ip" '$4 ~ "^"ip"/" {print $2}' | grep -v "^lo$" | head -1)
    if [ -z "$iface" ]; then
        log "WARNING: no interface found for $ip"
        return 1
    fi
    if [ "$iface" = "$newname" ]; then
        log "Interface $newname already named correctly"
        return 0
    fi
    log "Renaming $iface -> $newname"
    ip link set "$iface" down
    ip link set "$iface" name "$newname"
    ip link set "$newname" up
}

# FIX: "|| true" so one missing network does not kill the script under set -e
rename_by_ip "172.20.0.2"  "mgmt"    || true
rename_by_ip "10.10.10.1"  "vlan10"  || true
rename_by_ip "10.10.20.1"  "vlan20"  || true
rename_by_ip "10.10.30.1"  "transit" || true
rename_by_ip "10.10.40.1"  "ospf0"   || true

# FIX: kernel routing settings. IPv4 forwarding was never enabled, so the
#      FORWARD rules below had nothing to act on. In Docker these may need
#      to be set in compose under "sysctls:" instead.
sysctl -w net.ipv4.ip_forward=1                 2>/dev/null || log "WARNING: set net.ipv4.ip_forward=1 in compose"
sysctl -w net.ipv4.conf.all.rp_filter=0         2>/dev/null || true
sysctl -w net.ipv4.conf.default.rp_filter=0     2>/dev/null || true

# ---- 3. Enable IPv6 on ospf0 and assign global address -----------
if ip link show ospf0 &>/dev/null; then          # FIX: guard under set -e
    log "Enabling IPv6 on ospf0..."
    sysctl -w net.ipv6.conf.ospf0.disable_ipv6=0 2>/dev/null || true
    sysctl -w net.ipv6.conf.all.forwarding=1      2>/dev/null || true
    ip link set ospf0 down && ip link set ospf0 up
    sleep 1
    ip -6 addr add fd00:1::1/64 dev ospf0 2>/dev/null || true
    log "ospf0 IPv6: $(ip -6 addr show ospf0 | grep inet6 | awk '{print $2}')"
fi

# ---- 4. Create SVI sub-interfaces (VLAN tagging demonstration) ---
log "Creating SVI VLAN sub-interfaces..."
modprobe 8021q 2>/dev/null || true

if ip link show vlan10 &>/dev/null && ! ip link show vlan10.10 &>/dev/null; then
    ip link add link vlan10 name vlan10.10 type vlan id 10
    ip link set vlan10.10 up
    log "SVI vlan10.10 (VLAN ID 10, LAN) created"
fi
if ip link show vlan20 &>/dev/null && ! ip link show vlan20.20 &>/dev/null; then
    ip link add link vlan20 name vlan20.20 type vlan id 20
    ip link set vlan20.20 up
    log "SVI vlan20.20 (VLAN ID 20, DMZ) created"
fi

# FIX: ---- 4b. Link to SnakeT2R (interface name "t2r" either way) ----
if rename_by_ip "${T2R_IP%/*}" "t2r" 2>/dev/null; then
    log "t2r: untagged link, address came from Docker"
elif rename_by_ip "$T2R_PARENT_IP" "t2r-trunk"; then
    if ! ip link show t2r &>/dev/null; then
        ip link add link t2r-trunk name t2r type vlan id "$T2R_VLAN"
    fi
    ip link set t2r mtu 1500 up                  # must match router MTU for OSPF
    ip addr add "$T2R_IP" dev t2r 2>/dev/null || true
    log "t2r: VLAN $T2R_VLAN on t2r-trunk, $T2R_IP"
else
    log "WARNING: no link to SnakeT2R found - OSPFv2/PIM to the router will be down"
fi
sysctl -w net.ipv4.conf.t2r.rp_filter=0 2>/dev/null || true

# ---- 5. Assign loopback addresses -------------------------------
ip addr add 2.2.2.2/32 dev lo 2>/dev/null || true
ip -6 addr add fd00:ffff::2/128 dev lo 2>/dev/null || true

# ---- 6. iptables - Zone-based firewall --------------------------
log "Loading iptables rules..."
iptables -F; iptables -X; iptables -t nat -F; iptables -t mangle -F
iptables -P INPUT DROP; iptables -P FORWARD DROP; iptables -P OUTPUT ACCEPT

# INPUT
iptables -A INPUT -i lo -j ACCEPT
iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A INPUT -i mgmt   -p icmp -j ACCEPT
iptables -A INPUT -i vlan10 -p icmp -j ACCEPT
iptables -A INPUT -i vlan20 -p icmp -j ACCEPT
iptables -A INPUT -i vlan10 -p udp --dport 67 -j ACCEPT
iptables -A INPUT -i vlan20 -p udp --dport 67 -j ACCEPT
iptables -A INPUT -i mgmt   -j ACCEPT
# FIX: control plane from the router - OSPFv2 (89), PIM (103), IGMP (2), ICMP
iptables -A INPUT -i t2r -s 192.168.1.112/29 -p 89  -j ACCEPT
iptables -A INPUT -i t2r -d 224.0.0.5        -p 89  -j ACCEPT
iptables -A INPUT -i t2r -d 224.0.0.6        -p 89  -j ACCEPT
iptables -A INPUT -i t2r -p 103 -j ACCEPT
iptables -A INPUT -i t2r -p igmp -j ACCEPT
iptables -A INPUT -i t2r -p icmp -j ACCEPT

# FORWARD
iptables -A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A FORWARD -i vlan10 -o transit -j ACCEPT
iptables -A FORWARD -i vlan20 -o transit -j ACCEPT
iptables -A FORWARD -i vlan10 -o vlan20  -j ACCEPT
iptables -A FORWARD -i vlan10 -o mgmt    -j ACCEPT
iptables -A FORWARD -i vlan20 -o mgmt    -j ACCEPT
iptables -A FORWARD -i vlan20 -o vlan10  -j DROP
iptables -A FORWARD -i mgmt   -j ACCEPT
# FIX: LAN/DMZ out to the router; replies come back via conntrack
iptables -A FORWARD -i vlan10 -o t2r -j ACCEPT
iptables -A FORWARD -i vlan20 -o t2r -j ACCEPT
# FIX: router Loopback0 -> mgmt services (RADIUS, NTP, TFTP, syslog, traps, NetFlow)
iptables -A FORWARD -i t2r -o mgmt -s 192.168.1.124 -p udp \
    -m multiport --dports 69,123,162,514,1812,1813,2055 -j ACCEPT
# FIX: routed multicast in both directions (PIM does the RPF/OIL work)
iptables -A FORWARD -i t2r -d 239.0.0.0/8 -j ACCEPT
iptables -A FORWARD -o t2r -d 239.0.0.0/8 -j ACCEPT
iptables -A FORWARD -j LOG --log-prefix "[FW DROPPED] " --log-level 4

# NAT (unchanged - nothing is NATed toward t2r; OSPF advertises the real LANs)
iptables -t nat -A POSTROUTING -s 10.10.10.0/24 -o transit -j MASQUERADE
iptables -t nat -A POSTROUTING -s 10.10.20.0/24 -o transit -j MASQUERADE

log "iptables rules loaded ($(iptables -L FORWARD --line-numbers | grep -c '^[0-9]') FORWARD rules)"

# ---- 7. DHCP server ---------------------------------------------
log "Starting ISC DHCP server..."
mkdir -p /var/lib/dhcp && touch /var/lib/dhcp/dhcpd.leases
/usr/sbin/dhcpd -4 -cf /etc/dhcp/dhcpd.conf -pf /run/dhcpd.pid vlan10 vlan20 \
    && log "DHCP server started on vlan10 and vlan20" \
    || log "WARNING: DHCP server failed to start"

# ---- 8. FRR - OSPFv3 + OSPFv2 + PIM -----------------------------
log "Starting FRR (OSPFv3, OSPFv2, PIM; router-id 2.2.2.2)..."
mkdir -p /var/log/frr && chown frr:frr /var/log/frr 2>/dev/null || true   # FIX: log file target
/usr/lib/frr/frrinit.sh start
sleep 3

# Confirm IPv6 is still set after FRR zebra configures the interface
ip -6 addr show ospf0 2>/dev/null | grep -q fd00:1::1 \
    || ip -6 addr add fd00:1::1/64 dev ospf0 2>/dev/null || true

log "FRR started. ospf0: $(ip -6 addr show ospf0 2>/dev/null | grep 'inet6' | awk '{print $2}' | tr '\n' ' ')"

# ---- 9. Status summary ------------------------------------------
log "=== fw01 interfaces ==="
ip -br addr show
log "=== fw01 routes ==="
ip route show
log "=== fw01 ready ==="

tail -F /var/log/frr/frr.log 2>/dev/null || sleep infinity
