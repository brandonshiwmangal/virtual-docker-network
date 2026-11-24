#!/usr/bin/env python3
"""
Network Operations Manager - monitor.py
Checks: connectivity, OSPFv3 + OSPFv2 neighbors/routes, PIM, DHCP leases,
firewall rules, SVIs, the fw01 <-> SnakeT2R link, routing tables.

Usage:
    monitor.py --once            one report, then exit
    monitor.py --interval 60     report every 60 s (default mode)
"""
import argparse
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

# FIX: import separately - tabulate missing used to hide a working docker SDK,
#      and the connectivity fallback needed tabulate anyway.
try:
    import docker
except ImportError:
    docker = None

try:
    from tabulate import tabulate
except ImportError:
    def tabulate(rows, headers=(), tablefmt=None):
        lines = ["  ".join(str(h) for h in headers)] if headers else []
        lines += ["  ".join(str(c) for c in r) for r in rows]
        return "\n".join(lines)

# ---- Topology knowledge ------------------------------------------
MGMT_HOSTS = {
    "fw01 (Firewall)": "172.20.0.2",
    "rt01 (Router)":   "172.20.0.3",
}

# FIX: SnakeT2R is not on a network netops01 can route to, so test it from fw01
T2R_ROUTER_IP = "192.168.1.113"
T2R_ROUTER_LO = "192.168.1.124"

SEP  = "=" * 65
SEP2 = "-" * 65

# Summary of pass/fail checks, printed at the end of each report
RESULTS = []


def record(check, ok, detail=""):
    RESULTS.append([check, "PASS" if ok else "FAIL", detail])


def hdr(title):
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


def indent(text, pad="    "):
    return "\n".join(pad + line for line in text.splitlines()) if text else f"{pad}(no output)"


def run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "TIMEOUT", 1
    except Exception as e:
        return "", str(e), 1


def docker_exec(client, container_name, cmd):
    """Run a command inside a container. Returns (output, exit_code).
    FIX: stderr and the exit code used to be thrown away, so failures
    showed up as empty output."""
    try:
        c = client.containers.get(container_name)
        if c.status != "running":
            return f"[container {container_name} is {c.status}]", 1
        result = c.exec_run(cmd, demux=True)
        out, err = result.output or (None, None)
        text = (out or b"").decode("utf-8", errors="replace").strip()
        if result.exit_code != 0 and err:
            text = (text + "\n" if text else "") + err.decode("utf-8", errors="replace").strip()
        return text, result.exit_code
    except Exception as e:
        return f"[ERROR: {e}]", 1


def vtysh(client, cname, command):
    return docker_exec(client, cname, ["vtysh", "-c", command])


def count_full(neighbor_output):
    return sum(1 for line in neighbor_output.splitlines() if "Full" in line)


# ---- Section 1: Connectivity -------------------------------------
def check_connectivity():
    hdr("1. CONNECTIVITY CHECK  (ping from netops01 / 172.20.0.10)")
    rows = []
    for name, ip in MGMT_HOSTS.items():
        out, _, rc = run(["ping", "-c", "3", "-W", "2", ip])
        rtt = ""
        if rc == 0:
            for line in out.splitlines():
                if ("rtt" in line or "round-trip" in line) and "/" in line:
                    rtt = f"avg {line.split('/')[4]} ms"
                    break
        # FIX: ASCII status instead of check/cross glyphs
        rows.append([name, ip, "UP" if rc == 0 else "DOWN", rtt])
        record(f"ping {name}", rc == 0, ip)
    print(tabulate(rows, headers=["Host", "IP", "Status", "RTT"], tablefmt="simple"))


# ---- Section 2: OSPFv3 -------------------------------------------
def check_ospf6(client):
    hdr("2. OSPFv3  (rt01 <-> fw01 on ospf0)")

    for cname in ("rt01", "fw01"):
        out, _ = vtysh(client, cname, "show ipv6 ospf6 neighbor")
        full = count_full(out)
        print(f"\n  [Neighbors on {cname}]  Full: {full}")
        print(indent(out))
        record(f"OSPFv3 adjacency on {cname}", full >= 1, f"{full} Full")

    out, _ = vtysh(client, "rt01", "show ipv6 ospf6 route")
    print("\n  [OSPFv3 routes on rt01]")
    print(indent(out))

    out, _ = vtysh(client, "rt01", "show ipv6 ospf6 interface ospf0")
    print("\n  [Interface ospf0 on rt01]")
    print(indent(out))


