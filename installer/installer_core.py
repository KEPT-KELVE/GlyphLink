"""Offline setup engine. No downloads, builds, or file pickers on the user's PC."""
import base64
from dataclasses import asdict, dataclass, field
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
import uuid
import zipfile

VERSION = '1.3.6'
PACKAGE = 'com.adam.glyphlink'
ACTIVITY = PACKAGE + '/.MainActivity'
REQUIRED = ('app/GlyphLink.exe', 'android/GlyphLink.apk', 'platform-tools/adb.exe',
            'platform-tools/AdbWinApi.dll', 'platform-tools/AdbWinUsbApi.dll',
            'app/glyphlink.py', 'app/runtime/pythonw.exe', 'app/runtime/python312.dll',
            'app/runtime/vcruntime140.dll', 'app/runtime/vcruntime140_1.dll',
            'app/runtime/Lib/os.py', 'app/runtime/Lib/encodings/__init__.py',
            'app/runtime/DLLs/_tkinter.pyd', 'app/runtime/DLLs/tcl86t.dll',
            'app/runtime/DLLs/tk86t.dll', 'app/runtime/tcl/tcl8.6/init.tcl',
            'app/runtime/Lib/site-packages/customtkinter/__init__.py',
            'app/runtime/Lib/site-packages/numpy/__init__.py',
            'app/runtime/Lib/site-packages/soundcard/__init__.py',
            'app/runtime/Lib/site-packages/pystray/__init__.py',
            'app/runtime/Lib/site-packages/PIL/__init__.py') + tuple('app/assets/glyph_%d.png' % i for i in range(5))

class SetupError(RuntimeError): pass
class Cancelled(SetupError): pass

CHECK_LABELS = {'usb': 'USB connection', 'permission': 'Phone authorization',
                'android': 'Phone and Android version', 'glyph': 'Glyph support',
                'response': 'Phone response'}

@dataclass
class DeviceStatus:
    state: str
    title: str
    detail: str
    ready: bool = False
    serial: str = ''
    model: str = ''
    android: str = ''
    checks: dict = field(default_factory=lambda: {key: ('pending', 'Waiting to check') for key in CHECK_LABELS})

    def to_dict(self): return asdict(self)

class DeviceNotReady(SetupError):
    def __init__(self, status):
        self.status = status
        super().__init__(status.title + '\n\n' + status.detail)

def hidden_kwargs():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}

def run_command(args, timeout=30):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=timeout, **hidden_kwargs())

def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()

def is_generated_bytecode(relative, source_files):
    """Only tolerate normal Python caches whose source is part of this release."""
    path = Path(relative)
    if path.suffix != '.pyc' or path.parent.name != '__pycache__':
        return False
    if not path.is_relative_to(Path('app')):
        return False
    try:
        source = Path(importlib.util.source_from_cache(str(path))).as_posix()
    except (ValueError, NotImplementedError):
        return False
    return source in source_files

def validate_payload(payload):
    payload = Path(payload).resolve()
    try:
        m = json.loads((payload / 'manifest.json').read_text(encoding='utf-8'))
        if m['version'] != VERSION or m['package'] != PACKAGE:
            raise ValueError('Release version does not match setup')
        files = m['files']
        if not set(REQUIRED).issubset(files):
            raise ValueError('Required Windows, Android or USB files are missing')
        actual = {p.relative_to(payload).as_posix() for p in payload.rglob('*')
                  if p.is_file() and p != payload / 'manifest.json'}
        expected = set(files)
        missing = expected - actual
        unexpected = {name for name in actual - expected
                      if not is_generated_bytecode(name, expected)}
        if missing or unexpected:
            details = []
            if missing:
                details.append('Missing files:\n' + '\n'.join(sorted(missing)[:12]))
            if unexpected:
                details.append('Unexpected files:\n' + '\n'.join(sorted(unexpected)[:12]))
            raise ValueError('Release file list does not match.\n' + '\n'.join(details))
        for relative, expected in files.items():
            path = (payload / relative).resolve()
            if not path.is_relative_to(payload): raise ValueError('Invalid payload path')
            if path.stat().st_size != expected['size'] or sha256(path) != expected['sha256']:
                raise ValueError('Damaged bundled file: ' + relative)
        for relative in ('app/GlyphLink.exe', 'platform-tools/adb.exe'):
            with (payload / relative).open('rb') as f:
                if f.read(2) != b'MZ': raise ValueError('Invalid Windows executable')
        with zipfile.ZipFile(payload / 'android/GlyphLink.apk') as apk:
            if not {'AndroidManifest.xml', 'classes.dex'}.issubset(apk.namelist()):
                raise ValueError('Invalid Android app')
            if apk.testzip() is not None: raise ValueError('Damaged Android app')
        return m
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as e:
        raise SetupError('This setup file is incomplete or damaged. Download the complete '
                         'GlyphLink installer again. Nothing has been installed.\n\n' + str(e)) from e

