"""One worker owns USB checks and installation. The UI receives queued events."""
import queue
import threading
import traceback
from installer_core import Cancelled

class SetupController:
    def __init__(self, engine, tools_cache, report, poll_interval=1.2):
        self.engine, self.tools_cache, self.report = engine, tools_cache, report
        self.poll_interval = poll_interval
        self.cancel = engine.cancel
        self.pending = queue.Queue(maxsize=1)
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.latest = None
        self.installing = self.finished = False

    def request_install(self, serial):
        with self.lock:
            if (self.installing or self.finished or self.cancel.is_set() or self.latest is None
                    or not self.latest.ready or self.latest.serial != serial): return False
            self.installing = True
            self.pending.put_nowait(serial)
        self.wake.set()
        return True

    def stop(self):
        self.cancel.set()
        self.wake.set()

    def run(self):
        try:
            self.engine.prepare(self.tools_cache)
            previous = None
            while not self.cancel.is_set():
                try: serial = self.pending.get_nowait()
                except queue.Empty: serial = None
                if serial is not None:
                    self.report('installing', True)
                    try:
                        directory = self.engine.install(expected_serial=serial)
                        with self.lock: self.finished = True
                        self.report('done', str(directory))
                        return
                    except Cancelled: raise
                    except Exception as exc:
                        self.report('log', traceback.format_exc())
                        self.report('install_error', str(exc) or type(exc).__name__)
                    finally:
                        with self.lock:
                            self.installing = False
                            self.latest = None
                status = self.engine.inspect_phone()
                with self.lock: self.latest = status
                self.report('device', status.to_dict())
                summary = (status.state, status.serial, status.android)
                if summary != previous:
                    self.report('log', status.title + ': ' + status.detail)
                    previous = summary
                self.wake.wait(self.poll_interval)
                self.wake.clear()
        except Cancelled: self.report('cancelled', None)
        except Exception as exc:
            self.report('log', traceback.format_exc())
            self.report('fatal', str(exc) or type(exc).__name__)
        finally: self.report('worker_stopped', None)
