#!/usr/bin/env python3
"""Offline tests for roles/uplink_proxies rendering decisions.

The role renders an nft fragment on the box; this reproduces the same decisions
in Python so a regression in the *policy* fails without a router:

  * one redirect per client, to that client's objective port;
  * LAN destinations exempt in the steering and upload chains, so the router stays
    reachable and management traffic is never counted;
  * NO exemption in the download chain — the proxy's replies come from the router's
    own LAN address and must be counted, or every session under-reads;
  * unique ports, all above the stock instance.

    python3 tests/test-uplink-proxies.py
"""
import unittest

import yaml

GV = __file__.rsplit("/tests/", 1)[0] + "/group_vars/all.yml"


def load():
    with open(GV) as fh:
        return yaml.safe_load(fh)


def render(gv, clients):
    out = {"pre": [], "up": [], "down": []}
    proxies = {p["objective"]: p for p in gv["uplink_proxies"]}
    exempt = gv["uplink_proxy_exempt_cidrs"]
    for c in sorted(clients, key=lambda c: c["ip"]):
        port = proxies[c["objective"]]["port"]
        for cidr in exempt:
            out["pre"].append(f"ip saddr {c['ip']} ip daddr {cidr} return")
        out["pre"].append(f"ip saddr {c['ip']} meta l4proto tcp redirect to :{port}")
        for cidr in exempt:
            out["up"].append(f"ip saddr {c['ip']} ip daddr {cidr} return")
        out["up"].append(f"ip saddr {c['ip']} counter")
        out["down"].append(f"ip daddr {c['ip']} counter")
    return out


class TestUplinkProxies(unittest.TestCase):
    def setUp(self):
        self.gv = load()

    def test_ports_unique_and_above_stock(self):
        ports = [p["port"] for p in self.gv["uplink_proxies"]]
        self.assertEqual(len(ports), len(set(ports)))
        self.assertGreater(min(ports), self.gv["uplink_proxy_base_port"])

    def test_client_redirects_to_its_objective_port(self):
        clients = [{"ip": "192.168.50.10", "objective": "minimize_cost"}]
        rules = render(self.gv, clients)
        self.assertIn("ip saddr 192.168.50.10 meta l4proto tcp redirect to :1101", rules["pre"])

    def test_lan_is_exempt_from_steering_and_upload(self):
        rules = render(self.gv, [{"ip": "192.168.50.10", "objective": "minimize_cost"}])
        for chain in ("pre", "up"):
            self.assertTrue(
                any("ip daddr 192.168.50.0/24 return" in r for r in rules[chain]),
                f"{chain} must exempt the LAN so SSH/LuCI survive",
            )

    def test_download_chain_has_no_exemption(self):
        # The proxy answers from the router's LAN address; exempting the LAN here
        # would stop the meter counting the bulk of every session.
        rules = render(self.gv, [{"ip": "192.168.50.10", "objective": "minimize_cost"}])
        self.assertFalse(any("return" in r for r in rules["down"]))
        self.assertIn("ip daddr 192.168.50.10 counter", rules["down"])

    def test_unknown_objective_is_refused(self):
        with self.assertRaises(KeyError):
            render(self.gv, [{"ip": "192.168.50.10", "objective": "cheapest"}])

    def test_two_clients_get_two_redirects(self):
        rules = render(self.gv, [
            {"ip": "192.168.50.10", "objective": "minimize_cost"},
            {"ip": "192.168.50.11", "objective": "maximize_speed"},
        ])
        redirects = [r for r in rules["pre"] if "redirect to" in r]
        self.assertEqual(len(redirects), 2)
        self.assertTrue(any(":1101" in r for r in redirects))
        self.assertTrue(any(":1102" in r for r in redirects))


if __name__ == "__main__":
    unittest.main(verbosity=2)
