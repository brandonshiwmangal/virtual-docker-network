# Virtual Docker Network

A small enterprise-style network lab built with Docker Compose. It runs a zone-based firewall, an OSPFv3 router, DHCP for two VLANs, an OSPFv2/PIM uplink to a Cisco IOS-XE router (SnakeT2R), and a monitoring container that prints a network status report with a PASS/FAIL summary every 60 seconds.

## Topology

```
                         mgmt_net  172.20.0.0/24
     +-----------------------+--------------------------+
     | .10                   | .2                       | .3
+----------+           +-----------+  transit_net  +----------+
| netops01 |           |   fw01    |.1----------.2 |   rt01   |
| monitor  |           | firewall  | 10.10.30.0/24 |  router  |
+----------+           | DHCP      |               |  OSPFv3  |
                       | OSPFv3    |.1----------.2 |          |
                       | OSPFv2    | ospf_backbone +----------+
                       | PIM       | 10.10.40.0/24
                       +-----------+ fd00:1::/64
                         |.1  |.1  |
              lan_net    |    |    |  t2r_trunk 10.10.224.0/29 (fw01 .2)
           10.10.10.0/24 |    |    |  VLAN 224: 192.168.1.112/29
              (VLAN 10)  |    |    |  OSPFv2 area 0 (MD5) + PIM-SM
                         |    |    | .114
       +------------+    |    |    |         .113 +------------------+
       | client-lan |----+    |    +--------------| SnakeT2R         |
       | DHCP .100+ |         |                   | Cisco IOS-XE     |
       +------------+         | dmz_net           | Gi2.224          |
                              | 10.10.20.0/24     | Lo0 192.168.1.124|
                +------------+| (VLAN 20)         | Lo239 anycast RP |
                | client-dmz |+                   | 192.168.255.255  |
                | DHCP .100+ |                    +------------------+
                +------------+                    (optional, see below)
```

### Networks

| Compose network | Docker name   | Subnet          | Purpose                                         |
|-----------------|---------------|-----------------|-------------------------------------------------|
| `mgmt_net`      | `vdn_mgmt`    | 172.20.0.0/24   | Out-of-band management                          |
| `lan_net`       | `vdn_lan`     | 10.10.10.0/24   | LAN zone (VLAN 10)                              |
| `dmz_net`       | `vdn_dmz`     | 10.10.20.0/24   | DMZ zone (VLAN 20)                              |
| `transit_net`   | `vdn_transit` | 10.10.30.0/24   | Firewall <-> rt01 transit link                  |
| `ospf_backbone` | `vdn_ospf`    | 10.10.40.0/24   | OSPFv3 area 0 (`fd00:1::/64`)                   |
| `t2r_trunk`     | `vdn_t2r`     | 10.10.224.0/29  | Trunk to SnakeT2R; VLAN 224 carries 192.168.1.112/29 |

Each Docker bridge uses the top host address as its gateway (`.254`, or `.6` on `t2r_trunk`), so the containers can take `.1`/`.2` addresses.

The `t2r_trunk` subnet only addresses the parent interfaces. The real link to SnakeT2R (`192.168.1.112/29`) runs tagged on VLAN 224 on top of it. That way the Cisco config keeps `encapsulation dot1Q 224`, and the router container's own Docker IP can't clash with the Cisco VM's address.

### Nodes

| Container    | Role                                     | Addresses |
|--------------|------------------------------------------|-----------|
| `fw01`       | Firewall, DHCP, OSPFv3, OSPFv2, PIM      | mgmt .2, LAN .1, DMZ .1, transit .1, ospf .1, trunk .2; `t2r` 192.168.1.114/29; lo `2.2.2.2`, `fd00:ffff::2`; `fd00:1::1/64` |
| `rt01`       | Router, OSPFv3                           | mgmt .3, transit .2, ospf .2; lo `1.1.1.1`, `fd00:ffff::1`; `fd00:1::2/64` |
| `netops01`   | Network operations monitor               | mgmt .10 |
| `client-lan` | Alpine DHCP client                       | DHCP from 10.10.10.100-200 |
| `client-dmz` | Alpine DHCP client                       | DHCP from 10.10.20.100-200 |
| `snaket2r`   | Cisco IOS-XE router (optional, vrnetlab) | trunk .3; Gi2.224 192.168.1.113/29; Lo0 192.168.1.124; Lo239 192.168.255.255 |

## What each component does

