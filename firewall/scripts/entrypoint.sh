#!/bin/bash
set -e

log() { echo "[fw01] $(date '+%H:%M:%S') $*"; }

# ── 1. Wait for interfaces ────────────────────────────────────
log "Waiting for network interfaces..."
sleep 3

# ── 2. Rename interfaces by assigned IPv4 address ────────────
rename_by_ip() {
    local ip=$1 newname=$2
    local iface
    iface=$(ip -o addr show | awk "/ $ip\//{print \$2}" | grep -v "^lo$" | head -1)
    if [ -z "$iface" ]; then
        log "WARNING: no interface found for $ip"
        return
    fi
    if [ "$iface" = "$newname" ]; then
        log "Interface $newname already named correctly"
        return
    fi
    log "Renaming $iface -> $newname"
    ip link set "$iface" down
    ip link set "$iface" name "$newname"
    ip link set "$newname" up
}

rename_by_ip "172.20.0.2"  "mgmt"
rename_by_ip "10.10.10.1"  "vlan10"
rename_by_ip "10.10.20.1"  "vlan20"
rename_by_ip "10.10.30.1"  "transit"
rename_by_ip "10.10.40.1"  "ospf0"

# ── 3. Enable IPv6 on ospf0 and assign global address ─────────
log "Enabling IPv6 on ospf0..."
sysctl -w net.ipv6.conf.ospf0.disable_ipv6=0 2>/dev/null || true
sysctl -w net.ipv6.conf.all.forwarding=1      2>/dev/null || true
# Bounce interface to trigger link-local generation
ip link set ospf0 down && ip link set ospf0 up
sleep 1
ip -6 addr add fd00:1::1/64 dev ospf0 2>/dev/null || true
log "ospf0 IPv6: $(ip -6 addr show ospf0 | grep inet6 | awk '{print $2}')"

# ── 4. Create SVI sub-interfaces (VLAN tagging demonstration) ─
log "Creating SVI VLAN sub-interfaces..."
modprobe 8021q 2>/dev/null || true

if ! ip link show vlan10.10 &>/dev/null; then
    ip link add link vlan10 name vlan10.10 type vlan id 10
    ip link set vlan10.10 up
    log "SVI vlan10.10 (VLAN ID 10, LAN) created"
fi
if ! ip link show vlan20.20 &>/dev/null; then
    ip link add link vlan20 name vlan20.20 type vlan id 20
    ip link set vlan20.20 up
    log "SVI vlan20.20 (VLAN ID 20, DMZ) created"
fi

# ── 5. Assign loopback addresses ─────────────────────────────
ip addr add 2.2.2.2/32 dev lo 2>/dev/null || true
ip -6 addr add fd00:ffff::2/128 dev lo 2>/dev/null || true

# ── 6. iptables – Zone-based firewall ────────────────────────
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

# FORWARD
iptables -A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A FORWARD -i vlan10 -o transit -j ACCEPT
iptables -A FORWARD -i vlan20 -o transit -j ACCEPT
iptables -A FORWARD -i vlan10 -o vlan20  -j ACCEPT
iptables -A FORWARD -i vlan10 -o mgmt    -j ACCEPT
iptables -A FORWARD -i vlan20 -o mgmt    -j ACCEPT
iptables -A FORWARD -i vlan20 -o vlan10  -j DROP
iptables -A FORWARD -i mgmt   -j ACCEPT
iptables -A FORWARD -j LOG --log-prefix "[FW DROPPED] " --log-level 4

# NAT
iptables -t nat -A POSTROUTING -s 10.10.10.0/24 -o transit -j MASQUERADE
iptables -t nat -A POSTROUTING -s 10.10.20.0/24 -o transit -j MASQUERADE

log "iptables rules loaded ($(iptables -L FORWARD --line-numbers | grep -c '^[0-9]') FORWARD rules)"

# ── 7. DHCP server ────────────────────────────────────────────
log "Starting ISC DHCP server..."
mkdir -p /var/lib/dhcp && touch /var/lib/dhcp/dhcpd.leases
/usr/sbin/dhcpd -4 -cf /etc/dhcp/dhcpd.conf -pf /run/dhcpd.pid vlan10 vlan20 \
    && log "DHCP server started on vlan10 and vlan20" \
    || log "WARNING: DHCP server failed to start"

# ── 8. FRR – OSPFv3 ──────────────────────────────────────────
log "Starting FRR (OSPFv3, router-id 2.2.2.2)..."
/usr/lib/frr/frrinit.sh start
sleep 3

# Confirm IPv6 is still set after FRR zebra configures the interface
ip -6 addr show ospf0 | grep -q fd00:1::1 \
    || ip -6 addr add fd00:1::1/64 dev ospf0 2>/dev/null || true

log "FRR started. ospf0: $(ip -6 addr show ospf0 | grep 'inet6' | awk '{print $2}' | tr '\n' ' ')"

# ── 9. Status summary ─────────────────────────────────────────
log "=== fw01 interfaces ==="
ip -br addr show
log "=== fw01 routes ==="
ip route show
log "=== fw01 ready ==="

tail -f /var/log/frr/frr.log 2>/dev/null || sleep infinity
