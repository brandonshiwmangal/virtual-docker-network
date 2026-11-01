# Virtual Docker Network

A small enterprise-style network lab built entirely with Docker Compose. It runs a zone-based firewall, an OSPFv3 router, DHCP for two VLANs, and a monitoring container that prints a network status report every 60 seconds.

## Topology

```
                      mgmt_net  172.20.0.0/24
     ┌───────────────────────┬──────────────────────────┐
     │ .10                   │ .2                       │ .3
┌──────────┐           ┌───────────┐  transit_net  ┌──────────┐
│ netops01 │           │   fw01    │.1──────────.2 │   rt01   │
│ monitor  │           │ firewall  │ 10.10.30.0/24 │  router  │
└──────────┘           │ DHCP      │               │  OSPFv3  │
                       │ OSPFv3    │.1──────────.2 │          │
                       └───────────┘ ospf_backbone └──────────┘
                         │.1   │.1   10.10.40.0/24
                         │     │     fd00:1::/64
              lan_net    │     │    dmz_net
           10.10.10.0/24 │     │ 10.10.20.0/24
              (VLAN 10)  │     │  (VLAN 20)
              ┌────────────┐ ┌────────────┐
              │ client-lan │ │ client-dmz │
              │ DHCP .100+ │ │ DHCP .100+ │
              └────────────┘ └────────────┘
```

### Networks

| Compose network | Docker name   | Subnet          | Purpose                          |
|-----------------|---------------|-----------------|----------------------------------|
| `mgmt_net`      | `vdn_mgmt`    | 172.20.0.0/24   | Out-of-band management           |
| `lan_net`       | `vdn_lan`     | 10.10.10.0/24   | LAN zone (VLAN 10)               |
| `dmz_net`       | `vdn_dmz`     | 10.10.20.0/24   | DMZ zone (VLAN 20)               |
| `transit_net`   | `vdn_transit` | 10.10.30.0/24   | Firewall ↔ router transit link   |
| `ospf_backbone` | `vdn_ospf`    | 10.10.40.0/24   | OSPFv3 area 0 (`fd00:1::/64`)    |

Each Docker bridge uses `.254` as its gateway, so the containers can take `.1`/`.2` addresses.

### Nodes

| Container    | Role                         | Addresses                                                                 |
|--------------|------------------------------|---------------------------------------------------------------------------|
| `fw01`       | Firewall, DHCP server, OSPFv3 | mgmt .2, LAN .1, DMZ .1, transit .1, ospf .1 · lo `2.2.2.2`, `fd00:ffff::2` · `fd00:1::1/64` |
| `rt01`       | Router, OSPFv3               | mgmt .3, transit .2, ospf .2 · lo `1.1.1.1`, `fd00:ffff::1` · `fd00:1::2/64` |
| `netops01`   | Network operations monitor   | mgmt .10                                                                  |
| `client-lan` | Alpine DHCP client           | DHCP from 10.10.10.100–200                                                |
| `client-dmz` | Alpine DHCP client           | DHCP from 10.10.20.100–200                                                |

## What each component does

### fw01 — firewall (`firewall/`)
- Renames its interfaces by IP so they're readable: `mgmt`, `vlan10`, `vlan20`, `transit`, `ospf0`.
- Creates 802.1Q sub-interfaces `vlan10.10` and `vlan20.20` as SVIs (VLAN tagging demo).
- Loads a zone-based `iptables` policy (default `DROP` on INPUT and FORWARD):

  | From → To     | Action |
  |---------------|--------|
  | LAN → transit | allow  |
  | DMZ → transit | allow  |
  | LAN → DMZ     | allow  |
  | DMZ → LAN     | drop   |
  | LAN/DMZ → mgmt| allow  |
  | mgmt → any    | allow  |
  | anything else | log as `[FW DROPPED]`, then drop |

  LAN and DMZ traffic leaving through `transit` is NATed (masquerade).
- Runs ISC DHCP on `vlan10` and `vlan20` (`firewall/config/dhcpd.conf`).
- Runs FRR `ospf6d` with router-id `2.2.2.2` in area 0, redistributing connected routes.

