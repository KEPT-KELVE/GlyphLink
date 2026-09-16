import os
import sys
import time
import json
import copy
import socket
import shutil
import subprocess
import threading
from pathlib import Path
import tkinter as tk

import customtkinter as ctk
import numpy as np
import pystray
try:
    import soundcard as sc
    SOUNDCARD_IMPORT_ERROR = None
except Exception as _soundcard_exc:
    sc = None
    SOUNDCARD_IMPORT_ERROR = repr(_soundcard_exc)
from PIL import Image, ImageDraw, ImageTk

APP_NAME = "GlyphLink"
PORT = 47999
RATE = 48000
BLOCK = 1024
RECORDER_BUFFER = 4096
HOLD = 0.090

NORMAL = 1900
STRONG = 2850
MAXISH = 3500
SLAM = 4095
MANUAL_LEVEL = 3300

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

app_alive = True
phone_connected = False
sock = None
sock_lock = threading.RLock()
connection_failures = 0

audio_running = True
audio_thread = None
music_mode = "balanced"
active_mode = "Music"
tabs_widget = None

root = None
tray_icon = None
status_label = None
device_label = None
battery_label = None
charging_label = None
usb_label = None
music_button = None
startup_var = None

status_text = "Waiting for phone"
device_text = "No device"
battery_text = "--%"
charging_text = "Unknown"
usb_text = "Disconnected"

manual_state = [0, 0, 0, 0, 0]
pattern_edit_state = [0, 0, 0, 0, 0]
manual_preview = None
pattern_preview = None
pattern_timeline = None
manual_glyph_buttons = []
pattern_glyph_buttons = []

pattern_steps = []
selected_step = None
pattern_list_frame = None
on_var = None
wait_var = None
pattern_summary = None
loop_button = None
record_button = None
recording_taps = False
pattern_loop_running = False
pattern_loop_thread = None
pattern_play_once_running = False
pattern_play_thread = None

ON_OPTIONS = ["60 ms", "80 ms", "100 ms", "150 ms", "250 ms", "500 ms", "1 s"]
WAIT_OPTIONS = ["0 ms", "100 ms", "250 ms", "500 ms", "1 s", "1.5 s", "2 s"]

MAX_PATTERNS = 15
patterns = []
active_pattern_index = 0
pattern_selector = None
pattern_selector_var = None
pattern_storage_label = None

def app_data_dir():
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / APP_NAME
    return Path.home() / ".config" / APP_NAME

def pattern_store_path():
    return app_data_dir() / ".glyphlink_beats.json"

def _hide_windows_path(path):
    if os.name != "nt":
        return
    try:
        subprocess.run(["attrib", "+h", str(path)], capture_output=True, **hidden_kwargs())
    except Exception:
        pass

def _safe_step(step):
    try:
        frame = [int(np.clip(int(v), 0, 4095)) for v in list(step.get("frame", []))[:5]]
        frame += [0] * (5 - len(frame))
        return {
            "frame": frame,
            "on": max(0.02, float(step.get("on", 0.1))),
            "wait": max(0.0, float(step.get("wait", 0.1))),
        }
    except Exception:
        return None

def _unique_pattern_name(base):
    base = (base or "Pattern").strip() or "Pattern"
    existing = {p.get("name", "").casefold() for p in patterns}
    if base.casefold() not in existing:
        return base
    for i in range(2, 100):
        candidate = f"{base} {i}"
        if candidate.casefold() not in existing:
            return candidate
    return f"{base} {int(time.time())}"

def load_pattern_store():
    global patterns, active_pattern_index, pattern_steps, selected_step, pattern_edit_state
    path = pattern_store_path()
    loaded = None
    try:
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        loaded = None

    parsed = []
    if isinstance(loaded, dict):
        for item in loaded.get("patterns", [])[:MAX_PATTERNS]:
            if not isinstance(item, dict):
                continue
            steps = []
            for raw in item.get("steps", []):
                clean = _safe_step(raw)
                if clean:
                    steps.append(clean)
            parsed.append({
                "name": str(item.get("name") or f"Pattern {len(parsed)+1}")[:40],
                "steps": steps,
            })

    patterns = parsed or [{"name": "Pattern 1", "steps": []}]
    try:
        active_pattern_index = int(loaded.get("active", 0)) if isinstance(loaded, dict) else 0
    except Exception:
        active_pattern_index = 0
    active_pattern_index = max(0, min(active_pattern_index, len(patterns)-1))
    pattern_steps = copy.deepcopy(patterns[active_pattern_index]["steps"])
    selected_step = 0 if pattern_steps else None
    pattern_edit_state = pattern_steps[0]["frame"][:] if pattern_steps else [0, 0, 0, 0, 0]

def save_pattern_store(update_active=True):
    global patterns
    if not patterns:
        patterns = [{"name": "Pattern 1", "steps": []}]
    if update_active and 0 <= active_pattern_index < len(patterns):
        patterns[active_pattern_index]["steps"] = copy.deepcopy(pattern_steps)
    data = {
        "version": 1,
        "active": active_pattern_index,
        "patterns": patterns[:MAX_PATTERNS],
    }
    path = pattern_store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)
        _hide_windows_path(path)
        if pattern_storage_label is not None:
            try:
                pattern_storage_label.configure(text=f"Autosaved • {len(patterns)}/{MAX_PATTERNS} patterns")
            except Exception:
                pass
        return True
    except Exception:
        if pattern_storage_label is not None:
            try:
                pattern_storage_label.configure(text="Could not save patterns")
            except Exception:
                pass
        return False

def refresh_pattern_selector():
    if pattern_selector is None:
        return
    names = [p["name"] for p in patterns]
    pattern_selector.configure(values=names)
    if pattern_selector_var is not None and names:
        pattern_selector_var.set(names[active_pattern_index])

def switch_pattern(name):
    global active_pattern_index, pattern_steps, selected_step, pattern_edit_state, recording_taps
    if not patterns:
        return
    save_pattern_store(update_active=True)
    idx = next((i for i, p in enumerate(patterns) if p["name"] == name), None)
    if idx is None:
        return
    stop_pattern_playback(restore_preview=False)
    recording_taps = False
    if record_button is not None:
        try:
            record_button.configure(text="Record Taps", fg_color="#222222", text_color="#FFFFFF")
        except Exception:
            pass
    active_pattern_index = idx
    pattern_steps = copy.deepcopy(patterns[idx]["steps"])
    selected_step = 0 if pattern_steps else None
    pattern_edit_state = pattern_steps[0]["frame"][:] if pattern_steps else [0, 0, 0, 0, 0]
    if pattern_steps and on_var is not None and wait_var is not None:
        on_var.set(option_from_seconds(pattern_steps[0]["on"], ON_OPTIONS))
        wait_var.set(option_from_seconds(pattern_steps[0]["wait"], WAIT_OPTIONS))
    refresh_previews()
    refresh_step_list()
    refresh_pattern_selector()
    save_pattern_store(update_active=False)

def create_pattern():
    global active_pattern_index, pattern_steps, selected_step, pattern_edit_state
    if len(patterns) >= MAX_PATTERNS:
        from tkinter import messagebox
        messagebox.showinfo("GlyphLink", f"You can keep up to {MAX_PATTERNS} patterns.")
        return
    dialog = ctk.CTkInputDialog(text="Name your new pattern:", title="New Pattern")
    value = dialog.get_input()
    if value is None:
        return
    save_pattern_store(update_active=True)
    name = _unique_pattern_name(value or f"Pattern {len(patterns)+1}")[:40]
    patterns.append({"name": name, "steps": []})
    active_pattern_index = len(patterns) - 1
    pattern_steps = []
    selected_step = None
    pattern_edit_state = [0, 0, 0, 0, 0]
    refresh_pattern_selector()
    refresh_previews()
    refresh_step_list()
    save_pattern_store()

def rename_pattern():
    if not patterns:
        return
    current = patterns[active_pattern_index]["name"]
    dialog = ctk.CTkInputDialog(text=f"Rename '{current}' to:", title="Rename Pattern")
    value = dialog.get_input()
    if value is None or not value.strip():
        return
    requested = value.strip()[:40]
    if requested.casefold() != current.casefold():
        requested = _unique_pattern_name(requested)[:40]
    patterns[active_pattern_index]["name"] = requested
    refresh_pattern_selector()
    save_pattern_store()