def parse_devices(output):
    rows = []
    for line in output.splitlines():
        words = line.split()
        if len(words) < 2 or words[1] not in {'device', 'offline', 'unauthorized'}: continue
        serial = words[0]
        if serial.startswith('emulator-') or ':' in serial or '_adb-tls-' in serial: continue
        rows.append((serial, words[1]))
    return rows

def install_error(output):
    if 'INSTALL_FAILED_UPDATE_INCOMPATIBLE' in output:
        return ('Android refused this update because the installed GlyphLink uses a different signing '
                'key. Your existing app and its data were kept.\n\n' + output)
    if 'INSTALL_FAILED_VERSION_DOWNGRADE' in output:
        return 'The phone already has a newer GlyphLink version.\n\n' + output
    if 'INSTALL_FAILED_USER_RESTRICTED' in output:
        return 'Unlock the phone and allow the installation prompt, then click Retry.\n\n' + output
    return 'Android could not install GlyphLink.\n\n' + output

class Installer:
    def __init__(self, payload, target, report, cancel=None, runner=run_command):
        self.payload, self.target = Path(payload), Path(target)
        self.report, self.runner = report, runner
        self.cancel = cancel or threading.Event()
        self.serial = None
        self.adb = self.payload / 'platform-tools/adb.exe'
        self.log_path = self.target.parent / 'GlyphLink-setup.log'

    def log(self, message):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open('a', encoding='utf-8') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S ') + str(message) + '\n')
        self.report('log', str(message))

    def check_cancel(self):
        if self.cancel.is_set(): raise Cancelled('Setup cancelled.')

    def progress(self, value, title, detail=''):
        self.report('progress', (value, title, detail))

    def step(self, key, value, title, detail):
        self.report('stage', key)
        self.progress(value, title, detail)

    def prepare(self, tools_cache):
        """Prepare verified USB tools without installing either application."""
        self.report('preflight', ('Checking included files', 'Verifying the Windows app, Android companion and USB tools.'))
        manifest = validate_payload(self.payload)
        self.check_cancel()
        tool_files = {name[len('platform-tools/'):]: info for name, info in manifest['files'].items()
                      if name.startswith('platform-tools/')}
        cache = Path(tools_cache) / manifest['files']['platform-tools/adb.exe']['sha256'][:16]
        valid = all((cache / name).is_file() and sha256(cache / name) == info['sha256']
                    for name, info in tool_files.items())
        self.report('preflight', ('Preparing the USB connection', 'Starting the included USB tools so setup can check your phone.'))
        if not valid:
            if cache.exists(): cache = cache.with_name(cache.name + '-' + uuid.uuid4().hex[:8])
            cache.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(self.payload / 'platform-tools', cache)
        # Keep the normal shared ADB server in a stable location outside NSIS's temporary directory.
        self.adb = cache / 'adb.exe'
        self.command([self.adb, 'start-server'], timeout=10)
        self.report('package_ready', True)

    def inspect_phone(self, expected_serial=None, samples=3):
        """Read-only checks. Never install or open an app here."""
        status = DeviceStatus('searching', 'Connect your Nothing Phone (1)',
                              'Use a USB data cable, turn on USB debugging and unlock the phone.')
        try:
            self.check_cancel()
            rows = parse_devices(self.command([self.adb, 'devices', '-l'], timeout=5))
            if not rows: return status
            if len(rows) > 1:
                status.state, status.title = 'multiple', 'Connect one phone'
                status.detail = 'Disconnect other Android phones so setup can check the correct device.'
                status.checks['usb'] = ('wait', 'More than one phone is connected')
                return status
            serial, state = rows[0]
            status.serial = serial
            status.checks['usb'] = ('pass', 'Phone detected over USB')
            if state == 'unauthorized':
                status.state, status.title = 'authorize', 'Tap Allow on your phone'
                status.detail = 'Unlock the phone and accept Allow USB debugging. The checks continue automatically.'
                status.checks['permission'] = ('wait', 'Waiting for your approval on the phone')
                return status
            if state != 'device':
                status.state, status.title = 'offline', 'Waiting for the phone to reconnect'
                status.detail = 'The cable is detected, but Android is offline. Unlock the phone and reconnect the USB cable.'
                status.checks['usb'] = ('wait', 'Phone is offline')
                return status
            status.checks['permission'] = ('pass', 'USB debugging is authorized')
            if expected_serial is not None and serial != expected_serial:
                status.state, status.title = 'changed', 'The connected phone changed'
                status.detail = 'Let setup check this phone, then click Install again.'
                return status
            self.serial = serial
            self.check_cancel()
            device = self.phone(['shell', 'getprop', 'ro.product.device'], timeout=4).strip().lower()
            model = self.phone(['shell', 'getprop', 'ro.product.model'], timeout=4).strip()
            status.model = model
            if 'spacewar' not in device and model.lower() not in {'a063', 'nothing phone (1)', 'phone (1)'}:
                status.state, status.title = 'wrong_device', 'This phone is not supported'
                status.detail = 'This GlyphLink version is for Nothing Phone (1). Connect that phone to continue.'
                status.checks['android'] = ('fail', model or device or 'Different Android device')
                return status
            sdk = self.phone(['shell', 'getprop', 'ro.build.version.sdk'], timeout=4).strip()
            if not sdk.isdigit() or int(sdk) < 31:
                status.state, status.title = 'old_android', 'Android 12 or later is required'
                status.detail = 'The phone is connected, but its Android version is not supported by this companion.'
                status.checks['android'] = ('fail', 'Android version is not supported')
                return status
            if self.phone(['shell', 'getprop', 'sys.boot_completed'], timeout=4).strip() != '1':
                status.state, status.title = 'booting', 'Waiting for Android to finish starting'
                status.detail = 'The USB connection is available. Wait for the phone to reach its home screen.'
                status.checks['android'] = ('wait', 'Android is still starting')
                return status
            release = self.phone(['shell', 'getprop', 'ro.build.version.release'], timeout=4).strip()
            status.model, status.android = 'Nothing Phone (1)', release or ('API ' + sdk)
            status.checks['android'] = ('pass', 'Nothing Phone (1) / Android ' + status.android)
            self.check_cancel()
            check = self.runner([self.adb, '-s', serial, 'shell', 'pm', 'path', 'com.nothing.thirdparty'], timeout=5)
            if check.returncode or not any(line.startswith('package:') for line in (check.stdout or '').splitlines()):
                status.state, status.title = 'glyph_missing', 'The required Glyph service was not found'
                status.detail = 'This companion needs the Glyph adapter used by your Evolution X setup. The USB connection is working.'
                status.checks['glyph'] = ('fail', 'Required Glyph adapter is missing or unavailable')
                return status
            status.checks['glyph'] = ('pass', 'Required Glyph adapter is installed')
            for _ in range(samples):
                self.check_cancel()
                token = 'GLYPHLINK_CHECK_' + uuid.uuid4().hex
                if self.phone(['shell', 'echo', token], timeout=4).strip() != token:
                    raise SetupError('The phone did not return the expected USB response.')
            if self.phone(['get-state'], timeout=4).strip() != 'device':
                raise SetupError('The phone disconnected during the check.')
            status.checks['response'] = ('pass', str(samples) + ' of ' + str(samples) + ' USB replies received')
            status.state, status.title, status.ready = 'ready', 'Your phone is ready', True
            status.detail = 'All connection checks passed. Click Install GlyphLink to install both apps.'
            return status
        except Cancelled:
            raise
        except (SetupError, OSError, subprocess.TimeoutExpired) as exc:
            status.state, status.title, status.ready = 'unresponsive', 'The phone is not responding yet', False
            status.detail = 'Keep it unlocked and reconnect the USB cable. Setup will check again automatically.'
            status.checks['response'] = ('wait', 'Waiting for a complete USB response')
            self.report('diagnostic', str(exc))
            return status

    def command(self, args, timeout=30):
        r = self.runner(args, timeout=timeout)
        out = ((r.stdout or '') + '\n' + (r.stderr or '')).strip()
        if r.returncode: raise SetupError(out or 'A setup command could not finish.')
        return out

    def phone(self, args, timeout=30):
        return self.command([self.adb, '-s', self.serial] + list(args), timeout)

    def wait_for_phone(self, timeout=600, poll=1):
        self.command([self.adb, 'start-server'], timeout=10)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.inspect_phone()
            self.report('device', status.to_dict())
            self.progress(.02, status.title, status.detail)
            if status.ready: return status.serial
            if status.state in {'wrong_device', 'old_android', 'glyph_missing'}:
                raise DeviceNotReady(status)
            self.cancel.wait(poll)
        raise SetupError('The phone has not connected yet. Check the on-screen steps and try again.')

    def install_android(self):
        self.check_cancel()
        self.step('android', .48, 'Installing the Android companion',
                  'Sending the included app to your phone. Existing app data is kept. Allow an installation prompt if Android shows one.')
        r = self.runner([self.adb, '-s', self.serial, 'install', '-r', '-g',
                         self.payload / 'android/GlyphLink.apk'], timeout=180)
        out = ((r.stdout or '') + '\n' + (r.stderr or '')).strip()
        self.log(out)
        if r.returncode or 'Success' not in out.splitlines(): raise SetupError(install_error(out))
        installed = self.phone(['shell', 'pm', 'path', PACKAGE])
        if not any(x.startswith('package:') for x in installed.splitlines()):
            raise SetupError('Android did not confirm the GlyphLink installation.')
        self.progress(.62, 'Opening GlyphLink on your phone',
                      'Android confirmed the installation. Opening the companion so it can accept the USB connection.')
        opened = self.phone(['shell', 'am', 'start', '-W', '-n', ACTIVITY])
        if 'Error:' in opened or 'Exception' in opened:
            raise SetupError('The companion installed, but Android could not open it.\n\n' + opened)

    def verify_companion(self, timeout=30):
        self.step('link', .72, 'Testing the app connection',
                  'Asking the Android companion to reply through the USB cable. Setup waits for its response before continuing.')
        port_text = self.phone(['forward', 'tcp:0', 'tcp:47999']).strip()
        if not port_text.isdigit(): raise SetupError('Could not create the USB connection for verification.')
        port, deadline = int(port_text), time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                self.check_cancel()
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=2) as conn:
                        conn.settimeout(2)
                        conn.sendall(b'PING\n')
                        reply = b''
                        while b'\n' not in reply and len(reply) < 64:
                            part = conn.recv(64)
                            if not part: break
                            reply += part
                        if reply.strip() == b'PONG':
                            self.log('Android companion replied PONG over USB.')
                            return
                except OSError: pass
                self.cancel.wait(.5)
            raise SetupError('The phone app installed but did not respond. Open GlyphLink on the phone, keep it unlocked, then click Retry.')
        finally:
            try: self.phone(['forward', '--remove', 'tcp:' + str(port)])
            except SetupError: pass

    def powershell(self, script):
        encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
        return self.command(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded])

    def stop_previous_app(self):
        folder = str(self.target.resolve()).replace("'", "''") + '\\'
        self.powershell("$ErrorActionPreference='Stop'; $root='" + folder + "'; "
                        "Get-CimInstance Win32_Process | Where-Object { "
                        "$_.Name -in @('GlyphLink.exe','GlyphLinkDebug.exe','pythonw.exe','python.exe') -and $_.ExecutablePath -and "
                        "$_.ExecutablePath.StartsWith($root,[StringComparison]::OrdinalIgnoreCase) "
                        "} | ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction Stop }")

    def stage_windows(self):
        self.step('windows', .18, 'Copying the Windows app',
                  'Installing GlyphLink and its included runtime for your Windows account. Your saved patterns are kept.')
        directory = self.target / 'versions' / (VERSION + '-' + uuid.uuid4().hex[:8])
        try:
            shutil.copytree(self.payload / 'app', directory,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            shutil.copytree(self.payload / 'platform-tools', directory / 'platform-tools')
            shutil.copy2(self.payload / 'android/GlyphLink.apk', directory / 'GlyphLink.apk')
            return directory
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def activate_windows(self, directory):
        self.step('open', .84, 'Opening GlyphLink on Windows',
                  'Starting the desktop app and waiting for its window to be ready.')
        exe = directory / 'GlyphLink.exe'
        ready = directory / ('setup-ready-' + uuid.uuid4().hex + '.json')
        process = subprocess.Popen([str(exe), '--setup-ready-file', str(ready)], cwd=str(directory), **hidden_kwargs())
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not ready.exists() and process.poll() is None: time.sleep(.2)
        if not ready.exists() or process.poll() is not None:
            raise SetupError('The phone app installed, but the Windows app could not open. See View details and the GlyphLink crash log.')
        ready.unlink(missing_ok=True)
        self.step('finish', .94, 'Adding the finishing touches',
                  'Creating Desktop and Start-menu shortcuts, and enabling GlyphLink to start with Windows.')
        target, workdir = str(exe).replace("'", "''"), str(directory).replace("'", "''")
        self.powershell("$ErrorActionPreference='Stop'; $ws=New-Object -ComObject WScript.Shell; "
                        "$folders=@([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs')); "
                        "foreach($folder in $folders){if($folder){$s=$ws.CreateShortcut((Join-Path $folder 'GlyphLink.lnk')); "
                        "$s.TargetPath='" + target + "';$s.WorkingDirectory='" + workdir + "';$s.IconLocation='" + target + "';$s.Save()}}")
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
            winreg.SetValueEx(key, 'GlyphLink', 0, winreg.REG_SZ, '"' + str(exe) + '" --background')
        (self.target / 'current.json').write_text(json.dumps({'version': VERSION, 'directory': str(directory)}), encoding='utf-8')
        self.log('Windows app opened: ' + str(exe))

    def install(self, expected_serial=None):
        self.step('check', .02, 'Rechecking the phone before installing',
                  'Confirming the same phone is still connected, authorized and responding. No app files are copied until this passes.')
        validate_payload(self.payload)
        self.check_cancel()
        status = self.inspect_phone(expected_serial=expected_serial)
        self.report('device', status.to_dict())
        if not status.ready: raise DeviceNotReady(status)
        self.log('GlyphLink setup ' + VERSION + '; files verified and phone readiness checks passed.')
        directory = self.stage_windows()
        self.check_cancel()
        self.progress(.36, 'Preparing the Windows app to start',
                      'Closing any previous GlyphLink session so the new desktop app can connect cleanly.')
        self.stop_previous_app()
        self.install_android()
        self.verify_companion()
        self.check_cancel()
        self.activate_windows(directory)
        self.progress(1, 'GlyphLink is ready', 'The Android companion replied over USB, the Windows app opened, and your shortcuts are ready.')
        return directory