### fw01 - firewall (`firewall/`)
- Renames its interfaces by IP so they're readable: `mgmt`, `vlan10`, `vlan20`, `transit`, `ospf0`, `t2r-trunk`. A missing network is logged as a warning and the script carries on.
- Creates 802.1Q sub-interfaces `vlan10.10` and `vlan20.20` as SVIs (VLAN tagging demo).
- Builds `t2r`, the VLAN 224 sub-interface on `t2r-trunk`, with 192.168.1.114/29. If a Docker network ever hands out 192.168.1.114 directly, the script uses that interface untagged instead. `T2R_PARENT_IP` and `T2R_VLAN` are set in compose.
- Loads a zone-based `iptables` policy (default `DROP` on INPUT and FORWARD):

  | From -> To          | Action |
  |---------------------|--------|
  | LAN -> transit      | allow  |
  | DMZ -> transit      | allow  |
  | LAN -> DMZ          | allow  |
  | DMZ -> LAN          | drop   |
  | LAN/DMZ -> mgmt     | allow  |
  | LAN/DMZ -> t2r      | allow  |
  | SnakeT2R Lo0 -> mgmt | allow UDP 69, 123, 162, 514, 1812, 1813, 2055 (TFTP, NTP, traps, syslog, RADIUS, NetFlow) |
  | t2r <-> any, 239.0.0.0/8 | allow (routed multicast) |
  | mgmt -> any         | allow  |
  | anything else       | log as `[FW DROPPED]`, then drop |

  INPUT also allows OSPFv2 (IP proto 89), PIM (103), IGMP and ICMP from the router on `t2r`.

  LAN and DMZ traffic leaving through `transit` is NATed (masquerade). Nothing is NATed toward `t2r`, because OSPF advertises the real LAN subnets to SnakeT2R.
- Runs ISC DHCP on `vlan10` and `vlan20` (`firewall/config/dhcpd.conf`).
- Runs FRR (`firewall/config/frr/`):
  - `ospf6d`: router-id `2.2.2.2`, area 0 on `ospf0`, redistributes connected routes.
  - `ospfd`: router-id `2.2.2.2`, area 0 on `t2r` (broadcast, hellos 10/40, MD5 key 1). Every other interface is passive. Redistributes connected routes, so SnakeT2R learns the 10.10.x.x LANs as E2 routes.
  - `pimd`: PIM-SM and IGMP on `t2r`. Static anycast RP 192.168.255.255 for the local groups (239.0.11.0/24, 239.0.12.0/24, 239.192.0.0/23, 239.192.252.0/23, 239.192.248.0/26). Other groups use the RP learned from SnakeT2R's BSR.
  - Logs to `/var/log/frr/frr.log`, which the entrypoint tails into `docker logs fw01`.

### rt01 - router (`router/`)
- Interfaces: `mgmt`, `transit`, `ospf0`.
- FRR `ospf6d` with router-id `1.1.1.1`, area 0, redistributing connected routes.
- Default route `0.0.0.0/0 via 10.10.30.1` (the firewall).

### SnakeT2R - Cisco IOS-XE router (optional)
- The `snaket2r` service is commented out in `docker-compose.yml`. IOS-XE can't run as a plain container, so it needs a vrnetlab-style image that wraps the C8000v/CSR1000v VM. Fill in your image name, then check which container interface your image maps to Gi2.
- Its config is in `router/config/frr/SnakeT2R.conf`. It's IOS-XE syntax, not FRR, so it's stored there for reference only. Every value marked `<<...>>` must be filled in. The OSPF key must match `firewall/config/frr/frr.conf`.
- Gi2.224 peers with fw01 over OSPFv2 area 0 and PIM-SM. Loopback239 is the anycast RP for local multicast, and Loopback0 is the BSR and the source for management traffic.

### netops01 - monitor (`netops/`)
Mounts the Docker socket and runs `monitor.py` once `STARTUP_WAIT` has passed (15 s by default), then every `MONITOR_INTERVAL` seconds (60 s by default). One failed section doesn't stop the rest of the report. Each report covers:

1. Ping checks to fw01 and rt01 over the management network
2. OSPFv3 neighbors (rt01 and fw01), routes and the `ospf0` interface
3. fw01 <-> SnakeT2R: `t2r` interface, a ping to 192.168.1.113 from fw01, OSPFv2 neighbor, the route to Lo0 192.168.1.124, PIM neighbors and RP info
4. DHCP server status and active leases (latest state per IP, not the raw lease file)
5. Firewall INPUT, FORWARD and NAT rules with packet counters
6. SVI interfaces
7. IPv4/IPv6 routing tables for fw01 and rt01
8. Interface inventory for fw01 and rt01

Every report ends with a PASS/FAIL summary table. `--once` exits with the number of failed checks (0 means everything passed).

## Requirements

- Docker Engine with Docker Compose v2 (`docker compose`)
- A Linux host or WSL2. The firewall needs `privileged` mode and the `8021q` kernel module for the VLAN sub-interfaces, including `t2r`. Without the module, the LAN/DMZ/OSPFv3 part of the lab still works, but the SnakeT2R link doesn't. Docker Desktop's built-in VM normally includes 8021q.
- Internet access on the first build (FRR is installed from `deb.frrouting.org`)
- For SnakeT2R only: a vrnetlab-style C8000v/CSR1000v image you've built yourself. Cisco images are licensed, so the repo doesn't include one and `.gitignore` blocks them.

## Quick start

```bash
cd Virtual_Docker_Network
cp .env.example .env        # if you keep keys in .env (see Secrets)
docker compose up -d --build
```

The DHCP clients wait until fw01's healthcheck reports dhcpd as running.

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

# OSPFv2 / PIM toward SnakeT2R
docker exec fw01 ip -br addr show dev t2r
docker exec fw01 vtysh -c "show ip ospf neighbor"     # 192.168.1.124 should be Full
docker exec fw01 vtysh -c "show ip route ospf"
docker exec fw01 vtysh -c "show ip pim neighbor"
docker exec fw01 vtysh -c "show ip pim rp-info"

