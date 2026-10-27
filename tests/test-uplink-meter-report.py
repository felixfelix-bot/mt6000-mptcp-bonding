#!/usr/bin/env python3
"""Offline test for scripts/uplink_meter_report.sh against a captured nft dump."""
import json, os, subprocess, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPT = os.path.join(REPO, "scripts", "uplink_meter_report.sh")
FIXTURE = os.path.join(HERE, "fixtures", "uplink_proxies.nft")


class TestMeterReport(unittest.TestCase):
    def run_script(self, *args):
        with tempfile.TemporaryDirectory() as cfg:
            with open(os.path.join(cfg, "minimize_cost.json"), "w") as fh:
                fh.write('{"local_port": 1101, "outbound_bind_interface": "lan3"}')
            env = dict(os.environ,
                       UPLINK_NFT_FIXTURE=FIXTURE,
                       UPLINK_CONF_DIR=cfg)
            out = subprocess.run(["sh", SCRIPT, *args], capture_output=True,
                                 text=True, env=env, check=True)
            return out.stdout

    def test_json_reports_both_directions_and_total(self):
        doc = json.loads(self.run_script("--json"))
        self.assertEqual(doc["table"], "uplink_proxies")
        self.assertEqual(len(doc["clients"]), 1)
        c = doc["clients"][0]
        self.assertEqual(c["ip"], "192.168.50.10")
        self.assertEqual(c["up_bytes"], 91057)
        self.assertEqual(c["down_bytes"], 20092835)
        self.assertEqual(c["total_bytes"], 20092835 + 91057)
        self.assertEqual(c["uplink"], "lan3")
        self.assertIsNone(c["sats"])

    def test_price_turns_bytes_into_sats(self):
        doc = json.loads(self.run_script("--json", "--price=lan3=24"))
        c = doc["clients"][0]
        expected = (c["total_bytes"] / 1073741824) * 24
        self.assertAlmostEqual(c["sats"], expected, places=4)

    def test_table_output_has_a_header_and_a_row(self):
        out = self.run_script("--price=lan3=24")
        lines = [l for l in out.splitlines() if l.strip()]
        self.assertEqual(len(lines), 2, out)
        self.assertIn("CLIENT", lines[0])
        self.assertIn("192.168.50.10", lines[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
