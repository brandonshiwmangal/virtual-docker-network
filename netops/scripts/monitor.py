#!/usr/bin/env python3
"""
Network Operations Manager – monitor.py
Checks: connectivity, OSPFv3 neighbors/routes, DHCP leases, firewall rules, SVIs.
"""
import argparse
import subprocess
import sys
from datetime import datetime

try:
    import docker
    from tabulate import tabulate
    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False

# ── Topology knowledge ────────────────────────────────────────
MGMT_HOSTS = {
    "fw01 (Firewall)": "172.20.0.2",
    "rt01 (Router)":   "172.20.0.3",
}

CONTAINER_NAMES = {
    "fw01": "fw01",
    "rt01": "rt01",
}

SEP  = "=" * 65
SEP2 = "-" * 65


def hdr(title):
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


def run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "TIMEOUT", 1
    except Exception as e:
        return "", str(e), 1


def docker_exec(client, container_name, cmd):
    """Run a shell command inside a container and return stdout."""
    try:
        c = client.containers.get(container_name)
        result = c.exec_run(cmd, demux=True)
        stdout = result.output[0] or b""
        return stdout.decode("utf-8", errors="replace").strip()
    except Exception as e:
        return f"[ERROR: {e}]"


# ── Section 1: Connectivity ───────────────────────────────────
def check_connectivity():
    hdr("1. CONNECTIVITY CHECK  (ping from netops01 / 172.20.0.10)")
    rows = []
    for name, ip in MGMT_HOSTS.items():
        out, _, rc = run(["ping", "-c", "3", "-W", "2", ip])
        if rc == 0:
            # Extract avg rtt
            for line in out.splitlines():
                if "rtt" in line or "round-trip" in line:
                    rtt = line.split("/")[4] if "/" in line else "?"
                    rows.append([name, ip, "✓  UP", f"avg {rtt} ms"])
                    break
            else:
                rows.append([name, ip, "✓  UP", ""])
        else:
            rows.append([name, ip, "✗  DOWN", ""])
    print(tabulate(rows, headers=["Host", "IP", "Status", "RTT"], tablefmt="simple"))


# ── Section 2: OSPFv3 ─────────────────────────────────────────
def check_ospf(client):
    hdr("2. OSPFv3  (queried from rt01 via Docker exec)")

    # Neighbors
    print("\n  [Neighbors]")
    neighbors = docker_exec(client, "rt01", "vtysh -c 'show ipv6 ospf6 neighbor'")
    print(neighbors if neighbors else "  (no output)")

    # Routes
    print("\n  [OSPFv3 Routes]")
    routes = docker_exec(client, "rt01", "vtysh -c 'show ipv6 ospf6 route'")
    print(routes if routes else "  (no output)")

    # Interface status
    print("\n  [Interface ospf0 on rt01]")
    iface = docker_exec(client, "rt01", "vtysh -c 'show ipv6 ospf6 interface ospf0'")
    print(iface if iface else "  (no output)")

    # Same from fw01
    print("\n  [Neighbors on fw01]")
    fw_neighbors = docker_exec(client, "fw01", "vtysh -c 'show ipv6 ospf6 neighbor'")
    print(fw_neighbors if fw_neighbors else "  (no output)")


# ── Section 3: DHCP ───────────────────────────────────────────
def check_dhcp(client):
    hdr("3. DHCP SERVER  (fw01)")

    # Process status
    proc = docker_exec(client, "fw01", "bash -c 'pgrep dhcpd && echo RUNNING || echo STOPPED'")
    print(f"  dhcpd process : {proc}")

    # Active leases
    leases = docker_exec(client, "fw01", "cat /var/lib/dhcp/dhcpd.leases")
    if leases and "lease" in leases:
        print("\n  Active leases:")
        print(leases)
    else:
        print("  Active leases : none (pools ready: 10.10.10.100-200, 10.10.20.100-200)")

    # Pool summary
    print("\n  Configured pools:")
    print("    VLAN10 / LAN : 10.10.10.100 – 10.10.10.200  (GW 10.10.10.1)")
    print("    VLAN20 / DMZ : 10.10.20.100 – 10.10.20.200  (GW 10.10.20.1)")


# ── Section 4: Firewall rules ─────────────────────────────────
def check_firewall(client):
    hdr("4. FIREWALL RULES  (fw01 iptables)")

    rules = docker_exec(client, "fw01", "iptables -L FORWARD -n --line-numbers")
    print(rules if rules else "  (no output)")

    print(f"\n  {SEP2}")
    nat = docker_exec(client, "fw01", "iptables -t nat -L POSTROUTING -n")
    print(nat if nat else "  (no output)")


# ── Section 5: SVIs ───────────────────────────────────────────
def check_svis(client):
    hdr("5. SWITCHED VIRTUAL INTERFACES  (fw01)")

    ifaces = docker_exec(client, "fw01", "ip addr show")
    svi_found = []
    for line in ifaces.splitlines():
        if any(x in line for x in ("vlan10", "vlan20", "vlan10.10", "vlan20.20")):
            svi_found.append(line.strip())

    if svi_found:
        rows = []
        for l in svi_found:
            rows.append([l])
        print("  Active SVI interfaces:")
        for r in svi_found:
            print(f"    {r}")
    else:
        print("  (no SVI interfaces detected)")

    print("\n  SVI mapping:")
    print("    vlan10 / vlan10.10  ->  VLAN 10 (LAN)  10.10.10.1/24")
    print("    vlan20 / vlan20.20  ->  VLAN 20 (DMZ)  10.10.20.1/24")


# ── Section 6: Routing tables ─────────────────────────────────
def check_routes(client):
    hdr("6. ROUTING TABLES")

    print("  [fw01 IPv4 routes]")
    fw_routes = docker_exec(client, "fw01", "ip route show")
    print(fw_routes if fw_routes else "  (no output)")

    print("\n  [rt01 IPv4 routes]")
    rt_routes = docker_exec(client, "rt01", "ip route show")
    print(rt_routes if rt_routes else "  (no output)")

    print("\n  [rt01 IPv6 routes]")
    rt6_routes = docker_exec(client, "rt01", "ip -6 route show")
    print(rt6_routes if rt6_routes else "  (no output)")


# ── Section 7: Interface inventory ───────────────────────────
def interface_inventory(client):
    hdr("7. INTERFACE INVENTORY")
    for cname in ("fw01", "rt01"):
        print(f"\n  [{cname}]")
        out = docker_exec(client, cname, "ip -br addr show")
        for line in out.splitlines():
            print(f"    {line}")


# ── Main ──────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    print(f"\n{'#'*65}")
    print(f"#  NETWORK OPERATIONS CENTER – STATUS REPORT")
    print(f"#  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  netops01 (172.20.0.10)")
    print(f"{'#'*65}")

    if not DOCKER_AVAILABLE:
        print("ERROR: 'docker' or 'tabulate' Python package not available.")
        sys.exit(1)

    try:
        client = docker.from_env()
    except Exception as e:
        print(f"ERROR: Cannot connect to Docker daemon: {e}")
        print("Falling back to connectivity-only check.")
        check_connectivity()
        return

    check_connectivity()
    check_ospf(client)
    check_dhcp(client)
    check_firewall(client)
    check_svis(client)
    check_routes(client)
    interface_inventory(client)

    print(f"\n{SEP}")
    print("  END OF REPORT")
    print(SEP)


if __name__ == "__main__":
    main()
