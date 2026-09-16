"""Guided setup with automatic phone checks before installing either app."""
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import traceback
import customtkinter as ctk
from installer_core import CHECK_LABELS, Installer, VERSION, validate_payload
from setup_controller import SetupController

BG, CARD, PANEL = '#0d1017', '#171d28', '#202838'
TEXT, MUTED, BLUE, GREEN, AMBER, RED = '#f3f5fb', '#abb5c7', '#99bbff', '#88dbb0', '#f0c782', '#ff9d9d'
STAGES = (
    ('check', 'Recheck your phone', 'Check the same phone again before installing.'),
    ('windows', 'Install the Windows app', 'Copy the desktop app and its included runtime.'),
    ('android', 'Install the phone companion', 'Install or update GlyphLink, then open it on the phone.'),
    ('link', 'Test the app connection', 'Wait for the Android companion to reply over USB.'),
    ('open', 'Open GlyphLink on Windows', 'Check that the desktop app opens successfully.'),
    ('finish', 'Create shortcuts and finish', 'Add Desktop and Start-menu shortcuts and Windows startup.'),
)

def payload_dir(): return Path(__file__).resolve().parent / 'payload'

class Setup(ctk.CTk):
    def __init__(self, qa=False):
        super().__init__()
        self.title('GlyphLink Setup ' + VERSION)
        self.geometry('1000x790'); self.minsize(820, 620); self.configure(fg_color=BG)
        self.events, self.cancel = queue.Queue(), threading.Event()
        self.phase = 'check'; self.worker_done = qa
        self.finished = self.fatal = self.closing = False
        self.latest = self.controller = self.details_box = None
        self.last_error = self.last_diagnostic = ''
        self.logs, self.wraps, self.containers = [], [], set()
        self.build_ui()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(70, self.drain)
        if not qa:
            base = Path(os.environ['LOCALAPPDATA'])
            engine = Installer(payload_dir(), base / 'Programs/GlyphLink', self.emit, self.cancel)
            self.controller = SetupController(engine, base / 'GlyphLink/usb-tools', self.emit)
            threading.Thread(target=self.controller.run, daemon=False).start()

    def emit(self, kind, data): self.events.put((kind, data))

    def label(self, parent, text, size=13, color=TEXT, bold=False, wrap=False):
        label = ctk.CTkLabel(parent, text=text, height=0, anchor='w', justify='left', text_color=color,
            font=ctk.CTkFont(family='Segoe UI' if os.name == 'nt' else 'DejaVu Sans',
                            size=size, weight='bold' if bold else 'normal'), wraplength=340 if wrap else 0)
        if wrap:
            self.wraps.append((label, parent))
            if parent not in self.containers:
                parent.bind('<Configure>', lambda event: self.after_idle(self.rewrap), add='+')
                self.containers.add(parent)
        return label

    def rewrap(self):
        for label, parent in self.wraps:
            if parent.winfo_width() > 50:
                label.configure(wraplength=max(140, label._reverse_widget_scaling(parent.winfo_width()) - 46))

    def build_ui(self):
        header = ctk.CTkFrame(self, fg_color='transparent'); header.pack(fill='x', padx=30, pady=(24, 18))
        bar = ctk.CTkFrame(header, fg_color='transparent'); bar.pack(fill='x')
        self.label(bar, 'GlyphLink', 29, bold=True).pack(side='left')
        self.label(bar, 'SETUP ' + VERSION, 11, MUTED).pack(side='right')
        self.label(header, 'Your Windows audio. Your phone’s Glyph lights.', 13, MUTED).pack(anchor='w', pady=(7, 18))
        self.nav = self.label(header, '1  Connect & check     /     2  Install both apps     /     3  Ready', 12, BLUE)
        self.nav.pack(fill='x')
        self.banner = self.label(header, '', color=RED, wrap=True)
        self.body = ctk.CTkScrollableFrame(self, fg_color=BG, scrollbar_button_color=PANEL)
        self.body.pack(fill='both', expand=True, padx=22)
        self.check_panel = ctk.CTkFrame(self.body, fg_color='transparent'); self.check_panel.pack(fill='both', expand=True)
        self.check_panel.grid_columnconfigure(0, weight=3, minsize=350)
        self.check_panel.grid_columnconfigure(1, weight=2, minsize=290)
        phone = ctk.CTkFrame(self.check_panel, fg_color=CARD, corner_radius=18)
        phone.grid(row=0, column=0, sticky='nsew', padx=(0, 14))
        self.label(phone, 'LIVE PHONE CHECK', 11, BLUE, True).pack(anchor='w', padx=22, pady=(22, 14))
        self.phone_title = self.label(phone, 'Getting setup ready', 23, bold=True, wrap=True)
        self.phone_title.pack(fill='x', padx=22)
        self.phone_detail = self.label(phone, 'Checking the included apps and preparing the USB tools.', color=MUTED, wrap=True)
        self.phone_detail.pack(fill='x', padx=22, pady=(10, 14))
        self.badge = self.label(phone, 'Waiting for a phone', 12, MUTED); self.badge.pack(anchor='w', padx=22, pady=(0, 15))
        self.check_rows = {}
        for key, title in CHECK_LABELS.items():
            row = ctk.CTkFrame(phone, fg_color='transparent'); row.pack(fill='x', padx=22, pady=9)
            mark = self.label(row, '○', 18, MUTED); mark.pack(side='left', anchor='n', padx=(0, 12))
            content = ctk.CTkFrame(row, fg_color='transparent'); content.pack(side='left', fill='x', expand=True)
            self.label(content, title, bold=True).pack(fill='x')
            detail = self.label(content, 'Waiting to check', 12, MUTED, wrap=True); detail.pack(fill='x', pady=(4, 0))
            self.check_rows[key] = (mark, detail)
        self.package_label = self.label(phone, 'Included files: checking…', 12, MUTED, wrap=True)
        self.package_label.pack(fill='x', padx=22, pady=(16, 10))
        self.label(phone, 'These checks confirm USB communication and required Android services. The app connection is tested after installation.',
                   11, MUTED, wrap=True).pack(fill='x', padx=22, pady=(0, 22))
        help_card = ctk.CTkFrame(self.check_panel, fg_color=CARD, corner_radius=18); help_card.grid(row=0, column=1, sticky='nsew')
        self.label(help_card, 'Get your phone ready', 19, bold=True).pack(anchor='w', padx=20, pady=(22, 18))
        for title, detail in (
            ('1  Enable Developer options', 'Settings → About phone → Software info → Build number. Tap 7 times and enter your PIN if asked. On Evolution X, Build number may be directly under About phone.'),
            ('2  Turn on USB debugging', 'Settings → System → Developer options → USB debugging. Enable it and confirm the phone’s prompt.'),
            ('3  Connect and tap Allow', 'Use a USB data cable and keep the phone unlocked. Accept Allow USB debugging on the phone. Setup checks again automatically.'),
        ):
            self.label(help_card, title, bold=True, wrap=True).pack(fill='x', padx=20, pady=(10, 7))
            self.label(help_card, detail, 12, MUTED, wrap=True).pack(fill='x', padx=20, pady=(0, 14))
        self.label(help_card, 'Both apps and USB tools are included. Setup creates shortcuts and starts GlyphLink with Windows. Your saved patterns are kept.',
                   12, MUTED, wrap=True).pack(fill='x', padx=20, pady=(14, 22))
        self.install_panel = ctk.CTkFrame(self.body, fg_color=CARD, corner_radius=18)
        self.label(self.install_panel, 'INSTALLATION PROGRESS', 11, BLUE, True).pack(anchor='w', padx=26, pady=(24, 15))
        self.install_title = self.label(self.install_panel, 'Preparing to install', 24, bold=True, wrap=True); self.install_title.pack(fill='x', padx=26)
        self.install_detail = self.label(self.install_panel, '', 14, MUTED, wrap=True); self.install_detail.pack(fill='x', padx=26, pady=(10, 19))
        self.progress = ctk.CTkProgressBar(self.install_panel, progress_color=BLUE, fg_color=PANEL, height=7)
        self.progress.set(0); self.progress.pack(fill='x', padx=26)
        self.next_label = self.label(self.install_panel, '', 12, MUTED, wrap=True); self.next_label.pack(fill='x', padx=26, pady=(12, 19))
        self.stage_rows = {}
        for key, title, detail in STAGES:
            heading = self.label(self.install_panel, '○  ' + title, 13, MUTED, True); heading.pack(fill='x', padx=26, pady=(10, 4))
            self.label(self.install_panel, detail, 12, MUTED, wrap=True).pack(fill='x', padx=46, pady=(0, 10))
            self.stage_rows[key] = heading
        self.label(self.install_panel, 'Keep the phone connected until setup finishes.', 12, MUTED).pack(anchor='w', padx=26, pady=(17, 24))
        footer = ctk.CTkFrame(self, fg_color=BG); footer.pack(fill='x', padx=30, pady=(15, 22))
        self.footer_note = self.label(footer, 'Checking your phone before installation…', 12, MUTED, wrap=True)
        self.footer_note.pack(fill='x', pady=(0, 13))
        buttons = ctk.CTkFrame(footer, fg_color='transparent'); buttons.pack(fill='x')
        ctk.CTkButton(buttons, text='View details', command=self.show_details, width=115, fg_color=PANEL).pack(side='left')
        self.button = ctk.CTkButton(buttons, text='Waiting for phone', command=self.start, state='disabled', width=210, height=42,
                                    fg_color=BLUE, hover_color='#b7d0ff', text_color='#101a2b', text_color_disabled='#7c899f')
        self.button.pack(side='right')
        self.cancel_button = ctk.CTkButton(buttons, text='Cancel', command=self.close, width=85, fg_color='transparent', hover_color=PANEL)
        self.cancel_button.pack(side='right', padx=12)

    def switch_panel(self, phase):
        self.phase = phase; self.check_panel.pack_forget(); self.install_panel.pack_forget()
        (self.check_panel if phase == 'check' else self.install_panel).pack(fill='both', expand=True)
        self.nav.configure(text={'check': '1  Connect & check     /     2  Install both apps     /     3  Ready',
                                'install': '✓  Phone checked     /     2  Installing both apps     /     3  Ready',
                                'done': '✓  Phone checked     /     ✓  Both apps installed     /     ✓  Ready'}[phase])
        self.after_idle(self.rewrap)

    def render_device(self, data):
        self.latest = data; ready = data['ready']
        self.phone_title.configure(text=data['title'], text_color=GREEN if ready else TEXT)
        self.phone_detail.configure(text=data['detail'])
        self.badge.configure(text=(data['model'] + ('  /  Android ' + data['android'] if data['android'] else '')) if data['model'] else 'Waiting for a phone')
        for key, (mark, detail) in self.check_rows.items():
            state, text = data['checks'][key]
            color = {'pass': GREEN, 'wait': AMBER, 'fail': RED, 'pending': MUTED}[state]
            mark.configure(text={'pass': '✓', 'wait': '●', 'fail': '!', 'pending': '○'}[state], text_color=color)
            detail.configure(text=text, text_color=color)
        if self.phase == 'check' and not self.fatal and not self.closing:
            self.button.configure(state='normal' if ready else 'disabled',
                text=('Retry install' if self.last_error else 'Install GlyphLink') if ready else 'Waiting for phone')
            self.footer_note.configure(text='Phone checks passed. Ready to install both apps.' if ready else data['detail'],
                                       text_color=GREEN if ready else MUTED)

    def start(self):
        if self.finished or self.fatal: self.close(); return
        if self.phase != 'check' or not self.latest or not self.latest['ready']: return
        if self.controller is None or not self.controller.request_install(self.latest['serial']): return
        self.last_error = ''; self.banner.pack_forget(); self.switch_panel('install'); self.render_stage('check')
        self.button.configure(state='disabled', text='Installing…')
        self.footer_note.configure(text='Keep your phone connected and unlocked.', text_color=MUTED)

    def render_stage(self, key, done=False):
        index = [s[0] for s in STAGES].index(key)
        for i, (stage, title, _) in enumerate(STAGES):
            completed = done or i < index
            self.stage_rows[stage].configure(text=('✓  ' if completed else '●  ' if i == index else '○  ') + title,
                                             text_color=GREEN if completed else BLUE if i == index else MUTED)
        self.next_label.configure(text='All steps completed.' if done else
            'Next: ' + STAGES[index + 1][1] if index + 1 < len(STAGES) else 'Next: finish setup.')

    def append_log(self, text):
        line = time.strftime('%H:%M:%S  ') + str(text); self.logs.append(line)
        if self.details_box is not None and self.details_box.winfo_exists():
            self.details_box.configure(state='normal'); self.details_box.insert('end', line + '\n')
            self.details_box.see('end'); self.details_box.configure(state='disabled')

    def drain(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == 'device': self.render_device(data)
                elif kind == 'preflight':
                    self.phone_title.configure(text=data[0]); self.phone_detail.configure(text=data[1]); self.append_log(': '.join(data))
                elif kind == 'package_ready': self.package_label.configure(text='Included apps and USB tools verified.', text_color=GREEN)
                elif kind == 'stage': self.render_stage(data)
                elif kind == 'progress':
                    value, title, detail = data; self.progress.set(value)
                    self.install_title.configure(text=title); self.install_detail.configure(text=detail); self.append_log(title + ': ' + detail)
                elif kind == 'log': self.append_log(data)
                elif kind == 'diagnostic':
                    if data != self.last_diagnostic: self.append_log(data); self.last_diagnostic = data
                elif kind == 'installing': self.switch_panel('install'); self.button.configure(state='disabled', text='Installing…')
                elif kind == 'done':
                    self.finished = True; self.switch_panel('done'); self.render_stage('finish', done=True)
                    self.button.configure(state='normal', text='Finish'); self.cancel_button.configure(state='disabled')
                    self.footer_note.configure(text='GlyphLink is installed and open. You can close setup.', text_color=GREEN)
                elif kind == 'install_error':
                    self.last_error = str(data); self.switch_panel('check')
                    self.banner.configure(text='Installation stopped: ' + str(data).split('\n\n')[0]); self.banner.pack(fill='x', pady=(12, 0))
                    self.button.configure(state='disabled', text='Checking phone…')
                    self.footer_note.configure(text='Checking the phone again before you retry.', text_color=AMBER)
                elif kind == 'fatal':
                    self.fatal = True; self.phone_title.configure(text='Setup could not get ready', text_color=RED)
                    self.phone_detail.configure(text=str(data).split('\n\n')[0])
                    self.footer_note.configure(text='Open View details for the full error.', text_color=RED); self.button.configure(state='normal', text='Close')
                elif kind == 'worker_stopped': self.worker_done = True
                elif kind == 'cancelled': self.append_log('Setup cancelled.')
        except queue.Empty: pass
        if self.closing and self.worker_done: self.destroy(); return
        self.after(70, self.drain)

    def show_details(self):
        dialog = ctk.CTkToplevel(self); dialog.title('GlyphLink setup details'); dialog.geometry('820x500')
        self.details_box = ctk.CTkTextbox(dialog, wrap='word'); self.details_box.pack(fill='both', expand=True, padx=18, pady=18)
        self.details_box.insert('end', '\n'.join(self.logs)); self.details_box.configure(state='disabled')
        copy = ctk.CTkButton(dialog, text='Copy details', command=lambda: self.copy_details(copy))
        copy.pack(anchor='e', padx=18, pady=(0, 16)); dialog.after(100, dialog.lift)

    def copy_details(self, button):
        self.clipboard_clear(); self.clipboard_append('\n'.join(self.logs)); button.configure(text='Copied')

    def close(self):
        if self.worker_done: self.destroy(); return
        self.closing = True; self.button.configure(state='disabled', text='Closing…'); self.cancel_button.configure(state='disabled')
        self.footer_note.configure(text='Finishing the current USB operation, then closing setup.', text_color=MUTED)
        if self.controller: self.controller.stop()

def main():
    if '--verify-payload' in sys.argv:
        m = validate_payload(payload_dir()); result = json.dumps({'version': VERSION, 'files': len(m['files']), 'valid': True})
        index = sys.argv.index('--verify-payload')
        if len(sys.argv) > index + 1: Path(sys.argv[index + 1]).write_text(result, encoding='utf-8')
        elif sys.stdout is not None: print(result)
        return
    if os.name != 'nt': raise RuntimeError('Run the complete GlyphLink setup EXE on 64-bit Windows.')
    ctk.set_appearance_mode('dark'); ctk.set_default_color_theme('blue'); Setup().mainloop()

if __name__ == '__main__':
    try: main()
    except Exception:
        details = traceback.format_exc()
        destination = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'GlyphLink-setup-startup.log'
        try:
            destination.write_text(details, encoding='utf-8'); message = 'GlyphLink Setup could not open. Error details:\n' + str(destination)
        except OSError: message = 'GlyphLink Setup could not open.\n\n' + details[-1500:]
        if os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, 'GlyphLink Setup', 0x10)
        elif sys.stderr is not None: print(details, file=sys.stderr)
        raise SystemExit(1)
