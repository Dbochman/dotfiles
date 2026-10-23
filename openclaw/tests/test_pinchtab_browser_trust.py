import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('trust_port', ROOT / 'openclaw/bin/pinchtab-browser-trust-port.py')
port = importlib.util.module_from_spec(spec)
spec.loader.exec_module(port)


class BrowserTrustTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.instance = {'id': 'inst_test', 'profileId': 'prof_test', 'profileName': 'audit', 'mode': 'headless', 'status': 'running', 'port': '19868'}
        self.config = {'server': {'stateDir': str(self.root)}}
        self.child = self.root / 'profiles/prof_test/.pinchtab-state/config.json'
        self.child.parent.mkdir(parents=True)
        self.child.write_text(json.dumps({'server': {'port': '19868'}, 'browser': {'remoteDebuggingPort': 19869}}))

    def test_resolves_only_matching_managed_headless_instance(self):
        self.assertEqual(port.resolve(self.instance, self.config, self.root), 19869)
        for patch in [{'status': 'stopped'}, {'mode': 'headed'}, {'attached': True}, {'profileId': '../private'}, {'port': '19870'}]:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                port.resolve({**self.instance, **patch}, self.config, self.root)

    def test_invalid_ports_fail_closed(self):
        for value in [0, 65536, True, '19869']:
            self.child.write_text(json.dumps({'server': {'port': '19868'}, 'browser': {'remoteDebuggingPort': value}}))
            with self.subTest(value=value), self.assertRaises(ValueError):
                port.resolve(self.instance, self.config, self.root)

    def test_attended_headed_resolution_requires_explicit_exact_mode(self):
        headed = {**self.instance, 'mode': 'headed'}
        self.assertEqual(port.resolve(headed, self.config, self.root, mode='headed'), 19869)
        with self.assertRaises(ValueError):
            port.resolve(headed, self.config, self.root)
        with self.assertRaises(ValueError):
            port.resolve(self.instance, self.config, self.root, mode='headed')
        with self.assertRaises(ValueError):
            port.resolve(headed, self.config, self.root, mode='anything')

    def test_acquire_gate_and_new_instance_cleanup(self):
        config_file = self.root / 'config.json'
        config_file.write_text(json.dumps(self.config))
        cli = self.root / 'pinchtab'
        log = self.root / 'calls'
        cli.write_text('''#!/usr/bin/env python3
import json,os,sys,pathlib
with open(os.environ['CALL_LOG'],'a') as f: f.write(' '.join(sys.argv[1:])+'\\n')
a=sys.argv[1:]
marker=pathlib.Path(os.environ['START_MARKER'])
if a[0]=='instances': print('[]' if os.environ['FAKE_NEW']=='1' and not marker.exists() else os.environ['INSTANCE_JSON'])
elif a[0]=='profiles': print('[{"name":"audit"}]')
elif a[0]=='health' or a[-1]=='health': print('{"status":"ok"}')
elif a[:2]==['instance','start']: marker.touch(); print('{"id":"inst_test"}')
elif a[:2]==['instance','stop']: pass
else: sys.exit(3)
''')
        cli.chmod(0o755)
        node = self.root / 'node'
        node.write_text('#!/bin/sh\nexit "$GATE_STATUS"\n')
        node.chmod(0o755)
        marker = self.root / 'started'
        env = {**os.environ, 'PINCHTAB_CONFIG': str(config_file), 'PINCHTAB_BIN': str(cli), 'PINCHTAB_TRUST_NODE': str(node), 'CALL_LOG': str(log), 'INSTANCE_JSON': json.dumps([self.instance]), 'START_MARKER': str(marker)}
        for status, new in [('1', '0'), ('2', '0'), ('0', '0'), ('1', '1'), ('0', '1')]:
            marker.unlink(missing_ok=True)
            log.write_text('')
            result = subprocess.run(['bash', str(ROOT / 'openclaw/bin/pinchtab-headless-instance'), 'acquire', 'audit'], env={**env, 'GATE_STATUS': status, 'FAKE_NEW': new}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0 if status == '0' else 1, result.stderr)
            self.assertEqual(result.stdout, f'inst_test\t{new}\n' if status == '0' else '')
            self.assertEqual('instance stop' in log.read_text(), new == '1' and status != '0')


if __name__ == '__main__':
    unittest.main()