# ---- Section 3: OSPFv2 + PIM to SnakeT2R (new) -------------------
def check_t2r(client):
    hdr("3. fw01 <-> SnakeT2R  (t2r, VLAN 224, OSPFv2 + PIM)")

    out, rc = docker_exec(client, "fw01", ["ip", "-br", "addr", "show", "dev", "t2r"])
    print("  [t2r interface]")
    print(indent(out))
    record("fw01 t2r interface", rc == 0 and "UP" in out, out.split()[1] if rc == 0 and out else "missing")

    out, rc = docker_exec(client, "fw01", ["ping", "-c", "2", "-W", "2", T2R_ROUTER_IP])
    record("ping SnakeT2R Gi2.224 from fw01", rc == 0, T2R_ROUTER_IP)
    print(f"\n  [ping {T2R_ROUTER_IP} from fw01]  {'UP' if rc == 0 else 'DOWN'}")

    out, _ = vtysh(client, "fw01", "show ip ospf neighbor")
    full = count_full(out)
    print(f"\n  [OSPFv2 neighbors on fw01]  Full: {full}")
    print(indent(out))
    record("OSPFv2 adjacency to SnakeT2R", T2R_ROUTER_LO in out and full >= 1,
           "Full" if full else "check MD5 key / VLAN / MTU")

    out, _ = vtysh(client, "fw01", "show ip route ospf")
    print("\n  [OSPFv2 routes on fw01]")
    print(indent(out))
    record("route to SnakeT2R Loopback0", T2R_ROUTER_LO in out, T2R_ROUTER_LO)

    out, _ = vtysh(client, "fw01", "show ip pim neighbor")
    print("\n  [PIM neighbors on fw01]")
    print(indent(out))
    record("PIM neighbor SnakeT2R", T2R_ROUTER_IP in out, T2R_ROUTER_IP)

    out, _ = vtysh(client, "fw01", "show ip pim rp-info")
    print("\n  [PIM RP info on fw01]")
    print(indent(out))


# ---- Section 4: DHCP ---------------------------------------------
LEASE_RE = re.compile(r"lease\s+(\S+)\s*\{(.*?)\}", re.S)


def parse_leases(text):
    """FIX: dhcpd.leases is an append-only log - the last block per IP wins.
    The old code printed the whole file, expired and duplicate entries included."""
    latest = {}
    for ip, body in LEASE_RE.findall(text):
        def field(pattern):
            m = re.search(pattern, body)
            return m.group(1) if m else ""
        latest[ip] = {
            "ip": ip,
            "state": field(r"binding state (\w+);"),
            "mac": field(r"hardware ethernet ([0-9a-fA-F:]+);"),
            "host": field(r'client-hostname "([^"]*)";'),
            "ends": field(r"ends \d (\S+ \S+);"),
        }
    return list(latest.values())


def check_dhcp(client):
    hdr("4. DHCP SERVER  (fw01)")

    # FIX: "pgrep dhcpd && echo RUNNING" printed the PID and the word together
    _, rc = docker_exec(client, "fw01",
                        ["sh", "-c", "kill -0 $(cat /run/dhcpd.pid 2>/dev/null) 2>/dev/null"])
    print(f"  dhcpd process : {'RUNNING' if rc == 0 else 'STOPPED'}")
    record("dhcpd running on fw01", rc == 0)

    text, _ = docker_exec(client, "fw01", ["cat", "/var/lib/dhcp/dhcpd.leases"])
    active = [l for l in parse_leases(text) if l["state"] == "active"]
    if active:
        print("\n  Active leases:")
        rows = [[l["ip"], l["mac"], l["host"], l["ends"] + " UTC"] for l in active]
        print(tabulate(rows, headers=["IP", "MAC", "Hostname", "Ends"], tablefmt="simple"))
    else:
        print("  Active leases : none")
    for net, name in (("10.10.10.", "client-lan"), ("10.10.20.", "client-dmz")):
        hit = any(l["ip"].startswith(net) for l in active)
        record(f"DHCP lease for {name}", hit)

    print("\n  Configured pools:")
    print("    VLAN10 / LAN : 10.10.10.100 - 10.10.10.200  (GW 10.10.10.1)")
    print("    VLAN20 / DMZ : 10.10.20.100 - 10.10.20.200  (GW 10.10.20.1)")