# Firewall
docker exec fw01 iptables -L INPUT   -n -v --line-numbers
docker exec fw01 iptables -L FORWARD -n -v --line-numbers
docker exec fw01 iptables -t nat -L POSTROUTING -n -v

# DHCP leases
docker exec fw01 cat /var/lib/dhcp/dhcpd.leases

# Client addresses and default route (should be via fw01 .1)
docker exec client-lan ip addr show eth0
docker exec client-lan ip route
docker exec client-dmz ip route

# Test the zone policy by pinging the other client, not fw01.
# (Pinging 10.10.10.1 / 10.10.20.1 hits fw01's own INPUT chain, which
#  allows ICMP on both VLANs, so it never tests the FORWARD policy.)
LAN_IP=$(docker exec client-lan ip -4 -o addr show eth0 | awk '{print $4}' | cut -d/ -f1)
DMZ_IP=$(docker exec client-dmz ip -4 -o addr show eth0 | awk '{print $4}' | cut -d/ -f1)
docker exec client-lan ping -c 2 "$DMZ_IP"    # LAN -> DMZ: should succeed
docker exec client-dmz ping -c 2 "$LAN_IP"    # DMZ -> LAN: should fail

# Run one monitoring report on demand
docker exec netops01 python3 /monitor.py --once
```

## Troubleshooting the SnakeT2R link

| Symptom | Likely cause |
|---------|--------------|
| `t2r` missing in `docker logs fw01` | 8021q module not loaded on the host, or fw01 isn't attached to `t2r_trunk` |
| No OSPFv2 neighbor at all | VLAN tag mismatch, MD5 key mismatch, or the Cisco image maps a different port to Gi2 |
| Neighbor stuck in ExStart/Exchange | MTU mismatch. Fix the MTU rather than using `ip ospf mtu-ignore` |
| Neighbor Full but no LAN reachability from SnakeT2R | Check `redistribute connected` under `router ospf` and the FORWARD rules for `t2r` |
| No PIM neighbor | `pimd=yes` missing in `daemons`, or PIM (proto 103) blocked on INPUT |

## Secrets

Keep real keys (OSPF MD5, RADIUS, SNMPv3, local passwords) out of git. `frr.conf` and the Cisco config use `<<PLACEHOLDER>>` values. Fill them in at deploy time, for example by having `entrypoint.sh` run `sed` with values from a `.env` file loaded through `env_file:`. `.gitignore` already excludes `.env`, `*.key`, `*.pem`, `secrets/` and VM images, and allows `.env.example`.

## Project layout

```
Virtual_Docker_Network/
|-- docker-compose.yml          # Networks and services (incl. t2r_trunk, healthcheck)
|-- .gitignore                  # Secrets, VM images, logs, leases, editor files
|-- .env.example                # Template for keys (copy to .env)
|-- firewall/
|   |-- Dockerfile              # Ubuntu 22.04 + iptables, ISC DHCP, FRR
|   |-- config/
|   |   |-- dhcpd.conf          # LAN and DMZ pools, t2r transit declared
|   |   `-- frr/                # daemons, frr.conf, vtysh.conf
|   `-- scripts/entrypoint.sh   # Interfaces, SVIs, t2r VLAN, iptables, DHCP, FRR
|-- router/
|   |-- Dockerfile              # Ubuntu 22.04 + FRR
|   |-- config/frr/             # daemons, frr.conf, vtysh.conf
|   |   `-- SnakeT2R.conf       # IOS-XE config for the optional Cisco router
|   `-- scripts/entrypoint.sh   # Interfaces, default route, FRR
`-- netops/
    |-- Dockerfile              # Ubuntu 22.04 + nmap, tcpdump, Python
    |-- requirements.txt        # docker>=7.1.0, tabulate
    `-- scripts/
        |-- entrypoint.sh       # Startup wait, then hands off to monitor.py
        `-- monitor.py          # Status report + PASS/FAIL summary
```

## Notes

- Docker gives each client a static address from the bridge's IPAM, with a default route via the bridge gateway (`.254`) that would bypass fw01. The client containers flush that address and route before running DHCP, so all their traffic goes through fw01.
- `vlan10.10` and `vlan20.20` are for demonstration only: LAN and DMZ traffic is untagged and flows over `vlan10`/`vlan20`. The `t2r` link is different: it really is tagged (VLAN 224), and Docker's Linux bridge passes the tagged frames through unchanged.
- `netops01` has access to the host's Docker socket, which effectively gives it root on the host. Mounting the socket `:ro` would not change that. Keep this lab off shared or production machines.
- netops01 doesn't include the Docker CLI. Run `docker` commands from the host, or add `docker.io` to `netops/Dockerfile` if you want it inside the container.
- Dropped packets are logged with the `[FW DROPPED]` prefix to the kernel log, which on Docker usually means the host's `dmesg`.
- Until SnakeT2R is running, the five SnakeT2R checks in the netops report show FAIL. That's expected.
