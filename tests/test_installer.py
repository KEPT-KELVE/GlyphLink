import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'installer'))
import installer_core as core

class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.payload = self.root / 'payload'
        self.target = self.root / 'install'
        for relative in core.REQUIRED:
            p = self.payload / relative
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'MZfixture')
        with zipfile.ZipFile(self.payload / 'android/GlyphLink.apk', 'w') as z:
            z.writestr('AndroidManifest.xml', b'fixture')
            z.writestr('classes.dex', b'fixture')
        self.manifest()
        self.events, self.calls = [], []

    def manifest(self):
        files = {p.relative_to(self.payload).as_posix(): {'size': p.stat().st_size, 'sha256': core.sha256(p)}
                 for p in self.payload.rglob('*') if p.is_file() and p.name != 'manifest.json'}
        (self.payload / 'manifest.json').write_text(json.dumps({'version': core.VERSION, 'package': core.PACKAGE, 'files': files}))

    def engine(self, runner=None):
        return core.Installer(self.payload, self.target, lambda *args: self.events.append(args), runner=runner or self.runner)

    def runner(self, args, timeout=30):
        args = list(map(str, args)); self.calls.append(args)
        if args[1:] == ['devices', '-l']: out = 'List of devices attached\ncb9f7fe8 device product:spacewar\n'
        elif args[-1] == 'ro.product.device': out = 'spacewar\n'
        elif args[-1] == 'ro.product.model': out = 'A063\n'
        elif args[-1] == 'ro.build.version.sdk': out = '35\n'
        elif args[-1] == 'ro.build.version.release': out = '15\n'
        elif args[-1] == 'sys.boot_completed': out = '1\n'
        elif args[-2] == 'echo': out = args[-1] + '\n'
        elif args[-1] == 'get-state': out = 'device\n'
        elif 'path' in args: out = 'package:/data/app/companion/base.apk\n'
        elif 'install' in args: out = 'Performing Streamed Install\nSuccess\n'
        else: out = 'Status: ok\n'
        return subprocess.CompletedProcess(args, 0, out, '')

    def test_complete_payload(self):
        self.assertEqual(core.validate_payload(self.payload)['version'], core.VERSION)

    def test_missing_apk_stops_before_any_commands_or_install_writes(self):
        (self.payload / 'android/GlyphLink.apk').unlink()
        with self.assertRaises(core.SetupError): self.engine().install()
        self.assertEqual(self.calls, [])
        self.assertFalse(self.target.exists())

    def test_missing_runtime_rejected_even_if_manifest_is_regenerated(self):
        (self.payload / 'app/runtime/DLLs/_tkinter.pyd').unlink()
        self.manifest()
        with self.assertRaises(core.SetupError): core.validate_payload(self.payload)

    def test_tamper_rejected_before_install(self):
        with (self.payload / 'android/GlyphLink.apk').open('ab') as f: f.write(b'tampered')
        with self.assertRaises(core.SetupError): self.engine().install()
        self.assertFalse(self.target.exists())
        self.assertEqual(self.calls, [])

    def test_waits_through_authorization_and_offline(self):
        outputs = iter(['', 'abc unauthorized\n', 'abc offline\n', 'abc device\n'])
        def runner(args, timeout=30):
            if list(map(str, args))[1:] == ['devices', '-l']:
                return subprocess.CompletedProcess(args, 0, next(outputs), '')
            return self.runner(args, timeout)
        self.assertEqual(self.engine(runner).wait_for_phone(timeout=1, poll=0), 'abc')
        titles = [data[1] for kind, data in self.events if kind == 'progress']
        self.assertIn('Tap Allow on your phone', titles)
        self.assertIn('Waiting for the phone to reconnect', titles)

    def test_wrong_phone_rejected(self):
        def runner(args, timeout=30):
            if str(args[-1]) in ['ro.product.model', 'ro.product.device']:
                return subprocess.CompletedProcess(args, 0, 'different phone', '')
            return self.runner(args, timeout)
        with self.assertRaisesRegex(core.SetupError, 'Nothing Phone'): self.engine(runner).wait_for_phone(timeout=1, poll=0)

    def test_multiple_phones_not_silently_selected(self):
        def runner(args, timeout=30):
            return subprocess.CompletedProcess(args, 0, 'a device\nb unauthorized\n', '')
        with self.assertRaises(core.SetupError): self.engine(runner).wait_for_phone(timeout=.02, poll=.01)
        self.assertTrue(any(kind == 'progress' and data[1] == 'Connect one phone' for kind, data in self.events))

    def test_wireless_and_emulators_ignored(self):
        self.assertEqual(core.parse_devices('emulator-5554 device\n192.168.1.2:5555 device\nabc device\n'), [('abc', 'device')])

    def test_signature_failure_never_uninstalls_or_launches(self):
        def runner(args, timeout=30):
            self.calls.append(list(map(str, args)))
            return subprocess.CompletedProcess(args, 1, '', 'Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE]')
        e = self.engine(runner);e.serial = 'abc'
        with self.assertRaisesRegex(core.SetupError, 'signing key'): e.install_android()
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn('uninstall', self.calls[0])
        self.assertNotIn('-d', self.calls[0])

    def test_install_checks_success_even_with_zero_exit(self):
        def runner(args, timeout=30):
            return subprocess.CompletedProcess(args, 0, 'Failure [INSTALL_FAILED_USER_RESTRICTED]', '')
        e=self.engine(runner);e.serial='abc'
        with self.assertRaises(core.SetupError):e.install_android()

    def test_successful_android_install_checks_package_and_launch(self):
        e=self.engine();e.serial='abc';e.install_android()
        self.assertTrue(any('install' in args and '-r' in args for args in self.calls))
        self.assertTrue(any('path' in args and core.PACKAGE in args for args in self.calls))
        self.assertTrue(any(core.ACTIVITY in args for args in self.calls))

    def test_handshake_handles_partial_reply_and_removes_temporary_forward(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen()
        self.addCleanup(listener.close)
        def server():
            with listener.accept()[0] as conn:
                self.assertEqual(conn.recv(20),b'PING\n')
                conn.sendall(b'PO');conn.sendall(b'NG\n')
        t=threading.Thread(target=server);t.start()
        def runner(args,timeout=30):
            if 'tcp:0' in args:return subprocess.CompletedProcess(args,0,str(listener.getsockname()[1]),'')
            return self.runner(args,timeout)
        e=self.engine(runner);e.serial='abc';e.verify_companion(timeout=2);t.join(2)
        self.assertTrue(any('--remove' in args for args in self.calls))

    def test_eof_cannot_be_reported_as_connected(self):
        fake=unittest.mock.MagicMock()
        fake.__enter__.return_value=fake;fake.recv.return_value=b''
        e=self.engine();e.serial='abc'
        with patch.object(e,'phone',return_value='49152'), patch.object(core.socket,'create_connection',return_value=fake):
            with self.assertRaises(core.SetupError):e.verify_companion(timeout=.01)

    def test_cancel_does_not_install(self):
        e=self.engine();e.cancel.set()
        with self.assertRaises(core.Cancelled):e.install()
        self.assertFalse(self.target.exists())

    def add_importable_module(self):
        module = self.payload / 'app/runtime/Lib/cache_probe.py'
        module.write_text('VALUE = 42\n', encoding='utf-8')
        self.manifest()
        return module

    def import_in_fresh_python(self, module, flags=()):
        script = ('import importlib.util, sys; '
                  's=importlib.util.spec_from_file_location("cache_probe",sys.argv[1]); '
                  'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
                  'assert m.VALUE == 42')
        subprocess.run([sys.executable, '-E', '-s', *flags, '-c', script, str(module)],
                       check=True, capture_output=True, text=True)

    def test_real_python_import_caches_do_not_break_validation_or_staging(self):
        module = self.add_importable_module()
        core.validate_payload(self.payload)
        self.import_in_fresh_python(module)
        self.import_in_fresh_python(module, flags=('-O',))
        caches = list(module.parent.glob('__pycache__/cache_probe.*.pyc'))
        self.assertEqual(len(caches), 2)  # Real regular and optimized Python bytecode.
        core.validate_payload(self.payload)
        core.validate_payload(self.payload)  # Retrying setup is valid too.
        staged = self.engine().stage_windows()
        self.assertEqual((staged / 'runtime/Lib/cache_probe.py').read_text(), 'VALUE = 42\n')
        self.assertEqual(list(staged.rglob('*.pyc')), [])

    def test_no_bytecode_launcher_flag_keeps_payload_unchanged(self):
        module = self.add_importable_module()
        self.import_in_fresh_python(module, flags=('-B',))
        self.assertFalse((module.parent / '__pycache__').exists())
        core.validate_payload(self.payload)

    def test_cache_does_not_hide_a_missing_source(self):
        module = self.add_importable_module()
        self.import_in_fresh_python(module)
        module.unlink()
        with self.assertRaisesRegex(core.SetupError, 'Missing files:.*'):
            core.validate_payload(self.payload)

    def test_cache_does_not_hide_changed_source(self):
        module = self.add_importable_module()
        self.import_in_fresh_python(module)
        module.write_text('VALUE = 99\n', encoding='utf-8')
        with self.assertRaisesRegex(core.SetupError, 'Damaged bundled file'):
            core.validate_payload(self.payload)

    def test_unexpected_file_in_cache_directory_is_rejected(self):
        extra = self.payload / 'app/runtime/Lib/__pycache__/unlisted.dll'
        extra.parent.mkdir(exist_ok=True)
        extra.write_bytes(b'MZunlisted')
        with self.assertRaisesRegex(core.SetupError, 'Unexpected files:'):
            core.validate_payload(self.payload)

    def test_bytecode_without_bundled_source_is_rejected(self):
        extra = self.payload / 'app/runtime/Lib/__pycache__/unlisted.cpython-312.pyc'
        extra.parent.mkdir(exist_ok=True)
        extra.write_bytes(b'orphan-cache')
        with self.assertRaisesRegex(core.SetupError, 'Unexpected files:'):
            core.validate_payload(self.payload)

    def test_dll_hash_is_still_checked_after_cache_generation(self):
        module = self.add_importable_module()
        self.import_in_fresh_python(module)
        (self.payload / 'app/runtime/python312.dll').write_bytes(b'MZchanged')
        with self.assertRaisesRegex(core.SetupError, 'Damaged bundled file'):
            core.validate_payload(self.payload)


    def test_preparation_only_caches_usb_tools(self):
        e = self.engine(); cache = self.root / 'usb-tools'; e.prepare(cache)
        self.assertFalse(self.target.exists()); self.assertFalse(e.log_path.exists())
        self.assertEqual({p.name for p in cache.rglob('*') if p.is_file()},
                         {'adb.exe', 'AdbWinApi.dll', 'AdbWinUsbApi.dll'})
        with patch.object(core.shutil, 'copytree') as copy:
            e.prepare(cache); copy.assert_not_called()

    def test_damaged_bundle_does_not_prepare_usb_tools(self):
        (self.payload / 'android/GlyphLink.apk').unlink()
        with self.assertRaises(core.SetupError): self.engine().prepare(self.root / 'usb-tools')
        self.assertFalse((self.root / 'usb-tools').exists()); self.assertEqual(self.calls, [])

    def test_readiness_checks_three_replies_without_installing(self):
        status = self.engine().inspect_phone()
        self.assertTrue(status.ready)
        echoes = [args for args in self.calls if 'echo' in args]
        self.assertEqual(len(echoes), 3); self.assertEqual(len({a[-1] for a in echoes}), 3)
        self.assertTrue(all(v[0] == 'pass' for v in status.checks.values()))
        self.assertFalse(any('install' in args or 'am' in args for args in self.calls))
        self.assertFalse(self.target.exists())

    def test_not_ready_states_stop_before_app_copy(self):
        for expected, command, output in [
            ('searching','devices',''), ('authorize','devices','cb9f7fe8 unauthorized\n'),
            ('offline','devices','cb9f7fe8 offline\n'), ('multiple','devices','a device\nb device\n'),
            ('booting','sys.boot_completed','0'), ('old_android','ro.build.version.sdk','30'),
            ('glyph_missing','path',''), ('unresponsive','echo','wrong'),
            ('unresponsive','get-state','offline'),
        ]:
            with self.subTest(expected=expected, command=command):
                def runner(args, timeout=30):
                    if command in list(map(str,args)): return subprocess.CompletedProcess(args,0,output,'')
                    return self.runner(args,timeout)
                e = self.engine(runner)
                with patch.object(e,'stage_windows') as stage:
                    with self.assertRaises(core.DeviceNotReady) as raised: e.install(expected_serial='cb9f7fe8')
                    self.assertEqual(raised.exception.status.state,expected); stage.assert_not_called()
                self.assertFalse(self.target.exists()); self.assertFalse(e.log_path.exists())

    def test_response_timeout_can_recover(self):
        def runner(args,timeout=30):
            if 'echo' in args: raise subprocess.TimeoutExpired(args,timeout)
            return self.runner(args,timeout)
        e=self.engine(runner); self.assertEqual(e.inspect_phone().state,'unresponsive')
        e.runner=self.runner; self.assertTrue(e.inspect_phone().ready)

    def test_disconnection_invalidates_previous_readiness(self):
        e=self.engine(); self.assertTrue(e.inspect_phone().ready)
        e.runner=lambda args,timeout=30: subprocess.CompletedProcess(args,0,'','')
        status=e.inspect_phone(); self.assertFalse(status.ready)
        self.assertFalse(any(v[0]=='pass' for v in status.checks.values()))

    def test_substituted_phone_cannot_install(self):
        e=self.engine()
        with patch.object(e,'stage_windows') as stage:
            with self.assertRaises(core.DeviceNotReady) as raised: e.install(expected_serial='previous-phone')
            self.assertEqual(raised.exception.status.state,'changed'); stage.assert_not_called()
        self.assertFalse(self.target.exists())

    def test_install_order_checks_phone_and_connection(self):
        e=self.engine(); order=[]; original=e.inspect_phone
        def inspect(expected_serial=None):
            order.append('inspect'); return original(expected_serial)
        e.inspect_phone=inspect
        steps=['stage_windows','stop_previous_app','install_android','verify_companion','activate_windows']
        for name in steps: setattr(e,name,lambda *args,name=name: order.append(name))
        e.install(expected_serial='cb9f7fe8')
        self.assertEqual(order,['inspect']+steps)

if __name__ == '__main__':unittest.main(verbosity=2)