def delete_pattern():
    global active_pattern_index, pattern_steps, selected_step, pattern_edit_state
    from tkinter import messagebox
    if len(patterns) <= 1:
        messagebox.showinfo("GlyphLink", "Keep at least one pattern. You can clear its timeline instead.")
        return
    name = patterns[active_pattern_index]["name"]
    if not messagebox.askyesno("Delete Pattern", f"Delete '{name}'?"):
        return
    patterns.pop(active_pattern_index)
    active_pattern_index = min(active_pattern_index, len(patterns)-1)
    pattern_steps = copy.deepcopy(patterns[active_pattern_index]["steps"])
    selected_step = 0 if pattern_steps else None
    pattern_edit_state = pattern_steps[0]["frame"][:] if pattern_steps else [0, 0, 0, 0, 0]
    refresh_pattern_selector()
    refresh_previews()
    refresh_step_list()
    save_pattern_store()


def parse_time_option(value):
    value = value.strip().lower()
    if value.endswith("ms"):
        return float(value[:-2].strip()) / 1000.0
    if value.endswith("s"):
        return float(value[:-1].strip())
    return 0.1


def hidden_kwargs():
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def find_adb():
    bundled = Path(sys.executable).resolve().parent / "platform-tools" / "adb.exe"
    if not bundled.is_file() and Path(sys.executable).parent.name == "runtime":
        bundled = Path(sys.executable).resolve().parents[1] / "platform-tools" / "adb.exe"
    if bundled.is_file():
        return str(bundled)
    found = shutil.which("adb")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    candidates = []
    if local:
        candidates.append(Path(local) / "GlyphLink" / "platform-tools" / "adb.exe")
        candidates.append(Path(local) / "Programs" / "GlyphLink" / "platform-tools" / "adb.exe")
        candidates.append(Path(local) / "Android" / "Sdk" / "platform-tools" / "adb.exe")
    app_dir = Path(sys.executable).resolve().parent
    candidates.append(app_dir / "platform-tools" / "adb.exe")
    candidates.append(Path.home() / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools" / "adb.exe")
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def run_adb(adb, args, timeout=6):
    return subprocess.run([adb] + args, capture_output=True, text=True, timeout=timeout, **hidden_kwargs())


def setup_forward():
    global device_text
    adb = find_adb()
    if not adb:
        raise RuntimeError("ADB not found")
    run_adb(adb, ["start-server"], timeout=8)
    p = run_adb(adb, ["devices"], timeout=5)
    devices = []
    for line in p.stdout.splitlines()[1:]:
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    if len(devices) != 1:
        raise RuntimeError("Need exactly one authorized USB-debugging phone")
    serial = devices[0]
    device_text = serial
    run_adb(adb, ["-s", serial, "forward", "--remove", f"tcp:{PORT}"], timeout=5)
    p = run_adb(adb, ["-s", serial, "forward", f"tcp:{PORT}", f"tcp:{PORT}"], timeout=5)
    if p.returncode != 0:
        raise RuntimeError("ADB forward failed")


def open_socket():
    s = socket.create_connection(("127.0.0.1", PORT), timeout=2.0)
    s.settimeout(2.0)
    s.sendall(b"PING\n")
    reply = b""
    while b"\n" not in reply:
        reply += s.recv(64)
    if reply.strip().upper() != b"PONG":
        s.close()
        raise RuntimeError("No PONG")
    s.settimeout(3.0)
    return s


def set_status(text):
    global status_text
    status_text = text
    if root and status_label:
        root.after(0, lambda: status_label.configure(text=status_text))
    if tray_icon:
        try:
            tray_icon.title = f"GlyphLink — {text}"
        except Exception:
            pass


def update_header():
    if not root:
        return
    if device_label:
        root.after(0, lambda: device_label.configure(text=device_text))
    if battery_label:
        root.after(0, lambda: battery_label.configure(text=battery_text))
    if charging_label:
        root.after(0, lambda: charging_label.configure(text=charging_text))
    if usb_label:
        root.after(0, lambda: usb_label.configure(text=usb_text))
    if music_button:
        root.after(0, lambda: music_button.configure(text=("Stop Music" if audio_running else "Start Music")))


def mark_disconnected():
    global sock, phone_connected, usb_text, connection_failures
    with sock_lock:
        try:
            if sock:
                sock.close()
        except Exception:
            pass
        sock = None
    phone_connected = False
    usb_text = "Disconnected"
    connection_failures = 0
    set_status("Waiting for phone")
    update_header()


def send_line(line):
    if not phone_connected or sock is None:
        raise ConnectionError("Phone not connected")
    with sock_lock:
        sock.sendall((line + "\n").encode("utf-8"))


def query_line(line):
    if not phone_connected or sock is None:
        raise ConnectionError("Phone not connected")
    with sock_lock:
        sock.sendall((line + "\n").encode("utf-8"))
        reply = b""
        while b"\n" not in reply:
            chunk = sock.recv(256)
            if not chunk:
                raise ConnectionError("Socket closed")
            reply += chunk
        return reply.decode("utf-8", errors="ignore").strip()


def send_frame(frame):
    vals = [int(np.clip(v, 0, 4095)) for v in frame]
    send_line("FRAME:" + ",".join(map(str, vals)))


def parse_status(line):
    global battery_text, charging_text, usb_text
    if not line.startswith("STATUS|"):
        return
    parts = {}
    for segment in line.split("|")[1:]:
        if "=" in segment:
            k, v = segment.split("=", 1)
            parts[k] = v
    battery = parts.get("battery", "-1")
    battery_text = f"{battery}%" if battery not in ("", "-1") else "--%"
    charging_text = "Charging" if parts.get("charging") == "1" else "Not charging"
    usb_text = parts.get("plug", "USB")
    update_header()


def connection_manager():
    global sock, phone_connected, usb_text, connection_failures
    while app_alive:
        if not phone_connected:
            try:
                setup_forward()
                s = open_socket()
                with sock_lock:
                    sock = s
                phone_connected = True
                connection_failures = 0
                usb_text = "USB / ADB"
                set_status("Connected")
                update_header()
                try:
                    send_frame([2200] * 5)
                    time.sleep(0.10)
                    send_frame([0] * 5)
                except Exception:
                    mark_disconnected()
            except Exception:
                set_status("Waiting for phone")
                usb_text = "Disconnected"
                update_header()
                time.sleep(1.25)
                continue
        time.sleep(0.5)


def heartbeat_manager():
    global connection_failures
    while app_alive:
        time.sleep(1.5)
        if not phone_connected:
            continue
        try:
            reply = query_line("HEARTBEAT")
            if reply != "PONG":
                raise ConnectionError("Bad heartbeat")
            connection_failures = 0
        except Exception:
            connection_failures += 1
            if connection_failures >= 2:
                mark_disconnected()


def phone_status_manager():
    global connection_failures
    while app_alive:
        time.sleep(3.0)
        if not phone_connected:
            continue
        try:
            parse_status(query_line("STATUS"))
            connection_failures = 0
        except Exception:
            connection_failures += 1
            if connection_failures >= 2:
                mark_disconnected()


class GlyphPreview(tk.Canvas):
    """Nothing Phone (1) style glyph preview with invisible click targets.

    The artwork intentionally avoids numbered badges over the LEDs so the preview
    looks like the real back of the phone instead of a diagram.
    """
    def __init__(self, parent, state_getter, toggle_callback, width=360, height=500, compact=False):
        super().__init__(parent, width=width, height=height, bg="#070707", highlightthickness=0, bd=0)
        self.state_getter = state_getter
        self.toggle_callback = toggle_callback
        self.compact = compact
        self.w = width
        self.h = height
        for idx in range(5):
            self.tag_bind(f"g{idx}", "<Button-1>", lambda e, i=idx: self.toggle_callback(i))
            self.tag_bind(f"g{idx}", "<Enter>", lambda e: self.configure(cursor="hand2"))
            self.tag_bind(f"g{idx}", "<Leave>", lambda e: self.configure(cursor=""))
        self._ref_size = (181, 332)
        self._asset_bboxes = [
            (27, 26, 63, 90),      # camera ring
            (115, 35, 147, 73),    # slash
            (26, 89, 158, 252),    # centre C
            (89, 265, 95, 301),    # bottom line
            (89, 307, 95, 313),    # bottom dot
        ]
        self._glyph_assets = []
        self._glyph_photos = []
        asset_dir = Path(__file__).resolve().parent / "assets"
        try:
            for idx in range(5):
                self._glyph_assets.append(Image.open(asset_dir / f"glyph_{idx}.png").convert("RGBA"))
        except Exception:
            self._glyph_assets = []
        self.redraw()

    def redraw(self):
        self.delete("all")
        state = self.state_getter()

        # Phone body. The real Nothing Phone (1) is tall and narrow, so keep a
        # little more side margin than the old preview did.
        side = 52 if self.w >= 330 else 38
        top = 14
        bottom = self.h - 14
        self.create_round_rect(
            side, top, self.w - side, bottom, 34,
            fill="#090909", outline="#454545", width=2
        )

        # When the bundled reference masks are available, render the exact Glyph
        # silhouettes taken from the Phone (1) reference image the user supplied.
        # This is far closer to the real hardware than approximating every curve
        # with generic Canvas arcs.
        if len(self._glyph_assets) == 5:
            ref_w, ref_h = self._ref_size
            body_h = bottom - top
            body_w = body_h * ref_w / ref_h
            max_w = self.w - 2 * 34
            if body_w > max_w:
                body_w = max_w
                body_h = body_w * ref_h / ref_w
            bx = (self.w - body_w) / 2
            by = (self.h - body_h) / 2
            sx = body_w / ref_w
            sy = body_h / ref_h

            # redraw body to the exact aspect ratio used by the reference masks
            self.delete("phone_exact")
            self.create_round_rect(bx, by, bx + body_w, by + body_h, 28,
                                   fill="#080808", outline="#454545", width=2,
                                   tags=("phone_exact",))
            # subtle camera glass
            self.create_oval(bx + 30*sx, by + 33*sy, bx + 62*sx, by + 65*sy,
                             outline="#1D1D1D", width=2)

            self._glyph_photos = []
            for idx, asset in enumerate(self._glyph_assets):
                x1, y1, x2, y2 = self._asset_bboxes[idx]
                target_w = max(1, int(round((x2-x1) * sx)))
                target_h = max(1, int(round((y2-y1) * sy)))
                alpha = asset.getchannel("A")
                if state[idx]:
                    tinted = Image.new("RGBA", asset.size, (255, 255, 255, 0))
                    tinted.putalpha(alpha)
                else:
                    # Keep the physical Glyph visible but dark when OFF.
                    dim_alpha = alpha.point(lambda v: int(v * 0.70))
                    tinted = Image.new("RGBA", asset.size, (54, 54, 54, 0))
                    tinted.putalpha(dim_alpha)
                resized = tinted.resize((target_w, target_h), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(resized)
                self._glyph_photos.append(photo)
                self.create_image(bx + x1*sx, by + y1*sy, anchor="nw", image=photo, tags=(f"g{idx}",))

            msg = "click a lit glyph again to turn it off" if any(state) else "click a glyph to toggle it"
            self.create_text(self.w / 2, self.h - 8, text=msg, fill="#666666", font=("Segoe UI", 8))
            return

        off = "#353535"
        on = "#FFFFFF"
        glow = "#DADADA"

        # Camera glass (not a glyph) behind the top-left ring.
        self.create_oval(side + 34, 44, side + 78, 88, outline="#202020", width=3)
        self.create_oval(side + 41, 51, side + 71, 81, outline="#151515", width=2)

        # 0 — camera ring. It is a broken ring around the lens with a small tail,
        # much closer to the actual Phone (1) layout than a generic circle.
        c = on if state[0] else off
        self.create_arc(side + 18, 30, side + 92, 104, start=40, extent=282,
                        style="arc", outline=c, width=9, tags=("g0",))
        self.create_line(side + 75, 94, side + 88, 86, fill=c, width=9,
                         capstyle=tk.ROUND, tags=("g0",))

        # 1 — upper-right slash. The previous version had this backwards (\).
        c = on if state[1] else off
        x2 = self.w - side - 20
        self.create_line(x2 - 42, 102, x2, 48, fill=c, width=9,
                         capstyle=tk.ROUND, tags=("g1",))

        # 2 — large centre C. The opening is on the RIGHT like the real glyph.
        c = on if state[2] else off
        self.create_arc(side + 5, 124, self.w - side + 4, self.h - 120,
                        start=34, extent=292, style="arc", outline=c, width=13,
                        tags=("g2",))

        # 3 — bottom vertical line.
        c = on if state[3] else off
        cx = self.w / 2
        self.create_line(cx, self.h - 108, cx, self.h - 53, fill=c, width=10,
                         capstyle=tk.ROUND, tags=("g3",))

        # 4 — bottom dot.
        c = on if state[4] else off
        self.create_oval(cx - 7, self.h - 38, cx + 7, self.h - 24,
                         fill=c, outline=c, tags=("g4",))

        # Soft inner glow only while active. This keeps the preview clean without
        # drawing labels over the actual glyphs.
        if any(state):
            self.create_text(self.w / 2, self.h - 8, text="click a lit glyph again to turn it off",
                             fill="#707070", font=("Segoe UI", 8))
        else:
            self.create_text(self.w / 2, self.h - 8, text="click a glyph to toggle it",
                             fill="#626262", font=("Segoe UI", 8))

        # Wider invisible hit areas make the thin glyphs easy to click.
        self._hitbox(side + 12, 24, side + 100, 112, "g0")
        self._hitbox(self.w - side - 80, 34, self.w - side + 5, 116, "g1")
        self._hitbox(side, 116, self.w - side + 8, self.h - 112, "g2")
        self._hitbox(cx - 24, self.h - 120, cx + 24, self.h - 46, "g3")
        self._hitbox(cx - 22, self.h - 48, cx + 22, self.h - 15, "g4")

    def _hitbox(self, x1, y1, x2, y2, tag):
        # Tk supports an empty outline/fill; the rectangle exists for events but
        # stays visually invisible.
        self.create_rectangle(x1, y1, x2, y2, fill="", outline="", tags=(tag,))

    def create_round_rect(self, x1, y1, x2, y2, radius=25, **kwargs):
        points = [
            x1+radius,y1, x2-radius,y1, x2,y1, x2,y1+radius,
            x2,y2-radius, x2,y2, x2-radius,y2, x1+radius,y2,
            x1,y2, x1,y2-radius, x1,y1+radius, x1,y1,
        ]
        return self.create_polygon(points, smooth=True, **kwargs)


class PatternTimeline(tk.Canvas):
    """A compact non-linear-editor style timeline for pattern beats."""
    TRACKS = ["Camera", "Slash", "Center C", "Line", "Dot"]

    def __init__(self, parent, height=250):
        super().__init__(parent, height=height, bg="#0A0A0A", highlightthickness=0, bd=0)
        self.step_spans = []
        self.bind("<Button-1>", self._clicked)
        self.bind("<Configure>", lambda _e: self.redraw())
        self.redraw()

    def redraw(self):
        self.delete("all")
        self.step_spans = []
        view_w = max(700, self.winfo_width())
        label_w = 105
        top = 38
        row_h = 36
        pps = 150.0
        total = sum(max(0.02, s["on"]) + max(0.0, s["wait"]) for s in pattern_steps)
        content_w = max(view_w, int(label_w + 45 + max(4.0, total) * pps))
        content_h = top + row_h * 5 + 18
        self.configure(scrollregion=(0, 0, content_w, content_h))

        # Time ruler.
        max_sec = max(4, int(total) + 2)
        for sec in range(max_sec + 1):
            x = label_w + 15 + sec * pps
            self.create_line(x, 24, x, content_h, fill="#1C1C1C", width=1)
            self.create_text(x + 3, 12, text=f"{sec}s", fill="#747474",
                             anchor="nw", font=("Segoe UI", 8))

        # Track labels and separators.
        for row, name in enumerate(self.TRACKS):
            y1 = top + row * row_h
            y2 = y1 + row_h
            self.create_rectangle(0, y1, label_w, y2, fill="#151515", outline="#232323")
            self.create_text(10, (y1+y2)/2, text=name, fill="#CFCFCF",
                             anchor="w", font=("Segoe UI", 9, "bold"))
            self.create_line(label_w, y2, content_w, y2, fill="#202020")

        if not pattern_steps:
            self.create_text(label_w + 28, top + row_h * 2.5,
                             text="Add a beat — clips will appear here like an editor timeline",
                             fill="#666666", anchor="w", font=("Segoe UI", 10))
            return

        x = label_w + 15
        for i, step in enumerate(pattern_steps):
            beat_w = max(10, step["on"] * pps)
            wait_w = max(0, step["wait"] * pps)
            selected = (i == selected_step)

            # Selected beat outline spans all tracks, like selecting a clip/group.
            if selected:
                self.create_rectangle(x - 3, top - 3, x + beat_w + 3, top + row_h * 5 + 2,
                                      outline="#2F8FFF", width=2)

            self.create_text(x + 3, top - 8, text=f"B{i+1}", fill="#8E8E8E",
                             anchor="sw", font=("Segoe UI", 8, "bold"))

            for row in range(5):
                y1 = top + row * row_h + 6
                y2 = y1 + row_h - 12
                if step["frame"][row]:
                    fill = "#FFFFFF" if selected else "#D7D7D7"
                    txt = "#080808"
                    self.create_rectangle(x, y1, x + beat_w, y2, fill=fill,
                                          outline="#FFFFFF", width=1)
                    if beat_w >= 48:
                        self.create_text(x + 6, (y1+y2)/2, text=self.TRACKS[row], fill=txt,
                                         anchor="w", font=("Segoe UI", 8, "bold"))
                else:
                    self.create_rectangle(x, y1, x + beat_w, y2, fill="#131313",
                                          outline="#242424", width=1)

            # Wait is shown as a darker gap/clip tail.
            if wait_w > 0:
                self.create_rectangle(x + beat_w, top + 3, x + beat_w + wait_w,
                                      top + row_h * 5 - 3, fill="#0F0F0F",
                                      outline="#1F1F1F", width=1)
                if wait_w >= 55:
                    self.create_text(x + beat_w + 5, top + 10,
                                     text=f"wait {format_seconds(step['wait'])}",
                                     fill="#5E5E5E", anchor="nw", font=("Segoe UI", 7))

            self.step_spans.append((i, x, x + beat_w + wait_w))
            x += beat_w + wait_w

    def _clicked(self, event):
        x = self.canvasx(event.x)
        for i, x1, x2 in self.step_spans:
            if x1 <= x <= x2:
                load_step(i)
                return


def refresh_previews():
    if manual_preview:
        manual_preview.redraw()
    if pattern_preview:
        pattern_preview.redraw()
    for i, btn in enumerate(manual_glyph_buttons):
        active = bool(manual_state[i])
        btn.configure(fg_color="#FFFFFF" if active else "#202020",
                      hover_color="#E8E8E8" if active else "#303030",
                      text_color="#000000" if active else "#FFFFFF")
    for i, btn in enumerate(pattern_glyph_buttons):
        active = bool(pattern_edit_state[i])
        btn.configure(fg_color="#FFFFFF" if active else "#202020",
                      hover_color="#E8E8E8" if active else "#303030",
                      text_color="#000000" if active else "#FFFFFF")
    if pattern_timeline:
        pattern_timeline.redraw()


def manual_toggle(index):
    manual_state[index] = 0 if manual_state[index] else MANUAL_LEVEL
    refresh_previews()
    if phone_connected and active_mode == "Manual":
        try:
            send_frame(manual_state)
        except Exception:
            pass


def manual_all(value):
    global manual_state
    manual_state = [MANUAL_LEVEL if value else 0] * 5
    refresh_previews()
    if phone_connected and active_mode == "Manual":
        try:
            send_frame(manual_state)
        except Exception:
            pass


def force_all_off():
    global manual_state, pattern_edit_state
    manual_state = [0, 0, 0, 0, 0]
    pattern_edit_state = [0, 0, 0, 0, 0]
    stop_pattern_playback(restore_preview=False)
    if root:
        root.after(0, refresh_previews)
    else:
        refresh_previews()
    if phone_connected:
        try:
            send_frame([0, 0, 0, 0, 0])
        except Exception:
            pass


def pattern_toggle(index):
    pattern_edit_state[index] = 0 if pattern_edit_state[index] else MANUAL_LEVEL
    refresh_previews()
    if phone_connected and active_mode == "Pattern":
        try:
            send_frame(pattern_edit_state)
        except Exception:
            pass
    if recording_taps:
        add_step([MANUAL_LEVEL if i == index else 0 for i in range(5)])


def add_step(frame=None):
    global selected_step
    frame = list(frame if frame is not None else pattern_edit_state)
    if not any(frame):
        return
    step = {
        "frame": frame,
        "on": parse_time_option(on_var.get()),
        "wait": parse_time_option(wait_var.get()),
    }
    pattern_steps.append(step)
    selected_step = len(pattern_steps) - 1
    refresh_step_list()
    refresh_previews()
    save_pattern_store()


def update_selected_step():
    if selected_step is None or not (0 <= selected_step < len(pattern_steps)):
        return
    pattern_steps[selected_step] = {
        "frame": pattern_edit_state[:],
        "on": parse_time_option(on_var.get()),
        "wait": parse_time_option(wait_var.get()),
    }
    refresh_step_list()
    refresh_previews()
    save_pattern_store()


def delete_selected_step():
    global selected_step
    if selected_step is None or not (0 <= selected_step < len(pattern_steps)):
        return
    pattern_steps.pop(selected_step)
    if pattern_steps:
        selected_step = min(selected_step, len(pattern_steps) - 1)
        load_step(selected_step)
    else:
        selected_step = None
    refresh_step_list()
    refresh_previews()
    save_pattern_store()


def move_step(delta):
    global selected_step
    if selected_step is None:
        return
    new = selected_step + delta
    if not (0 <= new < len(pattern_steps)):
        return
    pattern_steps[selected_step], pattern_steps[new] = pattern_steps[new], pattern_steps[selected_step]
    selected_step = new
    refresh_step_list()
    refresh_previews()
    save_pattern_store()


def format_seconds(value):
    if value >= 1:
        return f"{value:g}s"
    return f"{int(value * 1000)}ms"


def step_name(frame):
    names = ["Camera", "Upper", "Center", "Line", "Dot"]
    selected = [names[i] for i, v in enumerate(frame) if v]
    return "+".join(selected) if selected else "Off"


def load_step(index):
    global selected_step, pattern_edit_state
    if not (0 <= index < len(pattern_steps)):
        return
    selected_step = index
    step = pattern_steps[index]
    pattern_edit_state = step["frame"][:]
    on_var.set(option_from_seconds(step["on"], ON_OPTIONS))
    wait_var.set(option_from_seconds(step["wait"], WAIT_OPTIONS))
    refresh_previews()
    refresh_step_list()


def option_from_seconds(seconds, options):
    best = options[0]
    best_diff = 999
    for opt in options:
        diff = abs(parse_time_option(opt) - seconds)
        if diff < best_diff:
            best, best_diff = opt, diff
    return best


def refresh_step_list():
    if pattern_list_frame is None:
        return
    for child in pattern_list_frame.winfo_children():
        child.destroy()
    for i, step in enumerate(pattern_steps):
        active = (i == selected_step)
        text = f"{i+1}. {step_name(step['frame'])}   • ON {format_seconds(step['on'])}   • WAIT {format_seconds(step['wait'])}"
        btn = ctk.CTkButton(
            pattern_list_frame,
            text=text,
            anchor="w",
            height=38,
            corner_radius=12,
            fg_color="#FFFFFF" if active else "#1B1B1B",
            text_color="#000000" if active else "#FFFFFF",
            hover_color="#E8E8E8" if active else "#292929",
            command=lambda idx=i: load_step(idx),
        )
        btn.pack(fill="x", padx=4, pady=4)
    if pattern_summary:
        total = sum(step["on"] + step["wait"] for step in pattern_steps)
        pattern_summary.configure(text=f"{len(pattern_steps)} beat{'s' if len(pattern_steps) != 1 else ''} • {total:.2f}s")
    if pattern_timeline:
        pattern_timeline.redraw()


def toggle_record_taps():
    global recording_taps
    recording_taps = not recording_taps
    record_button.configure(
        text="Stop Recording" if recording_taps else "Record Taps",
        fg_color="#FFFFFF" if recording_taps else "#222222",
        text_color="#000000" if recording_taps else "#FFFFFF",
    )


def _play_pattern(loop):
    global pattern_loop_running, pattern_play_once_running
    try:
        while app_alive and (pattern_loop_running if loop else pattern_play_once_running):
            if not pattern_steps:
                break
            for step in list(pattern_steps):
                running = pattern_loop_running if loop else pattern_play_once_running
                if not running or active_mode != "Pattern":
                    break
                # Keep refreshing the active beat so long ON times (including 1s)
                # are not cleared by Android's disconnect watchdog.
                end_on = time.perf_counter() + max(0.01, step["on"])
                while time.perf_counter() < end_on:
                    running = pattern_loop_running if loop else pattern_play_once_running
                    if not running or active_mode != "Pattern":
                        break
                    if phone_connected:
                        try:
                            send_frame(step["frame"])
                        except Exception:
                            pass
                    remain = end_on - time.perf_counter()
                    if remain > 0:
                        time.sleep(min(0.25, remain))
                if phone_connected:
                    try:
                        send_frame([0, 0, 0, 0, 0])
                    except Exception:
                        pass
                end_wait = time.perf_counter() + max(0.0, step["wait"])
                while time.perf_counter() < end_wait:
                    running = pattern_loop_running if loop else pattern_play_once_running
                    if not running or active_mode != "Pattern":
                        break
                    time.sleep(min(0.05, max(0.0, end_wait - time.perf_counter())))
            if not loop:
                break
    finally:
        if loop:
            pattern_loop_running = False
        else:
            pattern_play_once_running = False
        if phone_connected and active_mode == "Pattern":
            try:
                send_frame(pattern_edit_state)
            except Exception:
                pass
        if root:
            root.after(0, _refresh_transport_buttons)


def _refresh_transport_buttons():
    if loop_button:
        loop_button.configure(
            text="Stop Loop" if pattern_loop_running else "Loop",
            fg_color="#FFFFFF" if pattern_loop_running else "#242424",
            text_color="#000000" if pattern_loop_running else "#FFFFFF",
        )


def play_once():
    global pattern_play_once_running, pattern_play_thread
    if not pattern_steps or active_mode != "Pattern":
        return
    stop_pattern_playback(restore_preview=False)
    pattern_play_once_running = True
    pattern_play_thread = threading.Thread(target=lambda: _play_pattern(False), daemon=True)
    pattern_play_thread.start()


def toggle_loop():
    global pattern_loop_running, pattern_loop_thread
    if pattern_loop_running:
        stop_pattern_playback()
        return
    if not pattern_steps or active_mode != "Pattern":
        return
    stop_pattern_playback(restore_preview=False)
    pattern_loop_running = True
    _refresh_transport_buttons()
    pattern_loop_thread = threading.Thread(target=lambda: _play_pattern(True), daemon=True)
    pattern_loop_thread.start()


def stop_pattern_playback(restore_preview=True):
    global pattern_loop_running, pattern_play_once_running
    pattern_loop_running = False
    pattern_play_once_running = False
    if phone_connected:
        try:
            if restore_preview and active_mode == "Pattern":
                send_frame(pattern_edit_state)
            else:
                send_frame([0, 0, 0, 0, 0])
        except Exception:
            pass
    _refresh_transport_buttons()


def stop_loop():
    # Kept for tray/quit compatibility.
    stop_pattern_playback()


def clear_pattern():
    global pattern_steps, selected_step, pattern_edit_state
    stop_pattern_playback(restore_preview=False)
    pattern_steps = []
    selected_step = None
    pattern_edit_state = [0, 0, 0, 0, 0]
    refresh_previews()
    refresh_step_list()
    save_pattern_store()


def on_tab_changed():
    """Make Music/Manual/Pattern true exclusive modes.

    Music used to keep sending frames while Manual was open, and Android's safety
    watchdog then cleared a one-shot Manual frame. This mode switch + hold sender
    makes Manual act like a real toggle: ON stays ON until clicked again or the
    user leaves Manual.
    """
    global active_mode, manual_state
    if not tabs_widget:
        return
    new_mode = tabs_widget.get()
    old_mode = active_mode
    if new_mode == old_mode:
        return

    if old_mode == "Manual":
        manual_state = [0, 0, 0, 0, 0]
        refresh_previews()
    if old_mode == "Pattern":
        stop_pattern_playback(restore_preview=False)

    active_mode = new_mode

    if phone_connected:
        try:
            if new_mode == "Manual":
                send_frame(manual_state)
            elif new_mode == "Pattern":
                send_frame(pattern_edit_state)
            else:
                # Clear the previous mode immediately. Music will write the next
                # audio frame if it is enabled.
                send_frame([0, 0, 0, 0, 0])
        except Exception:
            pass


def mode_hold_worker():
    """Refresh static Manual/Pattern states before Android's watchdog expires."""
    while app_alive:
        if phone_connected:
            try:
                if active_mode == "Manual":
                    send_frame(manual_state)
                elif active_mode == "Pattern" and not pattern_loop_running and not pattern_play_once_running:
                    send_frame(pattern_edit_state)
            except Exception:
                pass
        time.sleep(0.30)


def audio_worker():
    if sc is None:
        return
    global audio_running
    speaker = sc.default_speaker()
    loopback = sc.get_microphone(id=str(speaker.name), include_loopback=True)
    window = np.hanning(BLOCK).astype(np.float32)
    freqs = np.fft.rfftfreq(BLOCK, 1.0 / RATE)
    low = (freqs >= 35) & (freqs < 180)
    lowmid = (freqs >= 180) & (freqs < 900)
    mid = (freqs >= 900) & (freqs < 2500)
    highmid = (freqs >= 2500) & (freqs < 7000)
    air = (freqs >= 7000) & (freqs < 14000)

    prev_spec = np.zeros(len(freqs), dtype=np.float64)
    bk = blm = bm = bhm = ba = None
    last_kick = last_snare = last_hat = 0.0
    warmup = 0
    until = np.zeros(5, dtype=np.float64)
    bright = np.zeros(5, dtype=np.float64)

    def mean_band(arr, mask):
        return float(np.mean(arr[mask])) if np.any(mask) else 0.0

    def ratio(v, baseline):
        if baseline is None or baseline <= 1e-9:
            return 1.0
        return float(np.clip(v / baseline, 0.0, 8.0))

    def hit(i, value, now):
        bright[i] = max(bright[i], value)
        until[i] = max(until[i], now + HOLD)

    try:
        with loopback.recorder(samplerate=RATE, channels=2, blocksize=RECORDER_BUFFER) as mic:
            while app_alive:
                if not audio_running or active_mode != "Music":
                    time.sleep(0.05)
                    continue
                audio = mic.record(numframes=BLOCK)
                mono = np.mean(audio, axis=1).astype(np.float32)
                spec = np.abs(np.fft.rfft(mono * window)).astype(np.float64)
                flux = np.maximum(spec - prev_spec, 0.0)
                prev_spec = spec
                f_low = mean_band(flux, low)
                f_lm = mean_band(flux, lowmid)
                f_mid = mean_band(flux, mid)
                f_hm = mean_band(flux, highmid)
                f_air = mean_band(flux, air)
                warmup += 1

                if bk is None:
                    bk, blm, bm, bhm, ba = f_low, f_lm, f_mid, f_hm, f_air
                    continue

                rlow = ratio(f_low, bk)
                rmid = ratio(f_mid, bm)
                rhm = ratio(f_hm, bhm)
                rair = ratio(f_air, ba)
                total = float(np.sum(spec))
                centroid = float(np.sum(freqs * spec) / total) if total > 1e-12 else 0.0
                ss = np.maximum(spec, 1e-12)
                flat = float(np.exp(np.mean(np.log(ss))) / np.mean(ss))
                rms = float(np.sqrt(np.mean(mono * mono) + 1e-12))
                now = time.perf_counter()

                if warmup >= 20 and rms > 0.00065:
                    voice_like = flat < 0.028 and 350 < centroid < 3200 and (f_lm + f_mid) > (f_low + f_hm + f_air) * 2.2
                    kick_t = 1.85 if music_mode == "balanced" else 1.60
                    snare_t = 1.75 if music_mode == "balanced" else 1.52
                    hat_t = 2.15 if music_mode == "balanced" else 1.90
                    kick = not voice_like and rlow > kick_t and f_low > f_lm * 0.52 and now - last_kick > 0.080
                    snare = not voice_like and rhm > snare_t and rmid > 1.12 and f_hm > f_air * 0.16 and centroid > 900 and now - last_snare > 0.080
                    hat = not voice_like and rair > hat_t and rhm > 1.25 and f_air > f_mid * 0.45 and centroid > 2800 and now - last_hat > 0.070

                    if kick:
                        last_kick = now
                        b = MAXISH if rlow > 4.2 else STRONG if rlow > 2.6 else NORMAL
                        if music_mode == "aggressive": b = min(4095, int(b * 1.12))
                        hit(3, b, now); hit(4, b * 0.86, now)
                        if rlow > 3.2: hit(2, b * 0.42, now)
                    if snare:
                        last_snare = now
                        p = max(rhm, rmid)
                        b = MAXISH if p > 4.2 else STRONG if p > 2.7 else NORMAL
                        if music_mode == "aggressive": b = min(4095, int(b * 1.10))
                        hit(2, b, now); hit(1, b * 0.84, now); hit(0, b * 0.35, now)
                    if hat:
                        last_hat = now
                        p = max(rair, rhm)
                        b = STRONG if p > 3.3 else NORMAL
                        hit(0, b * 0.82, now); hit(1, b * 0.52, now)
                    if kick and snare and max(rlow, rhm) > 5.0:
                        for i in range(5): hit(i, SLAM, now)

                alpha = 0.11 if warmup < 30 else 0.035
                bk = (1-alpha)*bk + alpha*f_low
                blm = (1-alpha)*blm + alpha*f_lm
                bm = (1-alpha)*bm + alpha*f_mid
                bhm = (1-alpha)*bhm + alpha*f_hm
                ba = (1-alpha)*ba + alpha*f_air

                frame = []
                for i in range(5):
                    if now < until[i]: frame.append(int(bright[i]))
                    else: bright[i] = 0; frame.append(0)
                if phone_connected:
                    try: send_frame(frame)
                    except Exception: pass
    except Exception:
        pass


def toggle_music():
    global audio_running
    audio_running = not audio_running
    if not audio_running and phone_connected:
        try: send_frame([0, 0, 0, 0, 0])
        except Exception: pass
    update_header()


def set_music_mode(label):
    global music_mode
    music_mode = {"Balanced Drums": "balanced", "Aggressive Drums": "aggressive"}.get(label, "balanced")


def startup_command():
    exe = Path(sys.executable).resolve()
    if exe.suffix.lower() == ".exe" and exe.name.lower() not in ("python.exe", "pythonw.exe"):
        return f'"{exe}" --background'
    pythonw = exe
    if pythonw.name.lower() == "python.exe":
        candidate = pythonw.with_name("pythonw.exe")
        if candidate.exists():
            pythonw = candidate
    return f'"{pythonw}" "{Path(__file__).resolve()}" --background'


def startup_enabled():
    if os.name != "nt": return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return bool(value)
    except OSError:
        return False


def set_startup(enable):
    if os.name != "nt": return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
        if enable: winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try: winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError: pass


def show_window(icon=None, item=None):
    if root:
        root.after(0, root.deiconify); root.after(0, root.lift); root.after(0, root.focus_force)


def hide_window():
    if root: root.withdraw()


def quit_app(icon=None, item=None):
    global app_alive, audio_running
    save_pattern_store(update_active=True)
    app_alive = False; audio_running = False; stop_loop()
    if phone_connected:
        try: send_frame([0,0,0,0,0])
        except Exception: pass
    if tray_icon: tray_icon.stop()
    if root: root.after(0, root.destroy)


def make_icon():
    im = Image.new("RGB", (64,64), "black")
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((8,8,56,56), radius=18, outline="white", width=4)
    d.arc((18,18,46,46), start=30, end=330, fill="white", width=4)
    return im


def build_gui():
    global root, status_label, device_label, battery_label, charging_label, usb_label, music_button
    global startup_var, manual_preview, pattern_preview, pattern_list_frame, on_var, wait_var
    global pattern_summary, loop_button, record_button, pattern_timeline, tabs_widget
    global manual_glyph_buttons, pattern_glyph_buttons
    global pattern_selector, pattern_selector_var, pattern_storage_label

    root = ctk.CTk()
    root.title("GlyphLink")
    root.geometry("1180x900")
    root.minsize(980, 760)
    root.configure(fg_color="#090909")

    outer = ctk.CTkFrame(root, fg_color="#090909")
    outer.pack(fill="both", expand=True, padx=16, pady=16)

    # ---------------- Header ----------------
    header = ctk.CTkFrame(outer, fg_color="#111111", corner_radius=22)
    header.pack(fill="x", pady=(0, 12))

    title_row = ctk.CTkFrame(header, fg_color="transparent")
    title_row.pack(fill="x", padx=18, pady=(14, 8))
    title_wrap = ctk.CTkFrame(title_row, fg_color="transparent")
    title_wrap.pack(side="left")
    ctk.CTkLabel(title_wrap, text="GlyphLink", font=ctk.CTkFont(size=28, weight="bold")).pack(anchor="w")
    ctk.CTkLabel(title_wrap, text="USB • Nothing Phone (1)", text_color="#BEBEBE").pack(anchor="w", pady=(2, 0))

    startup_var = ctk.BooleanVar(value=startup_enabled())
    ctk.CTkCheckBox(
        title_row, text="Start with Windows", variable=startup_var,
        command=lambda: set_startup(bool(startup_var.get()))
    ).pack(side="right", padx=(12, 0))

    stats = ctk.CTkFrame(header, fg_color="transparent")
    stats.pack(fill="x", padx=14, pady=(0, 8))
    stats.grid_columnconfigure((0, 1, 2, 3), weight=1)

    def stat(title, col):
        f = ctk.CTkFrame(stats, fg_color="#171717", corner_radius=16)
        f.grid(row=0, column=col, padx=5, pady=5, sticky="nsew")
        ctk.CTkLabel(f, text=title, text_color="#999999", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=11, pady=(9, 1))
        v = ctk.CTkLabel(f, text="--", font=ctk.CTkFont(size=15, weight="bold"))
        v.pack(anchor="w", padx=11, pady=(0, 9))
        return v

    status_label = stat("Status", 0)
    device_label = stat("ADB Device", 1)
    battery_label = stat("Battery", 2)
    charging_label = stat("Charging", 3)

    bar = ctk.CTkFrame(header, fg_color="#171717", corner_radius=16)
    bar.pack(fill="x", padx=19, pady=(0, 14))
    ctk.CTkLabel(bar, text="USB", text_color="#999999").pack(side="left", padx=(12, 6), pady=8)
    usb_label = ctk.CTkLabel(bar, text="Disconnected", font=ctk.CTkFont(weight="bold"))
    usb_label.pack(side="left", pady=8)
    ctk.CTkLabel(
        bar, text="Modes are exclusive • Manual stays on until you toggle it or leave Manual",
        text_color="#777777", font=ctk.CTkFont(size=10)
    ).pack(side="right", padx=12)

    # ---------------- Tabs ----------------
    tabs = ctk.CTkTabview(outer, fg_color="#090909", corner_radius=18, command=on_tab_changed)
    tabs_widget = tabs
    tabs.pack(fill="both", expand=True)
    for name in ("Music", "Manual", "Pattern"):
        tabs.add(name)
    tabs.set("Music")

    # ---------------- Music ----------------
    mt = tabs.tab("Music")
    card = ctk.CTkFrame(mt, fg_color="#111111", corner_radius=18)
    card.pack(fill="both", expand=True, padx=6, pady=6)
    ctk.CTkLabel(card, text="Music Visualizer", font=ctk.CTkFont(size=22, weight="bold")).pack(anchor="w", padx=18, pady=(18, 8))
    ctk.CTkLabel(
        card,
        text="Reactive drum lighting. Switching to Manual or Pattern pauses music frames automatically.",
        text_color="#AFAFAF"
    ).pack(anchor="w", padx=18, pady=(0, 14))
    ctk.CTkOptionMenu(card, values=["Balanced Drums", "Aggressive Drums"], command=set_music_mode).pack(fill="x", padx=18, pady=(0, 12))
    music_button = ctk.CTkButton(
        card, text="Stop Music", command=toggle_music, fg_color="#FFFFFF",
        text_color="#000000", corner_radius=16, height=44
    )
    music_button.pack(fill="x", padx=18, pady=(0, 18))

    # ---------------- Manual ----------------
    man = tabs.tab("Manual")
    mcard = ctk.CTkFrame(man, fg_color="#111111", corner_radius=18)
    mcard.pack(fill="both", expand=True, padx=6, pady=6)

    mw = ctk.CTkFrame(mcard, fg_color="transparent")
    mw.pack(fill="both", expand=True, padx=16, pady=16)
    mw.grid_columnconfigure(0, weight=2)
    mw.grid_columnconfigure(1, weight=1)
    mw.grid_rowconfigure(0, weight=1)

    left = ctk.CTkFrame(mw, fg_color="#0D0D0D", corner_radius=18)
    left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
    ctk.CTkLabel(left, text="Phone Glyph Preview", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=16, pady=(14, 8))
    preview_wrap = ctk.CTkFrame(left, fg_color="#070707", corner_radius=18)
    preview_wrap.pack(expand=True, pady=(0, 10))
    manual_preview = GlyphPreview(preview_wrap, lambda: manual_state, manual_toggle, width=360, height=500)
    manual_preview.pack(padx=8, pady=8)

    right = ctk.CTkFrame(mw, fg_color="#171717", corner_radius=18)
    right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
    ctk.CTkLabel(right, text="Manual Control", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=16, pady=(16, 4))
    ctk.CTkLabel(
        right,
        text="Tap the phone or these buttons. A glyph stays ON until you tap it again or leave Manual.",
        text_color="#AFAFAF", wraplength=330, justify="left"
    ).pack(anchor="w", padx=16, pady=(0, 14))

    glyph_names = ["Camera ring", "Top slash", "Center C", "Bottom line", "Bottom dot"]
    manual_glyph_buttons = []
    for i, name in enumerate(glyph_names):
        b = ctk.CTkButton(
            right, text=name, command=lambda idx=i: manual_toggle(idx),
            height=40, corner_radius=12, fg_color="#202020", hover_color="#303030"
        )
        b.pack(fill="x", padx=16, pady=4)
        manual_glyph_buttons.append(b)

    ctk.CTkFrame(right, height=1, fg_color="#2A2A2A").pack(fill="x", padx=16, pady=14)
    ctk.CTkButton(
        right, text="All On", command=lambda: manual_all(True), fg_color="#FFFFFF",
        text_color="#000000", corner_radius=14, height=42
    ).pack(fill="x", padx=16, pady=(0, 6))
    ctk.CTkButton(
        right, text="All Off", command=lambda: manual_all(False),
        corner_radius=14, height=42
    ).pack(fill="x", padx=16, pady=(0, 16))

    # ---------------- Pattern editor ----------------
    pt = tabs.tab("Pattern")
    pcard = ctk.CTkScrollableFrame(pt, fg_color="#111111", corner_radius=18)
    pcard.pack(fill="both", expand=True, padx=6, pady=6)

    ctk.CTkLabel(pcard, text="Pattern Editor", font=ctk.CTkFont(size=22, weight="bold")).pack(anchor="w", padx=16, pady=(14, 2))
    ctk.CTkLabel(
        pcard,
        text="Build beats like an editor: choose glyphs, set ON/WAIT timing, then arrange them on the timeline.",
        text_color="#AFAFAF"
    ).pack(anchor="w", padx=16, pady=(0, 10))

    # Pattern library — up to 15 saved beat patterns. Everything autosaves to
    # a hidden file inside the user's AppData folder.
    library = ctk.CTkFrame(pcard, fg_color="#171717", corner_radius=16)
    library.pack(fill="x", padx=16, pady=(0, 10))
    lib_left = ctk.CTkFrame(library, fg_color="transparent")
    lib_left.pack(side="left", fill="x", expand=True, padx=(12, 6), pady=10)
    ctk.CTkLabel(lib_left, text="Saved Pattern", text_color="#AFAFAF", font=ctk.CTkFont(size=11)).pack(anchor="w")
    pattern_selector_var = ctk.StringVar(value=patterns[active_pattern_index]["name"] if patterns else "Pattern 1")
    pattern_selector = ctk.CTkOptionMenu(
        lib_left, values=[p["name"] for p in patterns] or ["Pattern 1"],
        variable=pattern_selector_var, command=switch_pattern,
        height=38, corner_radius=12
    )
    pattern_selector.pack(fill="x", pady=(4, 0))

    lib_buttons = ctk.CTkFrame(library, fg_color="transparent")
    lib_buttons.pack(side="right", padx=(6, 12), pady=10)
    ctk.CTkButton(lib_buttons, text="+ New", width=82, height=36, corner_radius=11, command=create_pattern).pack(side="left", padx=3)
    ctk.CTkButton(lib_buttons, text="Rename", width=82, height=36, corner_radius=11, command=rename_pattern).pack(side="left", padx=3)
    ctk.CTkButton(lib_buttons, text="Delete", width=82, height=36, corner_radius=11, command=delete_pattern).pack(side="left", padx=3)
    ctk.CTkButton(
        lib_buttons, text="Save", width=82, height=36, corner_radius=11,
        fg_color="#FFFFFF", hover_color="#E8E8E8", text_color="#000000",
        command=lambda: save_pattern_store(True)
    ).pack(side="left", padx=3)
    pattern_storage_label = ctk.CTkLabel(
        library, text=f"Autosaved • {len(patterns)}/{MAX_PATTERNS} patterns",
        text_color="#777777", font=ctk.CTkFont(size=10)
    )
    pattern_storage_label.pack(side="bottom", anchor="e", padx=16, pady=(0, 8))

    # Transport lives at the TOP so Play/Loop can never be hidden below the editor.
    transport = ctk.CTkFrame(pcard, fg_color="#171717", corner_radius=16)
    transport.pack(fill="x", padx=16, pady=(0, 10))
    ctk.CTkButton(
        transport, text="▶  Play Once", command=play_once,
        fg_color="#FFFFFF", hover_color="#E8E8E8", text_color="#000000",
        corner_radius=12, height=40
    ).pack(side="left", padx=(10, 5), pady=10)
    loop_button = ctk.CTkButton(
        transport, text="Loop", command=toggle_loop,
        fg_color="#242424", hover_color="#333333", text_color="#FFFFFF",
        corner_radius=12, height=40
    )
    loop_button.pack(side="left", padx=5, pady=10)
    ctk.CTkButton(
        transport, text="■  Stop", command=lambda: stop_pattern_playback(restore_preview=False),
        fg_color="#242424", hover_color="#333333", corner_radius=12, height=40
    ).pack(side="left", padx=5, pady=10)
    pattern_summary = ctk.CTkLabel(transport, text="0 beats • 0.00s", font=ctk.CTkFont(size=13, weight="bold"))
    pattern_summary.pack(side="right", padx=14)

    workspace = ctk.CTkFrame(pcard, fg_color="transparent")
    workspace.pack(fill="x", padx=16, pady=(0, 10))
    workspace.grid_columnconfigure(0, weight=1)
    workspace.grid_columnconfigure(1, weight=2)

    pvcard = ctk.CTkFrame(workspace, fg_color="#0D0D0D", corner_radius=18)
    pvcard.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
    ctk.CTkLabel(pvcard, text="Beat Preview", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=12, pady=(12, 6))
    pvwrap = ctk.CTkFrame(pvcard, fg_color="#070707", corner_radius=16)
    pvwrap.pack(padx=10, pady=(0, 8))
    pattern_preview = GlyphPreview(pvwrap, lambda: pattern_edit_state, pattern_toggle, width=300, height=410, compact=True)
    pattern_preview.pack(padx=5, pady=5)

    # Buttons directly under the preview: no switching to Manual just to audition a glyph.
    glyph_row = ctk.CTkFrame(pvcard, fg_color="transparent")
    glyph_row.pack(fill="x", padx=10, pady=(0, 10))
    pattern_glyph_buttons = []
    short_names = ["Camera", "Slash", "C", "Line", "Dot"]
    for i, name in enumerate(short_names):
        b = ctk.CTkButton(
            glyph_row, text=name, width=54, height=32, corner_radius=10,
            fg_color="#202020", hover_color="#303030",
            command=lambda idx=i: pattern_toggle(idx)
        )
        b.pack(side="left", expand=True, fill="x", padx=2)
        pattern_glyph_buttons.append(b)

    inspector = ctk.CTkFrame(workspace, fg_color="#171717", corner_radius=18)
    inspector.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
    ctk.CTkLabel(inspector, text="Beat Properties", font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w", padx=16, pady=(14, 10))

    ctk.CTkLabel(inspector, text="Light stays ON for", text_color="#BDBDBD").pack(anchor="w", padx=16)
    on_var = ctk.StringVar(value="100 ms")
    ctk.CTkOptionMenu(inspector, values=ON_OPTIONS, variable=on_var).pack(fill="x", padx=16, pady=(4, 10))

    ctk.CTkLabel(inspector, text="Wait after this beat", text_color="#BDBDBD").pack(anchor="w", padx=16)
    wait_var = ctk.StringVar(value="100 ms")
    ctk.CTkOptionMenu(inspector, values=WAIT_OPTIONS, variable=wait_var).pack(fill="x", padx=16, pady=(4, 12))

    ctk.CTkButton(
        inspector, text="+ Add Beat to Timeline", command=add_step,
        fg_color="#FFFFFF", hover_color="#E8E8E8", text_color="#000000",
        corner_radius=14, height=42
    ).pack(fill="x", padx=16, pady=(0, 6))
    ctk.CTkButton(
        inspector, text="Update Selected Beat", command=update_selected_step,
        corner_radius=14, height=38
    ).pack(fill="x", padx=16, pady=6)

    record_button = ctk.CTkButton(
        inspector, text="Record Taps", command=toggle_record_taps,
        corner_radius=14, height=38
    )
    record_button.pack(fill="x", padx=16, pady=6)
    ctk.CTkLabel(
        inspector,
        text="Record Taps adds each glyph press as its own beat using the current ON + WAIT values.",
        wraplength=430, justify="left", text_color="#8C8C8C", font=ctk.CTkFont(size=11)
    ).pack(anchor="w", padx=16, pady=(4, 12))

    # Timeline — intentionally styled like a compact video/audio editor.
    tlcard = ctk.CTkFrame(pcard, fg_color="#0D0D0D", corner_radius=18)
    tlcard.pack(fill="x", padx=16, pady=(0, 10))
    tlhead = ctk.CTkFrame(tlcard, fg_color="transparent")
    tlhead.pack(fill="x", padx=12, pady=(10, 4))
    ctk.CTkLabel(tlhead, text="Timeline", font=ctk.CTkFont(size=17, weight="bold")).pack(side="left")
    ctk.CTkLabel(tlhead, text="click a clip to edit that beat", text_color="#777777", font=ctk.CTkFont(size=10)).pack(side="right")

    pattern_timeline = PatternTimeline(tlcard, height=245)
    pattern_timeline.pack(fill="x", padx=12, pady=(0, 2))
    hscroll = ctk.CTkScrollbar(tlcard, orientation="horizontal", command=pattern_timeline.xview, height=14)
    hscroll.pack(fill="x", padx=12, pady=(0, 10))
    pattern_timeline.configure(xscrollcommand=hscroll.set)

    # Compact beat list for precise reordering/deleting.
    listhead = ctk.CTkFrame(pcard, fg_color="transparent")
    listhead.pack(fill="x", padx=16, pady=(0, 4))
    ctk.CTkLabel(listhead, text="Beats", font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
    ctk.CTkLabel(listhead, text="ON + WAIT are stored per beat", text_color="#777777", font=ctk.CTkFont(size=10)).pack(side="right")

    pattern_list_frame = ctk.CTkScrollableFrame(pcard, height=112, fg_color="#0E0E0E", corner_radius=16)
    pattern_list_frame.pack(fill="x", padx=16, pady=(0, 8))

    editrow = ctk.CTkFrame(pcard, fg_color="transparent")
    editrow.pack(fill="x", padx=16, pady=(0, 16))
    ctk.CTkButton(editrow, text="← Earlier", command=lambda: move_step(-1), corner_radius=12, height=36).pack(side="left", padx=(0, 5))
    ctk.CTkButton(editrow, text="Later →", command=lambda: move_step(1), corner_radius=12, height=36).pack(side="left", padx=5)
    ctk.CTkButton(editrow, text="Delete Selected", command=delete_selected_step, corner_radius=12, height=36).pack(side="left", expand=True, fill="x", padx=5)
    ctk.CTkButton(editrow, text="Clear Timeline", command=clear_pattern, corner_radius=12, height=36).pack(side="left", expand=True, fill="x", padx=(5, 0))

    root.protocol("WM_DELETE_WINDOW", hide_window)
    refresh_previews()
    refresh_step_list()
    update_header()
    set_status(status_text)


def tray_main():
    global tray_icon
    menu = pystray.Menu(
        pystray.MenuItem("Open GlyphLink", show_window, default=True),
        pystray.MenuItem("Music On/Off", lambda i, x: toggle_music()),
        pystray.MenuItem("All Glyphs Off", lambda i, x: force_all_off()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit", quit_app),
    )
    tray_icon = pystray.Icon(APP_NAME, make_icon(), "GlyphLink", menu)
    tray_icon.run_detached()


def _write_crash_log(exc):
    try:
        import traceback
        d = app_data_dir()
        d.mkdir(parents=True, exist_ok=True)
        log = d / "glyphlink-crash.log"
        log.write_text(traceback.format_exc(), encoding="utf-8")
        return log
    except Exception:
        return None

def main():
    global audio_thread
    load_pattern_store()
    # Startup registration is handled by the installer. Avoid touching the registry
    # before the GUI is visible when running a standalone build.
    build_gui()
    root.update_idletasks()
    if "--setup-ready-file" in sys.argv:
        index = sys.argv.index("--setup-ready-file")
        Path(sys.argv[index + 1]).write_text(json.dumps({"ready": True}), encoding="utf-8")
    if "--ui-check" in sys.argv:
        index = sys.argv.index("--ui-check")
        Path(sys.argv[index + 1]).write_text(json.dumps({"ui_opened": True, "audio_import": sc is not None}), encoding="utf-8")
        root.after(1500, root.destroy)
        root.mainloop()
        return
    try:
        tray_main()
    except Exception:
        # Tray support should never prevent the main window from opening.
        pass
    threading.Thread(target=connection_manager, daemon=True).start()
    threading.Thread(target=heartbeat_manager, daemon=True).start()
    threading.Thread(target=phone_status_manager, daemon=True).start()
    threading.Thread(target=mode_hold_worker, daemon=True).start()
    if sc is not None:
        audio_thread = threading.Thread(target=audio_worker, daemon=True); audio_thread.start()
    if "--background" in sys.argv: root.withdraw()
    root.mainloop()

if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        log = _write_crash_log(exc)
        try:
            from tkinter import messagebox
            where = f"\n\nCrash log: {log}" if log else ""
            messagebox.showerror("GlyphLink failed to start", f"{type(exc).__name__}: {exc}{where}")
        except Exception:
            pass
        raise
