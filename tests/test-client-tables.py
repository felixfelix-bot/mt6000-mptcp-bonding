#!/usr/bin/env python3
"""Offline tests for the client_tables reconcile logic.

The role's shell block is generated from group_vars; this test reproduces the
same decisions in Python so a regression in the *policy* (which rules exist,
which get deleted) fails without a router.

    python3 tests/test-client-tables.py
"""
import re
import unittest

BASE = 12000
SPAN = 200

OBJECTIVES = {
    "minimize_cost": {"table": 11},
    "maximize_speed": {"table": 13},
    "balanced": {"table": "main"},
}


def desired_rules(clients):
    out = []
    for i, c in enumerate(sorted(clients, key=lambda c: c["ip"])):
        table = str(OBJECTIVES[c["objective"]]["table"])
        if table != "main":
            out.append((BASE + i, c["ip"], table))
    return out


def parse_rules(text):
    rules = []
    for line in text.splitlines():
        m = re.match(r"(\d+):\s+from (\S+) lookup (\S+)$", line.strip())
        if m:
            rules.append((int(m.group(1)), m.group(2), m.group(3)))
    return rules


def reconcile(existing_text, clients):
    """Return (to_add, to_delete, kept) exactly as the role decides."""
    want = desired_rules(clients)
    have = parse_rules(existing_text)
    have_in_band = [r for r in have if BASE <= r[0] <= BASE + SPAN]
    to_add = [r for r in want if r not in have]
    # An existing rule is only kept when pref AND table both match: a client
    # whose objective changed gets its pref deleted and re-added.
    to_delete = [r for r in have_in_band if r not in want]
    kept = [r for r in have_in_band if r in want]
    return to_add, to_delete, kept


class TestClientTables(unittest.TestCase):
    def test_balanced_emits_no_rule(self):
        got = desired_rules([{"ip": "192.168.50.9", "objective": "balanced"}])
        self.assertEqual(got, [])

    def test_prefs_are_deterministic_and_sorted_by_ip(self):
        clients = [
            {"ip": "192.168.50.30", "objective": "maximize_speed"},
            {"ip": "192.168.50.10", "objective": "minimize_cost"},
        ]
        self.assertEqual(
            desired_rules(clients),
            [(BASE, "192.168.50.10", "11"), (BASE + 1, "192.168.50.30", "13")],
        )

    def test_fresh_install_adds_all(self):
        adds, dels, kept = reconcile("0:\tfrom all lookup local", [
            {"ip": "192.168.50.180", "objective": "minimize_cost"},
        ])
        self.assertEqual(adds, [(BASE, "192.168.50.180", "11")])
        self.assertEqual(dels, [])
        self.assertEqual(kept, [])

    def test_second_run_is_a_noop(self):
        existing = f"{BASE}:\tfrom 192.168.50.180 lookup 11\n"
        adds, dels, kept = reconcile(existing, [
            {"ip": "192.168.50.180", "objective": "minimize_cost"},
        ])
        self.assertEqual(adds, [])
        self.assertEqual(dels, [])
        self.assertEqual(kept, [(BASE, "192.168.50.180", "11")])

    def test_objective_change_replaces_the_rule(self):
        existing = f"{BASE}:\tfrom 192.168.50.180 lookup 11\n"
        adds, dels, kept = reconcile(existing, [
            {"ip": "192.168.50.180", "objective": "maximize_speed"},
        ])
        self.assertEqual(adds, [(BASE, "192.168.50.180", "13")])
        # same pref, different table: the old rule is deleted, then re-added
        self.assertEqual(dels, [(BASE, "192.168.50.180", "11")])
        self.assertEqual(kept, [])

    def test_empty_client_list_removes_stale_band_rules(self):
        existing = f"{BASE}:\tfrom 192.168.50.180 lookup 11\n"
        adds, dels, kept = reconcile(existing, [])
        self.assertEqual(adds, [])
        self.assertEqual(dels, [(BASE, "192.168.50.180", "11")])

    def test_out_of_band_rules_are_never_touched(self):
        existing = "10000:\tfrom 192.168.50.1 lookup lan\n32766:\tfrom all lookup main\n"
        adds, dels, _ = reconcile(existing, [])
        self.assertEqual(adds, [])
        self.assertEqual(dels, [])

    def test_unknown_objective_is_refused(self):
        with self.assertRaises(KeyError):
            desired_rules([{"ip": "192.168.50.7", "objective": "cheapest"}])

    def test_span_must_cover_clients(self):
        clients = [{"ip": f"192.168.50.{i}", "objective": "minimize_cost"} for i in range(1, SPAN + 2)]
        self.assertGreater(len(desired_rules(clients)), SPAN - 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
