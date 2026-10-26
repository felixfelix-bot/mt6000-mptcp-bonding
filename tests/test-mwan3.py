#!/usr/bin/env python3
"""Controller-only regression checks; never connect to a router.

Run with the Python interpreter that provides Ansible (usually /usr/bin/python3).
"""
import copy
import json
from pathlib import Path
import subprocess
import unittest

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

ROOT = Path(__file__).resolve().parents[1]
VARS = yaml.safe_load((ROOT / 'group_vars/all.yml').read_text())


class Mwan3Tests(unittest.TestCase):
    def validate(self, variables):
        return subprocess.run(
            ['ansible-playbook', '-i', 'localhost,', 'tests/render-mwan3.yml',
             '-e', json.dumps(variables)], cwd=ROOT, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )

    def test_invalid_weights_fail_clearly(self):
        for weight in [0, -1, 1.5, '3', True]:
            with self.subTest(weight=weight):
                members = copy.deepcopy(VARS['mwan3_members'])
                members[0]['weight'] = weight
                result = self.validate({'mwan3_members': members})
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn('integer weight/metric >= 1', result.stdout)

    def test_unknown_policy_member_fails(self):
        result = self.validate({'mwan3_policies': [
            {'name': 'balanced', 'members': ['missing']}]})
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('known member interfaces', result.stdout)

    def test_render_and_raw_shell_syntax(self):
        variables = dict(VARS, mwan3_backup_dir='/etc/mt6000-mwan3-backup')
        loader = DataLoader()
        loader.set_basedir(str(ROOT / 'roles/mwan3/templates'))
        templar = Templar(loader=loader, variables=variables)
        config = templar.template((ROOT / 'roles/mwan3/templates/mwan3.j2').read_text())
        self.assertIn("option weight '3'", config)
        self.assertIn("list use_member 'wwan_member'", config)
        for filename in ['main.yml', 'teardown.yml']:
            tasks = yaml.safe_load((ROOT / 'roles/mwan3/tasks' / filename).read_text())
            for task in tasks:
                for candidate in task.get('block', [task]):
                    raw = candidate.get('ansible.builtin.raw')
                    if raw:
                        script = templar.template(raw)
                        result = subprocess.run(['sh', '-n'], input=script,
                                                text=True, capture_output=True)
                        self.assertEqual(result.returncode, 0,
                                         candidate['name'] + result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
