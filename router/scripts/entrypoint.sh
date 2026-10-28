#!/bin/bash
set -e

log() { echo "[rt01] $(date '+%H:%M:%S') $*"; }

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

rename_by_ip "172.20.0.3"  "mgmt"
rename_by_ip "10.10.30.2"  "transit"
rename_by_ip "10.10.40.2"  "ospf0"

# ── 3. Enable IPv6 on ospf0 and assign global address ─────────
log "Enabling IPv6 on ospf0..."
sysctl -w net.ipv6.conf.ospf0.disable_ipv6=0 2>/dev/null || true
sysctl -w net.ipv6.conf.all.forwarding=1      2>/dev/null || true
ip link set ospf0 down && ip link set ospf0 up
sleep 1
ip -6 addr add fd00:1::2/64 dev ospf0 2>/dev/null || true
log "ospf0 IPv6: $(ip -6 addr show ospf0 | grep inet6 | awk '{print $2}')"

# ── 4. Loopback addresses ─────────────────────────────────────
ip addr add 1.1.1.1/32 dev lo 2>/dev/null || true
ip -6 addr add fd00:ffff::1/128 dev lo 2>/dev/null || true

# ── 5. Default route via firewall ────────────────────────────
ip route replace default via 10.10.30.1 dev transit 2>/dev/null || true
log "Default route: 0.0.0.0/0 via 10.10.30.1 (transit)"

# ── 6. FRR – OSPFv3 ──────────────────────────────────────────
log "Starting FRR (OSPFv3, router-id 1.1.1.1)..."
/usr/lib/frr/frrinit.sh start
sleep 3

ip -6 addr show ospf0 | grep -q fd00:1::2 \
    || ip -6 addr add fd00:1::2/64 dev ospf0 2>/dev/null || true

log "FRR started. ospf0: $(ip -6 addr show ospf0 | grep 'inet6' | awk '{print $2}' | tr '\n' ' ')"
log "Waiting for OSPFv3 neighbor (fw01 at fd00:1::1)..."

# ── 7. Status summary ─────────────────────────────────────────
log "=== rt01 interfaces ==="
ip -br addr show
log "=== rt01 routes ==="
ip route show
log "=== rt01 ready ==="

tail -f /var/log/frr/frr.log 2>/dev/null || sleep infinity
