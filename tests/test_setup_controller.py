import queue
from pathlib import Path
import sys
import threading
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'installer'))
from installer_core import DeviceNotReady, DeviceStatus, SetupError
from setup_controller import SetupController

class FakeEngine:
    def __init__(self):
        self.cancel=threading.Event(); self.ready=True; self.installs=[]
        self.started=threading.Event(); self.release=threading.Event(); self.fail_prepare=False
    def prepare(self,cache):
        if self.fail_prepare: raise SetupError('Damaged bundle')
    def inspect_phone(self):
        return DeviceStatus('ready' if self.ready else 'searching','Phone check',
                            'Ready' if self.ready else 'Reconnect phone',ready=self.ready,serial='phone-a' if self.ready else '')
    def install(self,expected_serial=None):
        self.installs.append(expected_serial); self.started.set()
        if not self.release.wait(2): raise RuntimeError('Test did not release installation')
        status=self.inspect_phone()
        if not status.ready or status.serial!=expected_serial: raise DeviceNotReady(status)
        return Path('installed')

class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.engine=FakeEngine(); self.events=queue.Queue()
        self.controller=SetupController(self.engine,'cache',lambda k,d:self.events.put((k,d)),poll_interval=.01)
        self.thread=threading.Thread(target=self.controller.run); self.addCleanup(self.cleanup)
    def cleanup(self):
        self.engine.release.set(); self.controller.stop()
        if self.thread.ident is not None:
            self.thread.join(3); self.assertFalse(self.thread.is_alive())
    def event(self,kind,predicate=lambda d:True):
        while True:
            current,data=self.events.get(timeout=3)
            if current==kind and predicate(data): return data
    def test_checks_run_without_install_and_stop_on_close(self):
        self.thread.start(); self.event('device',lambda d:d['ready'])
        self.engine.ready=False; self.event('device',lambda d:not d['ready'])
        self.assertFalse(self.controller.request_install('phone-a')); self.assertEqual(self.engine.installs,[])
        self.controller.stop(); self.event('worker_stopped')
    def test_checked_phone_only_and_no_duplicate_installs(self):
        self.assertFalse(self.controller.request_install('phone-a'))
        self.thread.start(); self.event('device',lambda d:d['ready'])
        self.assertFalse(self.controller.request_install('phone-b')); self.assertTrue(self.controller.request_install('phone-a'))
        self.assertTrue(self.engine.started.wait(2)); self.assertFalse(self.controller.request_install('phone-a'))
        self.engine.release.set(); self.assertEqual(self.event('done'),'installed'); self.event('worker_stopped')
        self.assertFalse(self.controller.request_install('phone-a')); self.assertEqual(self.engine.installs,['phone-a'])
    def test_disconnect_during_install_returns_to_checks_and_retry(self):
        self.thread.start(); self.event('device',lambda d:d['ready'])
        self.assertTrue(self.controller.request_install('phone-a')); self.assertTrue(self.engine.started.wait(2))
        self.engine.ready=False; self.engine.release.set()
        self.assertIn('Reconnect phone',self.event('install_error')); self.event('device',lambda d:not d['ready'])
        self.assertFalse(self.controller.request_install('phone-a'))
        self.engine.ready=True; self.event('device',lambda d:d['ready'])
        self.assertTrue(self.controller.request_install('phone-a')); self.event('done')
        self.assertEqual(self.engine.installs,['phone-a','phone-a'])
    def test_bundle_failure_stops_before_phone_checks(self):
        self.engine.fail_prepare=True; self.thread.start()
        self.assertEqual(self.event('fatal'),'Damaged bundle'); self.event('worker_stopped')
        self.assertIsNone(self.controller.latest); self.assertEqual(self.engine.installs,[])

if __name__=='__main__': unittest.main()