### rt01 — router (`router/`)
- Interfaces: `mgmt`, `transit`, `ospf0`.
- FRR `ospf6d` with router-id `1.1.1.1`, area 0, redistributing connected routes.
- Default route `0.0.0.0/0 via 10.10.30.1` (the firewall).

### netops01 — monitor (`netops/`)
Mounts the Docker socket and runs `monitor.py` 15 seconds after startup, then every 60 seconds. Each report covers:

1. Ping checks to fw01 and rt01 over the management network
2. OSPFv3 neighbors, routes and the `ospf0` interface (via `vtysh` on rt01 and fw01)
3. DHCP server status and active leases
4. Firewall FORWARD and NAT rules
5. SVI interfaces
6. IPv4/IPv6 routing tables
7. Interface inventory for fw01 and rt01

## Requirements

- Docker Engine with Docker Compose v2 (`docker compose`)
- A Linux host or WSL2 (the firewall needs `privileged` mode and the `8021q` kernel module for the VLAN sub-interfaces; if the module isn't available the rest of the lab still works)
- Internet access on the first build (FRR is installed from `deb.frrouting.org`)

## Quick start

```bash
cd Virtual_Docker_Network
docker compose up -d --build
```

Watch the monitoring report:

```bash
docker logs -f netops01
```

Stop and remove everything:

```bash
docker compose down
```

## Useful commands

```bash
# OSPFv3 state
docker exec rt01 vtysh -c "show ipv6 ospf6 neighbor"
docker exec rt01 vtysh -c "show ipv6 ospf6 route"
docker exec fw01 vtysh -c "show running-config"

# Firewall
docker exec fw01 iptables -L FORWARD -n -v --line-numbers
docker exec fw01 iptables -t nat -L POSTROUTING -n -v

# DHCP leases
docker exec fw01 cat /var/lib/dhcp/dhcpd.leases

# Client addresses
docker exec client-lan ip addr show eth0
docker exec client-dmz ip addr show eth0

# Test the zone policy (LAN → DMZ allowed, DMZ → LAN dropped)
docker exec client-lan ping -c 2 10.10.20.1
docker exec client-dmz ping -c 2 10.10.10.1

# Run one monitoring report on demand
docker exec netops01 python3 /monitor.py --once
```

## Project layout

```
Virtual_Docker_Network/
├── docker-compose.yml          # Networks and services
├── firewall/
│   ├── Dockerfile              # Ubuntu 22.04 + iptables, ISC DHCP, FRR
│   ├── config/
│   │   ├── dhcpd.conf          # LAN and DMZ pools
│   │   └── frr/                # daemons, frr.conf, vtysh.conf
│   └── scripts/entrypoint.sh   # Interfaces, SVIs, iptables, DHCP, FRR
├── router/
│   ├── Dockerfile              # Ubuntu 22.04 + FRR
│   ├── config/frr/             # daemons, frr.conf, vtysh.conf
│   └── scripts/entrypoint.sh   # Interfaces, default route, FRR
└── netops/
    ├── Dockerfile              # Ubuntu 22.04 + nmap, tcpdump, docker CLI, Python
    ├── requirements.txt        # docker, tabulate
    └── scripts/
        ├── entrypoint.sh       # Monitoring loop
        └── monitor.py          # Status report
```

## Notes

- Docker also assigns each client a static address from the bridge's IPAM, so a client may end up with both that address and a DHCP lease. Check `dhcpd.leases` on fw01 to confirm DHCP worked.
- The VLAN sub-interfaces are for demonstration: Docker bridges carry untagged frames, so real traffic flows over `vlan10`/`vlan20`, not the `.10`/`.20` sub-interfaces.
- `netops01` has access to the host's Docker socket, which effectively gives it root on the host. Keep this lab off shared or production machines.
- Dropped packets are logged with the `[FW DROPPED]` prefix to the kernel log, which on Docker usually means the host's `dmesg`.