# ---- Section 5: Firewall rules -----------------------------------
def check_firewall(client):
    hdr("5. FIREWALL RULES  (fw01 iptables)")

    # FIX: INPUT added - that is where the OSPF/PIM rules for t2r live.
    #      -v adds packet counters so you can see which rules actually hit.
    for chain in ("INPUT", "FORWARD"):
        out, _ = docker_exec(client, "fw01", ["iptables", "-L", chain, "-n", "-v", "--line-numbers"])
        print(f"\n  [{chain}]")
        print(indent(out))

    out, _ = docker_exec(client, "fw01", ["iptables", "-t", "nat", "-L", "POSTROUTING", "-n", "-v"])
    print(f"\n  [NAT POSTROUTING]")
    print(indent(out))


# ---- Section 6: SVIs ---------------------------------------------
def check_svis(client):
    hdr("6. SWITCHED VIRTUAL INTERFACES  (fw01)")

    # FIX: the old substring match on "ip addr show" returned header and inet
    #      lines mixed together, with duplicates.
    out, _ = docker_exec(client, "fw01", ["ip", "-br", "addr", "show"])
    svis = [l for l in out.splitlines() if l.split() and l.split()[0].split("@")[0] in
            ("vlan10", "vlan20", "vlan10.10", "vlan20.20")]
    if svis:
        print("  Active SVI interfaces:")
        print(indent("\n".join(svis)))
    else:
        print("  (no SVI interfaces detected)")

    # FIX: the IP lives on vlan10/vlan20; the .10/.20 tagged SVIs have none
    print("\n  SVI mapping:")
    print("    vlan10  (10.10.10.1/24)  + tagged SVI vlan10.10 -> VLAN 10 (LAN)")
    print("    vlan20  (10.10.20.1/24)  + tagged SVI vlan20.20 -> VLAN 20 (DMZ)")


# ---- Section 7: Routing tables -----------------------------------
def check_routes(client):
    hdr("7. ROUTING TABLES")
    for label, cname, cmd in (
        ("fw01 IPv4 routes", "fw01", ["ip", "route", "show"]),
        ("fw01 IPv6 routes", "fw01", ["ip", "-6", "route", "show"]),
        ("rt01 IPv4 routes", "rt01", ["ip", "route", "show"]),
        ("rt01 IPv6 routes", "rt01", ["ip", "-6", "route", "show"]),
    ):
        out, _ = docker_exec(client, cname, cmd)
        print(f"\n  [{label}]")
        print(indent(out))


# ---- Section 8: Interface inventory ------------------------------
def interface_inventory(client):
    hdr("8. INTERFACE INVENTORY")
    for cname in ("fw01", "rt01"):
        out, _ = docker_exec(client, cname, ["ip", "-br", "addr", "show"])
        print(f"\n  [{cname}]")
        print(indent(out))


# ---- Report ------------------------------------------------------
def report():
    RESULTS.clear()
    print(f"\n{'#' * 65}")
    print("#  NETWORK OPERATIONS CENTER - STATUS REPORT")
    print(f"#  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  netops01 (172.20.0.10)")
    print(f"{'#' * 65}")

    # FIX: connectivity runs even when the Docker SDK or socket is unavailable
    sections = [check_connectivity]
    client = None
    if docker is None:
        print("WARNING: 'docker' Python package missing - connectivity check only.")
    else:
        try:
            client = docker.from_env()
            client.ping()
        except Exception as e:
            print(f"WARNING: cannot reach Docker daemon ({e}) - connectivity check only.")
            client = None

    if client:
        sections += [check_ospf6, check_t2r, check_dhcp, check_firewall,
                     check_svis, check_routes, interface_inventory]

    # FIX: one failing section no longer aborts the whole report
    for section in sections:
        try:
            section() if section is check_connectivity else section(client)
        except Exception as e:
            print(f"\n  [ERROR in {section.__name__}: {e}]")
            record(section.__name__, False, str(e)[:40])

    hdr("SUMMARY")
    print(tabulate(RESULTS, headers=["Check", "Result", "Detail"], tablefmt="simple"))
    failed = sum(1 for r in RESULTS if r[1] == "FAIL")
    print(f"\n  {len(RESULTS) - failed}/{len(RESULTS)} checks passed")
    print(f"\n{SEP}\n  END OF REPORT\n{SEP}")
    return failed


def main():
    parser = argparse.ArgumentParser(description="netops01 network monitor")
    parser.add_argument("--once", action="store_true", help="run one report and exit")
    parser.add_argument("--interval", type=int, default=60, help="seconds between reports")
    args = parser.parse_args()

    if args.once:
        # exit code = number of failed checks (0 = all good), handy for scripts/CI
        sys.exit(min(report(), 125))

    # FIX: --once was parsed but ignored; the loop now lives here
    while True:
        try:
            report()
        except Exception as e:
            print(f"[netops01] report failed: {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
