"""Exercise the real Tk UI with simulated phone events; no ADB or installs."""
import json
import os
from pathlib import Path
import sys
import time
from PIL import ImageGrab
sys.path.insert(0, str(Path(sys.argv[2]) if len(sys.argv)>2 else Path(__file__).resolve().parents[1]/'installer'))
from glyphlink_setup import Setup, ctk
from installer_core import DeviceStatus, CHECK_LABELS, VERSION
output=Path(sys.argv[1]); output.mkdir(parents=True,exist_ok=True)
ctk.set_appearance_mode('dark'); ctk.set_default_color_theme('blue')
app=Setup(qa=True); errors=[]
app.report_callback_exception=lambda *args:errors.append(str(args))
def settle():
    for _ in range(8): app.update(); time.sleep(.035)
    if errors: raise AssertionError(errors)
def snapshot(name):
    settle()
    ImageGrab.grab(xdisplay=os.environ.get('DISPLAY')).crop((app.winfo_rootx(),app.winfo_rooty(),
        app.winfo_rootx()+app.winfo_width(),app.winfo_rooty()+app.winfo_height())).save(output/(name+'.png'))
def footer_visible():
    assert app.button.winfo_ismapped()
    y=app.button.winfo_rooty()-app.winfo_rooty()
    assert 0<=y and y+app.button.winfo_height()<=app.winfo_height()
try:
    settle(); assert app.button.cget('state')=='disabled'
    status=DeviceStatus('authorize','Tap Allow on your phone','Unlock the phone and accept Allow USB debugging.',serial='qa-phone')
    status.checks['usb']=('pass','Phone detected over USB')
    status.checks['permission']=('wait','Waiting for your approval on the phone')
    app.emit('device',status.to_dict()); settle(); assert app.button.cget('state')=='disabled'
    ready=DeviceStatus('ready','Your phone is ready','All connection checks passed. Click Install GlyphLink to install both apps.',
                       ready=True,serial='qa-phone',model='Nothing Phone (1)',android='15')
    ready.checks={k:('pass',d) for k,d in zip(CHECK_LABELS,['Phone detected over USB','USB debugging is authorized',
                    'Nothing Phone (1) / Android 15','Required Glyph adapter is installed','3 of 3 USB replies received'])}
    app.emit('package_ready',True); app.emit('device',ready.to_dict()); settle()
    assert app.button.cget('state')=='normal'; footer_visible(); snapshot('ready')
    app.geometry('820x620'); settle(); footer_visible(); snapshot('small-window')
    app.emit('device',DeviceStatus('searching','Connect your Nothing Phone (1)','Reconnect the USB cable.').to_dict())
    settle(); assert app.button.cget('state')=='disabled'
    app.geometry('1000x790'); app.emit('device',ready.to_dict()); settle()
    class Controller:
        def request_install(self,serial): return serial=='qa-phone'
    app.controller=Controller(); app.button.invoke(); settle()
    assert app.phase=='install' and app.button.cget('state')=='disabled'
    app.emit('stage','android'); app.emit('progress',(.48,'Installing the Android companion',
        'Sending the included app to your phone. Existing app data is kept. Allow an installation prompt if Android shows one.'))
    settle(); assert 'Test the app connection' in app.next_label.cget('text'); footer_visible(); snapshot('installing')
    app.emit('install_error','The phone disconnected. Reconnect it and retry.\n\n'+'Technical details '*100)
    settle(); assert app.phase=='check' and app.button.cget('state')=='disabled'
    assert 'Technical details' not in app.banner.cget('text'); footer_visible()
    app.emit('device',ready.to_dict()); settle(); assert app.button.cget('text')=='Retry install'
    app.button.invoke(); app.emit('progress',(1,'GlyphLink is ready','Both apps are installed and the companion replied.'))
    app.emit('done','simulated'); settle(); assert app.phase=='done' and app.button.cget('text')=='Finish'; footer_visible()
    (output/'ui-check.json').write_text(json.dumps({'version':VERSION,'simulated_events':True,'install_button_gated':True,
        'disconnect_disables_install':True,'retry_flow':True,'progress_stages':True,'footer_visible_at_minimum_size':True,
        'callback_errors':errors,'windows_execution_tested':os.name=='nt'},indent=2))
    print('GUI checks passed.',flush=True)
finally: app.destroy()
