"""ShisaPoint (point-and-call desktop overlay; formerly Peek & Point / Desktop Lens) - click-through capture frame + assistant markup overlay (Windows).

Modes (config "mode"): region | monitor | window
Default hotkeys (Ctrl+Alt+...): L edit/lock (region), P pause, Q quit, M cycle monitor,
W snap to foreground window, K show/hide markup, C clear markup, O on/off, S save session,
G suggestion panel, F follow me. Rebind in View > Keyboard shortcuts... (config "hotkeys").
"""
import contextlib
import json
import re
import queue
import os
import sys
import threading
import time
from datetime import datetime

import tkinter as tk

IS_WIN = sys.platform == "win32"
if IS_WIN:
    import ctypes
    from ctypes import wintypes
    try:
        import mss
        from PIL import Image, ImageChops
        try:
            import pystray
        except ImportError:
            pystray = None
        MSS = getattr(mss, "MSS", None) or mss.mss
    except ImportError as e:  # pragma: no cover
        print("Missing dependency:", e, "-> pip install -r requirements.txt")
        sys.exit(1)

FROZEN = getattr(sys, "frozen", False)
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else sys.argv[0]))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
DEFAULTS = {
    "mode": "monitor",                # region | monitor | window
    "interval_seconds": 5,
    "output_dir": r"%USERPROFILE%\ShisaPoint\snapshots",
    "image_max_width": 1280,          # downscale saved snapshots to this width (0 = full size)
    "image_format": "jpg",            # jpg | png  (history + latest.<ext>)
    "jpeg_quality": 80,
    "privacy_pause_enabled": True,    # skip capture while a listed app is in front
    "privacy_pause_titles": ["1Password", "Bitwarden", "KeePass", "LastPass", "Bank",
                             "Credential Manager"],
    "show_region_mode": False,        # list Region under Mode (always shown while active)
    "tab_show_gear": True,
    "custom_interval_seconds": 0,     # set via Capture > Custom... (0 = none)            # tab item: gear menu (same menu as the tray)
    "max_images": 0,                  # 0 = no count limit
    "retention_minutes": 15,          # 0 = no age limit
    "max_cache_mb": 100,              # 0 = no size limit
    "session_format": "zip",
    "tab_show_interval": True,        # tab item: capture interval (click cycles)
    "tab_show_elapsed": True,         # tab item: elapsed LIVE recording time
    "tab_show_retention": False,      # tab item: history length (click cycles)
    "x": 200, "y": 200, "width": 640, "height": 400,
    "monitor_index": 1,               # mss index: 1 = first monitor, 2 = second...
    "window_title": "",               # substring to auto-find in window mode
    "border_color": "#00FF66",
    "edit_color": "#FF3366",
    "paused_color": "#FFAA00",
    "border_width": 3,
    "markup_enabled": True,
    "markup_file": "",                # default: <output_dir>/markup.json
    "markup_poll_ms": 300,
    "include_markup_in_capture": False,
    "tab_edge": "top",                # top | bottom | left | right
    "tab_offset": 0.0,                # 0..1 position along that edge
    # follow-along
    "capture_mode": "on_change",      # interval | on_change
    "settle_ms": 1200,                # on_change: screen must be stable this long
    "change_threshold": 0.002,        # fraction of downscaled pixels that must differ
    "min_gap_ms": 1500,               # on_change: minimum time between snaps
    "max_gap_s": 60,                  # on_change: fallback snap even w/o change (0 = never)
    "change_poll_ms": 250,
    "ocr_enabled": True,              # Windows.Media.Ocr -> latest.txt + meta.json
    "suggestions_enabled": True,      # suggestion panel (hotkey "panel")
    "suggestions_file": "",           # default: <output_dir>/suggestions.json
    "suggestions_poll_ms": 300,
    "suggestions_clickthrough": False,  # True = panel ignores the mouse (no dragging)
    "panel_x": None, "panel_y": None,   # dragged panel position (None = dock beside frame)
    "suggestions_history": 5,          # panel shows the first N of a file write (typos first)
    "custom_suggestions_history": 0,   # set via View > Suggestions > Last X...
    "ocr_upscale": True,               # enlarge capture before OCR (small UI text)
    "ocr_upscale_factor": 2.0,
    "confirm_ms": 2000,                # second look: still unchanged after this -> confirmed
    "hotkeys": None,                   # action -> "Ctrl+Alt+F" (None = defaults; see HOTKEY_ACTIONS)
    "max_continuous_s": 20,            # nonstop change this long -> forced 'checkin' snapshot
    "follow_max_continuous_s": 9,      # same, but while Follow me is on (faster feedback)
    "follow_limit_min": 15,            # Follow me session length (hotkey "follow")
    "custom_follow_limit_min": 0,
    "follow_webhook_url": "",          # POST JSON on Follow me events ("" = off)
    "follow_webhook_on_stop": True,
    "follow_webhook_on_confirm": False,
    "follow_copy_prompt": True,        # copy follow_prompt_template to clipboard on start
    "follow_autosave": False,          # save a follow_*.zip automatically when Follow me stops
    "follow_prompt_template": "Start watching ShisaPoint for {limit} minutes. Snapshots and "
                              "OCR text are in {output_dir} (meta.json, latest.jpg). "
                              "Reply via suggestions.json.",
    "ignore_taskbar_changes": True,    # leave the taskbar strip (clock) out of the change diff
    "suggestions_newest_bottom": False,
    "suggestions_clear_on_session": False,
    "app_display_name": "ShisaPoint",   # name shown in menus/About
    "support_url": "https://ko-fi.com/shisapoint",  # Ko-fi tip page
    "ignore_regions_px": [],           # [[x, y, w, h], ...] capture px left out of the change diff
    "auto_box_color": "#FFD400",       # box drawn around a suggestion's "target" phrase
    "markup_color": "",                # "" = as drawn; else colour for all assistant markup
    "theme": "system",                 # system | light | dark (panel, windows, menus)
    "indicator_edges": "all",          # all | top | top_bottom | sides | bottom
    "colorblind_mode": False,          # blue live / orange dashed stopped frame
    "secure_mode": False,              # DPAPI-encrypt snapshots/history/OCR/sessions at rest
    "secure_wipe_on_quit": True,       # secure mode: delete snapshots/history/OCR on quit
    "secure_wipe_sessions": False,     # ...and saved sessions too (default: keep, encrypted)
    "themed_menus": True,              # tray/gear menus drawn by the app (follow the theme);
                                       # false = plain Windows menus
    "markup_clear_after_s": 60,        # after a session ends: fade/clear markup (0 instant, -1 never)
    "custom_markup_clear_s": 0,
    "panel_hide_after_s": 60,          # after a session ends: fade out after N s (0 instant, -1 never)
    "custom_panel_hide_s": 0,  # Follow me start empties the panel  # False = newest suggestion at the top
    "follow_webhook_key": "",          # sent as "Authorization: Bearer <key>" ("" = none)
    "diff_width": 640,                 # change-detection thumbnail width (was 160)
    "diff_tile_px": 16,                # tile size on the thumbnail
    "diff_tile_min_px": 6,             # changed pixels in any one tile that count as a change
    "diff_pixel_delta": 24,            # grey-level difference that marks a pixel changed
    "show_in_screenshots": False,      # View > Show in screenshots. If True, windows use
                                       # WDA_NONE so they appear in external screenshots
                                       # (marketing). Own snapshots still hide the
                                       # frame/panel/markup during the grab. Default off.
}
APP_VERSION = "2026.11.16.1"
FOLLOW_PRESETS = (5, 15, 30, 60)
HISTORY_PRESETS = (1, 3, 5)
KEY = "#010203"   # chroma-key (fully transparent) color
GRIP = 12

GWL_EXSTYLE = -20
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x80000, 0x20, 0x80
WS_EX_NOACTIVATE = 0x08000000
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, WM_QUIT, WM_APP = 0x0312, 0x0012, 0x8000
WDA_NONE, WDA_EXCLUDEFROMCAPTURE = 0x0, 0x11
DWMWA_EXTENDED_FRAME_BOUNDS = 9
# (action, default combo, label) - order = hotkey id order and the shortcuts window order
HOTKEY_ACTIONS = (
    ("edit", "Ctrl+Alt+L", "Edit / lock frame"),
    ("pause", "Ctrl+Alt+P", "Pause / go live"),
    ("quit", "Ctrl+Alt+Q", "Quit"),
    ("monitor", "Ctrl+Alt+M", "Monitor mode / next monitor"),
    ("window", "Ctrl+Alt+W", "Snap to foreground window"),
    ("markup", "Ctrl+Alt+K", "Show / hide markup"),
    ("clear", "Ctrl+Alt+C", "Clear markup"),
    ("onoff", "Ctrl+Alt+O", "Turn on / off"),
    ("save", "Ctrl+Alt+S", "Save session"),
    ("panel", "Ctrl+Alt+G", "Show / hide suggestion panel"),
    ("follow", "Ctrl+Alt+F", "Follow me start / stop"),
)
DEFAULT_HOTKEYS = {a: c for a, c, _ in HOTKEY_ACTIONS}
APP_NAME = "ShisaPoint"          # default for config app_display_name

# ---- themes (panel, shortcuts/About windows, dialogs; menus via the Windows app mode)
THEMES = {
    "dark": dict(bg="#1E1E24", fg="#F2F2F5", dim="#9A9AA5", idle_fg="#6E6E78",
                 idle_dot="#6B6B75", accent="#38BDF8", field="#2A2A33", field_on="#33334A",
                 err="#FF8A8A", ok="#7EE2A8", warn="#FFB86B", link="#FF5C8A", subtle="#6E6E78",
                 button="#34343E", menu_bg="#2B2B2B", menu_hl="#414141", menu_sep="#454545",
                 menu_border="#4A4A4A", menu_dis="#7A7A7A"),
    "light": dict(bg="#F7F7FA", fg="#1E1E24", dim="#5F5F6B", idle_fg="#9A9AA5",
                  idle_dot="#A8A8B2", accent="#0369A1", field="#E4E4EA", field_on="#D3E5F5",
                  err="#B91C1C", ok="#15803D", warn="#B45309", link="#D6336C", subtle="#7A7A86",
                  button="#E4E4EA", menu_bg="#F9F9F9", menu_hl="#DCEBF8", menu_sep="#D6D6DC",
                  menu_border="#BDBDC4", menu_dis="#A0A0A8"),
}
THEME_CHOICES = (("system", "Match Windows"), ("light", "Light"), ("dark", "Dark"))
COLOR_PRESETS = (("Yellow", "#FFD400"), ("Cyan", "#00E5FF"), ("Magenta", "#FF3DCB"),
                 ("Lime", "#7CFF3D"), ("Orange", "#FF8A00"))
EDGE_CHOICES = (("all", "All four"), ("top", "Top only"), ("top_bottom", "Top and bottom"),
                ("sides", "Sides only"), ("bottom", "Bottom only"))
THICKNESS = (("Thin", 2), ("Normal", 3), ("Thick", 6))
# colorblind-safe frame palette: blue = live, orange (dashed) = stopped, white = edit
CB_LIVE, CB_LIVE_PULSE, CB_PAUSED, CB_EDIT = "#3B82F6", "#93C5FD", "#F59E0B", "#FFFFFF"


def lock_image(master, color="#F2C94C"):
    """Tiny 10x12 padlock PhotoImage (no emoji font needed)."""
    img = tk.PhotoImage(master=master, width=10, height=12)
    rows = ["..XXXXXX..", ".XX....XX.", ".X......X.", ".X......X.", ".X......X.",
            "XXXXXXXXXX", "XXXXXXXXXX", "XXXX..XXXX", "XXXX..XXXX", "XXXXXXXXXX",
            "XXXXXXXXXX", "XXXXXXXXXX"]
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch == "X":
                img.put(color, (x, y))
    return img


def windows_light_theme():
    """True if Windows apps use the light theme (HKCU ... Personalize AppsUseLightTheme)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return bool(winreg.QueryValueEx(k, "AppsUseLightTheme")[0])
    except (OSError, ImportError):
        return True


def set_dark_menus(dark, hwnds=()):
    """Native (tray + gear) popup menus follow dark/light via uxtheme's (undocumented but
    long-stable, Win10 1903+) SetPreferredAppMode/AllowDarkModeForWindow/FlushMenuThemes."""
    try:
        ux = ctypes.windll.uxtheme
        set_mode = ux[135]
        set_mode.argtypes, set_mode.restype = [ctypes.c_int], ctypes.c_int
        set_mode(2 if dark else 3)              # ForceDark / ForceLight
        allow = ux[133]
        allow.argtypes, allow.restype = [wintypes.HWND, wintypes.BOOL], wintypes.BOOL
        for h in hwnds:
            if h:
                allow(h, bool(dark))
        try:
            ux[104]()                           # RefreshImmersiveColorPolicyState
        except Exception:
            pass
        ux[136]()                               # FlushMenuThemes
        for h in hwnds:
            if h:
                ctypes.windll.user32.SendMessageW(h, 0x031A, 0, 0)   # WM_THEMECHANGED
        return True
    except Exception:
        return False


def dark_title_bar(win, dark):
    """Dark/light title bar for a Toplevel (DWMWA_USE_IMMERSIVE_DARK_MODE)."""
    try:
        win.update_idletasks()
        h = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        v = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(h, attr, ctypes.byref(v), 4) == 0:
                break
    except Exception:
        pass


def theme_tree(w, pal):
    """Recolor a plain tk widget tree (simpledialog etc.) with a theme palette."""
    try:
        cls = w.winfo_class()
        if cls in ("Toplevel", "Frame", "Tk"):
            w.configure(bg=pal["bg"])
        elif cls in ("Label", "Message"):
            w.configure(bg=pal["bg"], fg=pal["fg"])
        elif cls == "Entry":
            w.configure(bg=pal["field"], fg=pal["fg"], insertbackground=pal["fg"],
                        disabledbackground=pal["field"], relief="flat",
                        highlightthickness=1, highlightbackground=pal["subtle"],
                        highlightcolor=pal["accent"])
        elif cls == "Button":
            w.configure(bg=pal["button"], fg=pal["fg"], activebackground=pal["field_on"],
                        activeforeground=pal["fg"], relief="flat", padx=10)
        elif cls in ("Checkbutton", "Radiobutton"):
            w.configure(bg=pal["bg"], fg=pal["fg"], selectcolor=pal["field"],
                        activebackground=pal["bg"], activeforeground=pal["fg"])
    except tk.TclError:
        pass
    for c in w.winfo_children():
        theme_tree(c, pal)
HOTKEY_LABELS = {a: l for a, _, l in HOTKEY_ACTIONS}
HOTKEY_IDS = {a: i for i, (a, _, _) in enumerate(HOTKEY_ACTIONS, 1)}


def parse_combo(text):
    """'Ctrl+Alt+F' -> (mods, vk, 'Ctrl+Alt+F') normalized, or None. Needs Ctrl, Alt or Win;
    key = letter, digit or F1-F24."""
    parts = [p.strip() for p in str(text or "").replace("-", "+").split("+") if p.strip()]
    if not parts:
        return None
    mods, key = 0, parts[-1].upper()
    for p in parts[:-1]:
        q = p.lower()
        if q in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif q in ("alt", "menu"):
            mods |= MOD_ALT
        elif q == "shift":
            mods |= MOD_SHIFT
        elif q in ("win", "windows", "super", "meta"):
            mods |= MOD_WIN
        else:
            return None
    if len(key) == 1 and (key.isalpha() and key.isascii() or key.isdigit()):
        vk = ord(key)
    elif re.fullmatch(r"F([1-9]|1[0-9]|2[0-4])", key):
        vk = 0x70 + int(key[1:]) - 1
    else:
        return None
    if not mods & (MOD_CONTROL | MOD_ALT | MOD_WIN):
        return None
    return mods, vk, format_combo(mods, vk)


def format_combo(mods, vk):
    names = [n for m, n in ((MOD_CONTROL, "Ctrl"), (MOD_ALT, "Alt"), (MOD_SHIFT, "Shift"),
                            (MOD_WIN, "Win")) if mods & m]
    key = chr(vk) if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A else \
        f"F{vk - 0x6F}" if 0x70 <= vk <= 0x87 else None
    return "+".join(names + [key]) if key else None


def hotkey_map(cfg):
    """Current action -> combo map (defaults filled in; invalid entries fall back)."""
    out = dict(DEFAULT_HOTKEYS)
    user = cfg.get("hotkeys")
    if isinstance(user, dict):
        for a, v in user.items():
            if a in out:
                if v in ("", None):
                    out[a] = ""                    # explicitly unbound
                else:
                    pc = parse_combo(v)
                    if pc:
                        out[a] = pc[2]
    return out
HISTORY_LOG = "history.jsonl"         # one meta line per saved frame (for sessions)
MARKS_LOG = "marks.jsonl"             # on-screen boxes/marks over time (for sessions)
SCREEN_ANCHOR = {"left": 0, "top": 0, "scale": 1.0}   # markup with no anchor = screen px
RETENTION_PRESETS = (5, 15, 30)
INTERVAL_PRESETS = (5, 15, 60)
TAB_ITEMS = (("tab_show_interval", "Interval"), ("tab_show_elapsed", "Elapsed time"),
             ("tab_show_retention", "History length"), ("tab_show_gear", "Gear menu"))
IMG_EXTS = (".png", ".jpg", ".png.dpapi", ".jpg.dpapi")
# ---- secure mode: Windows DPAPI (CryptProtectData, current user) at rest
DP_MAGIC = b"PPDPAPI1\n"               # file = magic + DPAPI blob
DP_LINE = "PPDP1:"                      # history.jsonl line = prefix + base64(DPAPI blob)
SECURE = {"on": False}                  # module-wide switch (set by the app)


def _dpapi(data, protect=True):
    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(bytes(data), len(data))
    src = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    dst = BLOB()
    c = ctypes.windll.crypt32
    fn = c.CryptProtectData if protect else c.CryptUnprotectData
    ok = fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(dst))  # UI_FORBIDDEN
    if not ok:
        raise OSError(f"DPAPI {'protect' if protect else 'unprotect'} failed "
                      f"({ctypes.GetLastError()})")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


def dpapi_protect(data):
    return _dpapi(data, True)


def dpapi_unprotect(data):
    return _dpapi(data, False)


def write_secure(path, data):
    """Atomic write of magic + DPAPI(data)."""
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(DP_MAGIC + dpapi_protect(data))
    os.replace(tmp, path)


def read_maybe_secure(path):
    with open(path, "rb") as f:
        b = f.read()
    return dpapi_unprotect(b[len(DP_MAGIC):]) if b.startswith(DP_MAGIC) else b


def hist_encode(obj):
    line = json.dumps(obj)
    if SECURE["on"]:
        import base64
        line = DP_LINE + base64.b64encode(dpapi_protect(line.encode("utf-8"))).decode("ascii")
    return line + "\n"


def hist_decode(line):
    """dict for a history.jsonl line (plain or encrypted), None if unreadable."""
    line = line.strip()
    if not line:
        return None
    try:
        if line.startswith(DP_LINE):
            import base64
            line = dpapi_unprotect(base64.b64decode(line[len(DP_LINE):])).decode("utf-8")
        return json.loads(line)
    except (ValueError, OSError):
        return None


def wipe_history(folder, sessions=False):
    """Delete snapshots, latest.*, OCR text, history and follow state (secure mode quit)."""
    n = 0
    try:
        names = os.listdir(folder)
    except OSError:
        names = []
    for name in names:
        if (name.startswith(("lens_", "latest.", "latest_")) or
                name in (HISTORY_LOG, HISTORY_LOG + ".tmp", MARKS_LOG, MARKS_LOG + ".tmp",
                         "meta.secure.dpapi",
                         ".follow_last.txt", "meta.json.tmp")):
            try:
                os.remove(os.path.join(folder, name))
                n += 1
            except OSError:
                pass
    if sessions:
        d = os.path.join(folder, "sessions")
        try:
            for name in os.listdir(d):
                try:
                    os.remove(os.path.join(d, name))
                    n += 1
                except OSError:
                    pass
        except OSError:
            pass
    try:
        write_json_atomic(os.path.join(folder, "meta.json"),
                          {"note": "history wiped (secure mode)", "follow_active": False,
                           "secure": True, "ocr": None})
    except OSError:
        pass
    return n


def fmt_session(sec):
    """Compact, <=5 chars: 0:42 / 12:05 (m:ss) | 1:05h (h:mm)."""
    sec = int(sec)
    if sec < 3600:
        return f"{sec // 60}:{sec % 60:02d}"
    if sec < 360000:
        return f"{sec // 3600}:{sec % 3600 // 60:02d}h"
    return f"{sec // 3600}h"


def fmt_long(sec):
    sec = int(sec)
    h, m, s_ = sec // 3600, sec % 3600 // 60, sec % 60
    return (f"{h}h {m:02d}m {s_:02d}s" if h else f"{m}m {s_:02d}s" if m else f"{s_}s")
RES_DIR = getattr(sys, "_MEIPASS", APP_DIR)


def resource(*parts):
    return os.path.join(RES_DIR, *parts)
PATH_KEYS = ("output_dir", "markup_file")
LEGACY_OUTPUT = r"C:\DesktopLens"
LEGACY_OUTPUT2 = r"%USERPROFILE%\DesktopLens"   # pre-snapshots-subfolder default
LEGACY_OUTPUT3 = r"%USERPROFILE%\DesktopLens\snapshots"
LEGACY_CONFIGS = (
    r"%USERPROFILE%\DesktopLens\config.json",
    r"%USERPROFILE%\DesktopLens_old\config.json",
    r"C:\DesktopLens\config.json",
)


def _norm_path(v):
    return os.path.normcase(os.path.normpath(os.path.expanduser(os.path.expandvars(str(v or "")))))


def _is_legacy_output(val):
    """True if output_dir still points at a DesktopLens folder."""
    if not val:
        return False
    n = _norm_path(val)
    known = {_norm_path(p) for p in (
        LEGACY_OUTPUT, LEGACY_OUTPUT2, LEGACY_OUTPUT3, r"C:\DesktopLens\snapshots")}
    if n in known:
        return True
    base = os.path.basename(n).lower()
    parent = os.path.basename(os.path.dirname(n)).lower()
    return base == "desktoplens" or (base == "snapshots" and parent == "desktoplens")


def _legacy_config_path():
    here = os.path.normcase(os.path.abspath(CONFIG_PATH))
    for raw in LEGACY_CONFIGS:
        p = os.path.expanduser(os.path.expandvars(raw))
        if os.path.normcase(os.path.abspath(p)) == here:
            continue
        if os.path.isfile(p):
            return p
    return None


def _migrate_legacy_names(cfg, file_cfg):
    """Point old DesktopLens / Peek & Point values at ShisaPoint. Returns True if anything changed."""
    migrated = False
    if _is_legacy_output(cfg.get("output_dir", "")):
        cfg["output_dir"] = DEFAULTS["output_dir"]
        migrated = True
    for key in ("markup_file", "suggestions_file"):
        val = str(cfg.get(key) or "")
        if "desktoplens" in val.lower().replace("/", "\\"):
            cfg[key] = ""
            migrated = True
    name = str(cfg.get("app_display_name") or "")
    if name in ("Peek & Point", "Desktop Lens", "DesktopLens"):
        cfg["app_display_name"] = DEFAULTS["app_display_name"]
        migrated = True
    tpl = str(cfg.get("follow_prompt_template") or "")
    new_tpl = tpl.replace("Peek & Point", "ShisaPoint").replace("Desktop Lens", "ShisaPoint")
    if new_tpl != tpl:
        cfg["follow_prompt_template"] = new_tpl
        migrated = True
    if file_cfg is not None and "panel_x" not in file_cfg and cfg.get("suggestions_clickthrough"):
        cfg["suggestions_clickthrough"] = False
        migrated = True
    return migrated


# ---------------------------------------------------------------- helpers
class StampedLog:
    """File wrapper that prefixes every log line with a local timestamp."""
    def __init__(self, f):
        self.f = f
        self._bol = True
        self._lock = threading.Lock()

    def write(self, s):
        if not s:
            return 0
        with self._lock:
            out = []
            for part in s.splitlines(keepends=True):
                if self._bol and part.strip("\r\n"):
                    out.append(datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f")[:-3] + "] ")
                out.append(part)
                self._bol = part.endswith("\n")
            self.f.write("".join(out))
        return len(s)

    def flush(self):
        try:
            self.f.flush()
        except (OSError, ValueError):
            pass

    def __getattr__(self, name):
        return getattr(self.f, name)


def setup_logging(output_dir):
    """Redirect stdout/stderr to <output_dir>/shisapoint.log (needed for --noconsole)."""
    if not FROZEN and sys.stdout is not None and "--log" not in sys.argv:
        return
    try:
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, "shisapoint.log")
        if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
            os.replace(path, path + ".old")
        f = StampedLog(open(path, "a", encoding="utf-8", buffering=1))
        sys.stdout = sys.stderr = f
        print("\n=====", datetime.now().isoformat(timespec="seconds"), APP_NAME, "start =====")
    except OSError:
        pass


def load_config():
    cfg = dict(DEFAULTS)
    file_cfg = None
    imported_from = None
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                file_cfg = json.load(f)
                cfg.update(file_cfg)
        except (OSError, ValueError) as e:
            print("config.json unreadable, using defaults:", e)
    else:
        legacy = _legacy_config_path()
        if legacy:
            try:
                with open(legacy, "r", encoding="utf-8-sig") as f:
                    file_cfg = json.load(f)
                    cfg.update(file_cfg)
                imported_from = legacy
                print("Imported settings from", legacy)
            except (OSError, ValueError) as e:
                print("legacy config unreadable, using defaults:", e)
                save_config(cfg)
        else:
            save_config(cfg)  # create default config next to exe/script
    if cfg["mode"] not in ("region", "monitor", "window"):
        cfg["mode"] = "monitor"
    cfg["hotkeys"] = hotkey_map(cfg)            # fresh dict, never DEFAULTS' object
    migrated = _migrate_legacy_names(cfg, file_cfg)
    # keep raw (unexpanded) strings for saving; expand for use
    for k in PATH_KEYS:
        cfg["_raw_" + k] = cfg[k]
        cfg[k] = os.path.expanduser(os.path.expandvars(cfg[k])) if cfg[k] else cfg[k]
    if imported_from or migrated:
        if migrated:
            print("Migrated settings to", cfg.get("_raw_output_dir", cfg["output_dir"]))
        save_config(cfg)
    return cfg


def save_config(cfg):
    cfg = {k: cfg.get("_raw_" + k, v) if k in PATH_KEYS else v
           for k, v in cfg.items() if not k.startswith("_")}
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:   # plain UTF-8, no BOM
            json.dump(cfg, f, indent=2)
    except OSError as e:
        print("Could not save config:", e)


_APOS_SUFFIX = r"(s|ve|ll|re|d|t|m)"
_APOS_RE = [
    (re.compile(r"\b([A-Za-z]+);" + _APOS_SUFFIX + r"\b"), r"\1'\2"),       # she;s -> she's
    (re.compile(r"\b(I|you|we|they|he|she|it|that|there|who|what|don|can|isn|won|"
                r"didn|doesn|wasn|aren|couldn|wouldn|shouldn|haven|hasn|let)"
                r" " + _APOS_SUFFIX + r"\b", re.I), r"\1'\2"),                 # I ve -> I've
    (re.compile(r"\b(he|she|it|that|what|there|here|who|let);(?=\s|$)", re.I), r"\1's"),
]


def fix_apostrophes(text):
    """OCR often reads an apostrophe as ';' or a space. Excerpt/display text only."""
    for rx, rep in _APOS_RE:
        text = rx.sub(rep, text)
    return text


# lines that are only a time/date (taskbar clock) or editor status-bar noise; same as follow.ps1
CLOCK_RE = re.compile(r"^\s*(\d{1,2}[:.]\d{2}([:.]\d{2})?(\s*[AaPp]\.?\s*[Mm]\.?)?"
                      r"(\s+\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4})?|\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4})\s*$")
_NOISE_ITEM = (r"(Ln\s*\d+\s*,\s*Col\s*\d+|\d[\d,]*\s+characters?|\d{1,3}\s*%|Windows\s*\(CRLF\)"
               r"|Unix\s*\(LF\)|Macintosh\s*\(CR\)|UTF-?8(\s+with\s+BOM)?|UTF-16\s*(LE|BE)|ANSI"
               r"|Plain\s+text)")
NOISE_RE = re.compile(r"^\s*" + _NOISE_ITEM + r"(\s*[|\u00b7]?\s*" + _NOISE_ITEM + r")*\s*$",
                      re.IGNORECASE)


def write_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def set_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def history_files(folder):
    """[(name, mtime, size)] of lens_*.png in folder (top level only), oldest first."""
    out = []
    try:
        with os.scandir(folder) as it:
            for e in it:
                if e.is_file() and e.name.startswith("lens_") and e.name.endswith(IMG_EXTS):
                    st = e.stat()
                    out.append((e.name, st.st_mtime, st.st_size))
    except OSError:
        pass
    out.sort(key=lambda t: t[0])          # names are timestamp-sortable
    return out


def prune(folder, max_images=0, retention_minutes=0, max_cache_mb=0, now=None):
    """Delete oldest lens_*.png while ANY enabled limit is exceeded.
    Never touches latest.png, meta.json, markup.json, history.jsonl or sessions/.
    Returns (total_bytes, count) of what is left."""
    files = history_files(folder)
    now = time.time() if now is None else now
    total = sum(f[2] for f in files)
    cap = float(max_cache_mb) * 1024 * 1024
    cutoff = now - float(retention_minutes) * 60
    i = 0
    while i < len(files) - 1:             # always keep the newest frame
        name, mtime, size = files[i]
        n_left = len(files) - i
        if not ((max_images and n_left > int(max_images)) or
                (retention_minutes and mtime < cutoff) or
                (max_cache_mb and total > cap)):
            break
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass
        total -= size
        i += 1
    return total, len(files) - i


def name_ts(name):
    """epoch of a lens_YYYYmmdd_HHMMSS_mmm.* file name (local time), or None."""
    mo = re.match(r"lens_(\d{8}_\d{6})_(\d{3})", str(name or ""))
    if not mo:
        return None
    try:
        return datetime.strptime(mo.group(1), "%Y%m%d_%H%M%S").timestamp() + int(mo.group(2)) / 1000
    except ValueError:
        return None


def _iso(ts):
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds") \
        if ts is not None else None


def follow_ranges(folder, live=False):
    """Follow me sessions in the history: [{start, stop, reason, limit_min, live, legacy}]
    (epoch seconds, oldest first). From follow_start/follow_stop events in history.jsonl; older
    history without events falls back to runs of frames with follow_active true."""
    events, frames = [], []
    try:
        with open(os.path.join(folder, HISTORY_LOG), "r", encoding="utf-8") as f:
            for l in f:
                m = hist_decode(l)
                if not isinstance(m, dict):
                    continue
                ev = m.get("event")
                if ev in ("follow_start", "follow_stop"):
                    events.append(m)
                elif not ev and m.get("file"):
                    ts = _iso_ts(m.get("timestamp"), name_ts(m["file"]) or 0)
                    frames.append((ts, bool(m.get("follow_active"))))
    except OSError:
        pass
    events.sort(key=lambda e: float(e.get("ts", 0)))
    frames.sort()
    out, cur = [], None
    for e in events:
        ts = float(e.get("ts", 0))
        if e["event"] == "follow_start":
            if cur:                                   # start without a stop: app closed?
                cur.update(stop=ts, reason="not recorded")
                out.append(cur)
            cur = {"start": ts, "stop": None, "reason": None,
                   "limit_min": e.get("limit_min"), "live": False, "legacy": False}
        elif cur:
            cur.update(stop=ts, reason=e.get("reason"))
            out.append(cur)
            cur = None
    if cur:
        if live:
            cur["live"] = True
        else:
            after = [t for t, _ in frames if t >= cur["start"]]
            cur.update(stop=after[-1] if after else cur["start"], reason="not recorded")
        out.append(cur)
    first_ev = float(events[0].get("ts", 0)) if events else float("inf")
    run = None
    for ts, on in frames:                             # legacy history (before events)
        if ts >= first_ev:
            break
        if on:
            run = run or {"start": ts, "stop": ts, "reason": None, "limit_min": None,
                          "live": False, "legacy": True}
            run["stop"] = ts
        elif run:
            out.append(run)
            run = None
    if run:
        out.append(run)
    out.sort(key=lambda r: r["start"])
    return out


def _range_end(r):
    return (r["stop"] if r.get("stop") is not None else time.time()) + 1.5


def _iso_ts(iso, fallback):
    try:
        return datetime.fromisoformat(str(iso)).timestamp()
    except (TypeError, ValueError):
        return float(fallback)


def trim_history_log(folder):
    """Drop log lines whose PNG no longer exists."""
    path = os.path.join(folder, HISTORY_LOG)
    try:
        keep = {f[0] for f in history_files(folder)}
        with open(path, "r", encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        files = history_files(folder)
        cut = (name_ts(files[0][0]) or files[0][1]) if files else time.time()
        last_start = None
        decoded = [(hist_decode(l), l) for l in lines]
        for i, (m, _) in enumerate(decoded):
            if m and m.get("event") == "follow_start" and float(m.get("ts", 0)) < cut:
                last_start = i
        kept = []
        for i, (m, l) in enumerate(decoded):
            if m is None:
                continue
            if m.get("event") in ("follow_start", "follow_stop"):
                if float(m.get("ts", 0)) >= cut or i == last_start:
                    kept.append(l)                # Follow me ranges still covering frames
            elif m.get("file", m.get("of_file")) in keep:
                kept.append(l)
        if len(kept) != len(lines):
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(kept)
            os.replace(tmp, path)
    except OSError:
        pass


def _xf(src, dst):
    """Point mapper from src image px to dst image px; src/dst = {left, top, scale}."""
    ss = float(src.get("scale") or 1.0)
    ds = float(dst.get("scale") or 1.0)
    return lambda x, y: ((src["left"] + float(x) / ss - dst["left"]) * ds,
                         (src["top"] + float(y) / ss - dst["top"]) * ds)


def convert_mark(it, src, dst):
    """Copy of a markup item (public keys only) with its coords mapped src -> dst image px."""
    f = _xf(src, dst)
    k = float(dst.get("scale") or 1.0) / float(src.get("scale") or 1.0)
    r1 = lambda v: round(float(v), 1)
    o = {key: v for key, v in it.items() if not str(key).startswith("_")}
    if "x" in o and "y" in o:
        x, y = f(o["x"], o["y"])
        o["x"], o["y"] = r1(x), r1(y)
    for key in ("w", "h", "r"):
        if key in o:
            o[key] = r1(float(o[key]) * k)
    for a, b in (("x1", "y1"), ("x2", "y2")):
        if a in o and b in o:
            x, y = f(o[a], o[b])
            o[a], o[b] = r1(x), r1(y)
    if "points" in o:
        o["points"] = [[r1(v) for v in f(*pt)] for pt in o["points"]]
    return o


def read_marks_log(folder):
    """Decoded marks.jsonl records (plain or encrypted lines), oldest first."""
    out = []
    try:
        with open(os.path.join(folder, MARKS_LOG), "r", encoding="utf-8") as f:
            for l in f:
                m = hist_decode(l)
                if isinstance(m, dict) and "ts" in m:
                    out.append(m)
    except OSError:
        pass
    out.sort(key=lambda m: m.get("ts", 0))
    return out


def frame_rect(m):
    """{left, top, scale} of a history/meta record, or None."""
    try:
        s = m["screen_rect"]
        return {"left": s["left"], "top": s["top"], "scale": float(m["scale"])}
    except (KeyError, TypeError, ValueError):
        return None


def overlay_state_for(rec, dst):
    """A marks.jsonl record converted into a frame's image px (dst = frame_rect)."""
    src = rec.get("rect") or dst
    same = dst is None or src == dst
    conv = (lambda it: it) if same else (lambda it: convert_mark(it, src, dst))
    sugg = []
    for sg in rec.get("suggestions", []):
        sg = dict(sg)
        try:
            if sg.get("box"):
                x, y, w, h = sg["box"]
                b = conv({"x": x, "y": y, "w": w, "h": h})
                sg["box"] = [b["x"], b["y"], b["w"], b["h"]]
            if sg.get("circle"):
                x, y, r = sg["circle"]
                b = conv({"x": x, "y": y, "r": r})
                sg["circle"] = [b["x"], b["y"], b["r"]]
        except (TypeError, ValueError, KeyError):
            sg["box"] = sg["circle"] = None
        sugg.append(sg)
    marks = []
    for it in rec.get("marks", []):
        try:
            marks.append(conv(it))
        except (TypeError, ValueError, KeyError):
            pass
    return {"t": rec.get("t"), "from_file": rec.get("file"), "visible": rec.get("visible", True),
            "suggestions": sugg, "marks": marks}


def trim_marks_log(folder):
    """Drop marks records older than the oldest history frame (keep the one just before it,
    which is what that frame showed)."""
    path = os.path.join(folder, MARKS_LOG)
    if not os.path.exists(path):
        return
    try:
        files = history_files(folder)
        cut = files[0][1] if files else time.time()
        with open(path, "r", encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        recs = [(hist_decode(l), l) for l in lines]
        recs = [(m, l) for m, l in recs if isinstance(m, dict) and "ts" in m]
        before = [i for i, (m, _) in enumerate(recs) if m["ts"] < cut]
        start = before[-1] if before else 0
        kept = [l for _, l in recs[start:]]
        if len(kept) != len(lines):
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(kept)
            os.replace(tmp, path)
    except OSError:
        pass


def build_session(folder, fmt="zip", quality=80, secure=False, follow=None, live_follow=False):
    """Zip the current history (+manifest.json) into folder/sessions/. Returns (path, bytes, n).
    Encrypted snapshots are decrypted into the zip; secure=True encrypts the whole zip
    (session_*.zip.dpapi)."""
    import io
    import zipfile
    files = history_files(folder)
    metas = {}
    try:
        with open(os.path.join(folder, HISTORY_LOG), "r", encoding="utf-8") as f:
            for l in f:
                m = hist_decode(l)
                if m is None:
                    continue
                if m.get("event") == "confirm":           # late second-look result
                    if m.get("of_file") in metas:
                        metas[m["of_file"]].update({k: m.get(k) for k in (
                            "confirmed", "confirm_reason", "confirmed_at")})
                    continue
                if m.get("event"):                        # follow_start / follow_stop
                    continue
                metas[m.get("file")] = m
    except OSError:
        pass
    sess_dir = os.path.join(folder, "sessions")
    os.makedirs(sess_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(sess_dir, f"{'follow' if follow else 'session'}_{stamp}.zip")
    ranges = follow_ranges(folder, live_follow)
    if follow:                                  # only this Follow me session's frames
        lo, hi = follow["start"] - 0.5, _range_end(follow)
        files = [f for f in files
                 if lo <= _iso_ts(metas.get(f[0], {}).get("timestamp"),
                                  name_ts(f[0]) or f[1]) <= hi]
        if not files:
            raise ValueError("no snapshots left from that Follow me session")
    tmp = path + ".part"
    frames = []
    jpeg = fmt == "zip_jpeg"
    marks = read_marks_log(folder)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
        for name, mtime, size in files:
            src = os.path.join(folder, name)
            m = metas.get(name, {})
            plain = name[:-6] if name.endswith(".dpapi") else name
            arc = plain
            try:
                data = read_maybe_secure(src)
                if jpeg:
                    arc = plain[:-4] + ".jpg"
                    with Image.open(io.BytesIO(data)) as im:
                        buf = io.BytesIO()
                        im.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
                    z.writestr("frames/" + arc, buf.getvalue())
                else:
                    z.writestr("frames/" + arc, data)
            except (OSError, ValueError) as e:
                print("Session: skipped", name, e)
                continue
            frames.append({
                "file": "frames/" + arc, "source": name,
                "timestamp": m.get("timestamp") or datetime.fromtimestamp(mtime).astimezone()
                .isoformat(timespec="milliseconds"),
                "mode": m.get("mode"), "monitor_index": m.get("monitor_index"),
                "window_title": m.get("window_title"), "screen_rect": m.get("screen_rect"),
                "image_size": m.get("image_size"), "scale": m.get("scale"),
                "_ts": _iso_ts(m.get("timestamp"), mtime), "_rect": frame_rect(m)})
        # what was on screen while each frame was the latest one: the last marks record
        # before the next frame (boxes resolve a moment after their snapshot's OCR lands)
        first_rec = None
        for i, fr in enumerate(frames):
            end = frames[i + 1]["_ts"] if i + 1 < len(frames) else float("inf")
            rec = None
            for j, mr in enumerate(marks):
                if mr["ts"] < end:
                    rec = j
                else:
                    break
            if rec is not None:
                fr["overlay"] = overlay_state_for(marks[rec], fr["_rect"])
                if first_rec is None:
                    first_rec = rec
            else:
                fr["overlay"] = None
        used = []
        for r in ranges:                        # mark frames inside Follow me sessions
            idx = [i for i, fr in enumerate(frames) if r["start"] - 0.5 <= fr["_ts"] <= _range_end(r)]
            if not idx:
                continue
            for i in idx:
                frames[i]["follow"] = True
            used.append({"start": _iso(r["start"]), "stop": _iso(r["stop"]),
                         "stop_reason": "still running" if r.get("live") else r.get("reason"),
                         "limit_min": r.get("limit_min"), "live": bool(r.get("live")),
                         "inferred": bool(r.get("legacy")),
                         "first_frame": idx[0], "last_frame": idx[-1]})
        for fr in frames:
            fr.setdefault("follow", False)
            fr.pop("_ts", None)
            fr.pop("_rect", None)
        timeline = marks[first_rec:] if first_rec is not None else []
        if frames and timeline:
            z.writestr("marks.jsonl", "".join(json.dumps(mr) + "\n" for mr in timeline),
                       compress_type=zipfile.ZIP_DEFLATED)
        manifest = {"created": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "format": fmt, "jpeg_quality": quality if jpeg else None,
                    "frame_count": len(frames), "frames": frames,
                    "has_marks": bool(timeline), "version": 3,
                    "kind": "follow" if follow else "full",
                    "follow_ranges": used,
                    "follow": next((u for u in used if u["start"] == _iso(follow["start"])),
                                   None) if follow else None,
                    "note": "screen_x = screen_rect.left + image_x / scale. frames[].overlay = "
                            "suggestions (n, text, target, box [x,y,w,h] in that frame's px, "
                            "gone/old/stale) and markup marks shown while the frame was latest; "
                            "marks.jsonl = every change over time (coords in its own file's px, "
                            "rect = {left, top, scale} of that file). follow_ranges = Follow me "
                            "sessions (start/stop/stop_reason, first_frame/last_frame = 0-based "
                            "frame indexes); frames[].follow = inside one"}
        z.writestr("manifest.json", json.dumps(manifest, indent=2),
                   compress_type=zipfile.ZIP_DEFLATED)
    if secure:
        with open(tmp, "rb") as f:
            data = f.read()
        path += ".dpapi"
        write_secure(path, data)
        os.remove(tmp)
    else:
        os.replace(tmp, path)
    return path, os.path.getsize(path), len(frames)


def hwnd_of(widget):
    widget.update_idletasks()
    return ctypes.windll.user32.GetParent(widget.winfo_id())


def set_exstyle(hwnd, add=0, remove=0):
    u = ctypes.windll.user32
    get = getattr(u, "GetWindowLongPtrW", u.GetWindowLongW)
    put = getattr(u, "SetWindowLongPtrW", u.SetWindowLongW)
    style = (get(hwnd, GWL_EXSTYLE) | add) & ~remove
    put(hwnd, GWL_EXSTYLE, style)
    u.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)


def dwm_flat(hwnd):
    """Win11: no rounded corners, no DWM border colour, no non-client shadow."""
    try:
        dwm = ctypes.windll.dwmapi
        for attr, val in ((33, 1),            # WINDOW_CORNER_PREFERENCE = DONOTROUND
                          (34, 0xFFFFFFFE),   # BORDER_COLOR = DWMWA_COLOR_NONE
                          (2, 1)):            # NCRENDERING_POLICY = DISABLED
            v = ctypes.c_uint(val)
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
    except Exception:
        pass   # older Windows: attributes unsupported, harmless


def exclude_from_capture(hwnd, exclude=True):
    """True if Windows accepted the affinity (Win10 2004+)."""
    try:
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(
            hwnd, WDA_EXCLUDEFROMCAPTURE if exclude else WDA_NONE))
    except Exception:
        return False


def set_capture_exclusion(hwnd, cfg):
    """Apply configured capture affinity. True iff the window is hidden from captures."""
    exclude = not bool(cfg.get("show_in_screenshots", False))
    # Always apply the affinity. A prior `exclude and exclude_from_capture(...)`
    # short-circuit skipped SetWindowDisplayAffinity(WDA_NONE) when
    # show_in_screenshots was on, so _clean_own_grab could never restore
    # visibility after temporarily cloaking windows for an own grab.
    ok = exclude_from_capture(hwnd, exclude)
    return bool(exclude and ok)


def window_rect(hwnd):
    """Visible bounds (DWM extended frame, no invisible resize border)."""
    r = wintypes.RECT()
    try:
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r))
        if hr == 0:
            return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def window_title(hwnd):
    u = ctypes.windll.user32
    n = u.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def find_window(substr, skip=()):
    u = ctypes.windll.user32
    found = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        if h not in skip and u.IsWindowVisible(h) and \
                substr.lower() in window_title(h).lower():
            found.append(h)
            return False
        return True
    u.EnumWindows(proc(cb), 0)
    return found[0] if found else None


# ---------------------------------------------------------------- hotkeys
class HotkeyThread(threading.Thread):
    """Owns the global hotkeys (RegisterHotKey binds them to this thread). Other threads run
    register/unregister work here via call(); callback(action) on each press."""
    def __init__(self, callback, bindings):
        super().__init__(daemon=True)
        self.callback, self.tid = callback, None
        self.bindings = dict(bindings)         # action -> combo ("" = unbound)
        self.active = {}                       # action -> combo actually registered
        self.failed = {}                       # action -> combo that could not be registered
        self.suspended = False
        self.ready = threading.Event()
        self.jobs = queue.Queue()

    # -- in-thread helpers
    def _reg(self, action, combo):
        pc = parse_combo(combo)
        if not pc:
            return False
        ok = bool(ctypes.windll.user32.RegisterHotKey(None, HOTKEY_IDS[action],
                                                      pc[0] | MOD_NOREPEAT, pc[1]))
        if ok:
            self.active[action] = pc[2]
        return ok

    def _unreg(self, action):
        if self.active.pop(action, None) is not None:
            ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_IDS[action])

    def _unreg_all(self):
        for a in list(self.active):
            self._unreg(a)

    def _reg_all(self):
        self._unreg_all()
        self.failed = {}
        for a, combo in self.bindings.items():
            if combo and not self._reg(a, combo):
                self.failed[a] = combo
                print(f"Warning: {combo} ({HOTKEY_LABELS.get(a, a)}) is in use by another app")
        return dict(self.failed)

    def run(self):
        u = ctypes.windll.user32
        self.tid = ctypes.windll.kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)     # create the message queue
        self._reg_all()
        self.ready.set()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                a = next((k for k, v in HOTKEY_IDS.items() if v == msg.wParam), None)
                if a and not self.suspended:
                    self.callback(a)
            elif msg.message == WM_APP + 1:
                while True:
                    try:
                        fn, box, ev = self.jobs.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        box.append(fn())
                    except Exception as e:
                        box.append(e)
                    ev.set()
        self._unreg_all()

    def call(self, fn, timeout=2.0):
        """Run fn on the hotkey thread and return its result (None on timeout)."""
        if not self.ready.wait(timeout) or not self.is_alive():
            return None
        box, ev = [], threading.Event()
        self.jobs.put((fn, box, ev))
        ctypes.windll.user32.PostThreadMessageW(self.tid, WM_APP + 1, 0, 0)
        if not ev.wait(timeout) or not box:
            return None
        r = box[0]
        if isinstance(r, Exception):
            print("Hotkey change failed:", r)
            return None
        return r

    # -- public API (any thread)
    def apply(self, bindings):
        """Replace all bindings and re-register; returns {action: combo} that failed."""
        def job():
            self.bindings = dict(bindings)
            return self._reg_all()
        return self.call(job)

    def try_bind(self, action, combo):
        """Move one action to combo. On failure the old binding is restored. Returns True/False."""
        def job():
            old = self.active.get(action)
            self._unreg(action)
            if self._reg(action, combo):
                self.bindings[action] = combo
                self.failed.pop(action, None)
                return True
            if old:
                self._reg(action, old)
            return False
        return bool(self.call(job))

    def suspend(self):
        """Unregister everything (so the shortcuts window can see the keys)."""
        def job():
            self.suspended = True
            self._unreg_all()
            return True
        return self.call(job)

    def resume(self):
        def job():
            self.suspended = False
            return self._reg_all()
        return self.call(job)

    def stop(self):
        if self.tid:
            ctypes.windll.user32.PostThreadMessageW(self.tid, WM_QUIT, 0, 0)


# ---------------------------------------------------------------- markup overlay
class MarkupOverlay:
    """Full-virtual-desktop, click-through canvas that renders markup.json."""

    def __init__(self, app):
        self.app, self.cfg = app, app.cfg
        self.path = self.cfg["markup_file"] or os.path.join(self.cfg["output_dir"], "markup.json")
        self.items, self.mtime, self.visible = [], None, True
        self.anchor = None          # capture rect the coords refer to
        v = app.virtual             # {"left","top","width","height"}
        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=KEY)
        self.win.attributes("-transparentcolor", KEY)
        self.win.geometry(f"{v['width']}x{v['height']}+{v['left']}+{v['top']}")
        self.cv = tk.Canvas(self.win, bg=KEY, highlightthickness=0)
        self.cv.pack(fill="both", expand=True)
        self.hwnd = hwnd_of(self.win)
        set_exstyle(self.hwnd, add=WS_EX_LAYERED | WS_EX_TRANSPARENT |
                    WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        self.excluded = False
        if not self.cfg["include_markup_in_capture"]:
            dwm_flat(self.hwnd)
            self.excluded = set_capture_exclusion(self.hwnd, self.cfg)
            if not self.excluded and not self.cfg.get("show_in_screenshots"):
                print("WDA_EXCLUDEFROMCAPTURE unavailable; overlay will be hidden during grabs")
        self.poll()

    # -- file watching
    def poll(self):
        try:
            try:
                m = os.path.getmtime(self.path)
                if m != self.mtime:
                    self.mtime = m
                    self.load()
            except OSError:
                pass
            except Exception as e:      # bad content must not stop the poll loop
                err = f"{type(e).__name__}: {e}"
                if err != getattr(self, "_load_err", None):
                    self._load_err = err
                    print("markup.json unusable:", err)
            now = time.time()
            before = len(self.items)
            self.items = [i for i in self.items if i["_exp"] is None or i["_exp"] > now]
            if len(self.items) != before:
                self.redraw()
        finally:
            self.win.after(int(self.cfg["markup_poll_ms"]), self.poll)

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8-sig") as f:   # BOM or not
                data = json.load(f)
        except (OSError, ValueError):
            self.mtime = None          # half-written; retry next poll
            return
        self._load_err = None
        if isinstance(data, list):
            data = {"items": data}
        if not isinstance(data, dict):
            raise ValueError("top level must be an object or a list")
        if data.get("clear") or data.get("replace", True):
            self.items = []
        self.anchor = data.get("anchor") or self.app.last_meta_rect()
        now = time.time()
        try:      # TTL counts from when the file was written, not when we read it,
            born = os.path.getmtime(self.path)   # so stale markups never resurrect
        except OSError:
            born = now
        born = min(born, now)
        if born < getattr(self.app, "started", 0):
            print("markup.json is from before launch; ignored")
            self.items = []
            self.redraw()
            return
        for it in data.get("items", []):
            if not isinstance(it, dict):
                continue
            ttl = it.get("ttl_seconds", data.get("ttl_seconds"))
            try:
                it["_exp"] = born + float(ttl) if ttl else None
            except (TypeError, ValueError):
                it["_exp"] = None
            if it["_exp"] is not None and it["_exp"] <= now:
                continue
            it["_anchor"] = self.anchor
            self.items.append(it)
        if getattr(self.app, "panel", None):
            self.app.panel.pointers()        # re-evaluate one-mark-per-suggestion
        else:
            self.redraw()

    def fade_clear(self, step=0):
        """Quick alpha fade (~300 ms), then clear all markup (ignores item ttl)."""
        steps = 8
        if step < steps and self.items:
            try:
                self.win.attributes("-alpha", 1.0 - (step + 1) / steps)
            except tk.TclError:
                pass
            self.win.after(35, lambda: self.fade_clear(step + 1))
            return
        self.clear()
        try:
            self.win.attributes("-alpha", 1.0)
        except tk.TclError:
            pass

    def clear(self):
        self.items = []
        try:
            write_json_atomic(self.path, {"clear": True, "items": []})
            self.mtime = os.path.getmtime(self.path)
        except OSError:
            pass
        self.redraw()

    def toggle(self):
        self.visible = not self.visible
        self.redraw()

    def reapply(self):
        self.win.update_idletasks()
        self.hwnd = hwnd_of(self.win)
        self.win.attributes("-transparentcolor", KEY)
        self.win.attributes("-topmost", True)
        set_exstyle(self.hwnd, add=WS_EX_LAYERED | WS_EX_TRANSPARENT |
                    WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        if not self.cfg["include_markup_in_capture"]:
            dwm_flat(self.hwnd)
            self.excluded = set_capture_exclusion(self.hwnd, self.cfg)

    def hide_for_capture(self):
        return not self.cfg["include_markup_in_capture"] and not self.excluded and self.visible

    # -- drawing
    def map_pt(self, it, x, y):
        a = it["_anchor"] or {"left": 0, "top": 0, "scale": 1.0}
        s = a.get("scale", 1.0) or 1.0
        v = self.app.virtual
        return (a["left"] + x / s - v["left"], a["top"] + y / s - v["top"])

    def redraw(self):
        cv = self.cv
        cv.delete("all")
        if self.visible:
            for it in self.items:
                try:
                    self.draw(it)
                except (KeyError, TypeError, ValueError) as e:
                    print("Bad markup item", it.get("type"), e)
        self.app.log_marks()                 # record what is on screen (sessions)

    def draw(self, it):
        cv, t = self.cv, it.get("type", "rect")
        col = it.get("color", "#FF0000")
        if it.get("_auto"):
            col = str(self.cfg.get("auto_box_color") or "#FFD400")
        elif self.cfg.get("markup_color"):
            col = str(self.cfg["markup_color"])
        if col.lower() == KEY:
            col = "#020304"
        w = float(it.get("width", 3))
        P = lambda x, y: self.map_pt(it, x, y)
        if t in ("rect", "circle", "ellipse"):
            x0, y0 = P(it["x"], it["y"])
            if t == "circle" and "r" in it:
                cx, cy = P(it["x"], it["y"])
                r = float(it["r"]) / (it["_anchor"] or {}).get("scale", 1.0)
                x0, y0, x1, y1 = cx - r, cy - r, cx + r, cy + r
            else:
                x1, y1 = P(it["x"] + it["w"], it["y"] + it["h"])
            fn = cv.create_rectangle if t == "rect" else cv.create_oval
            fn(x0, y0, x1, y1, outline=col, width=w)
            if it.get("label"):
                cv.create_text(x0, y0 - 4, anchor="sw", text=it["label"], fill=col,
                               font=("Segoe UI", int(it.get("font_size", 12)), "bold"))
        elif t in ("line", "arrow"):
            x0, y0 = P(it["x1"], it["y1"])
            x1, y1 = P(it["x2"], it["y2"])
            kw = {"arrow": tk.LAST, "arrowshape": (4 * w + 6, 4 * w + 8, 1.5 * w + 3)} \
                if t == "arrow" else {}
            cv.create_line(x0, y0, x1, y1, fill=col, width=w, capstyle="round", **kw)
        elif t in ("polyline", "freehand"):
            pts = []
            for x, y in it["points"]:
                pts.extend(P(x, y))
            if len(pts) >= 4:
                cv.create_line(*pts, fill=col, width=w, smooth=bool(it.get("smooth", True)),
                               capstyle="round", joinstyle="round")
        elif t == "text":
            x, y = P(it["x"], it["y"])
            size = int(it.get("font_size", 16))
            tid = cv.create_text(x, y, anchor="nw", text=it["text"], fill=col,
                                 font=("Segoe UI", size, "bold"))
            if it.get("background"):
                bx = cv.bbox(tid)
                rid = cv.create_rectangle(bx[0] - 4, bx[1] - 2, bx[2] + 4, bx[3] + 2,
                                          fill=it["background"], outline="")
                cv.tag_lower(rid, tid)
        else:
            raise ValueError("unknown type")


# ---------------------------------------------------------------- OCR
class Ocr:
    """Windows.Media.Ocr via the winrt Python projection. Never raises."""

    def __init__(self):
        self.engine, self.error = None, None
        try:
            from winrt.windows.media.ocr import OcrEngine
            from winrt.windows.graphics.imaging import (SoftwareBitmap, BitmapPixelFormat,
                                                        BitmapAlphaMode)
            from winrt.windows.storage.streams import DataWriter
            self._sb, self._fmt, self._alpha, self._dw = (SoftwareBitmap, BitmapPixelFormat,
                                                          BitmapAlphaMode, DataWriter)
            self.engine = OcrEngine.try_create_from_user_profile_languages()
            if self.engine is None:
                self.error = "no OCR language installed"
            else:
                self.max_dim = int(OcrEngine.max_image_dimension or 10000)
                self.lang = self.engine.recognizer_language.language_tag
                print("OCR ready:", self.lang)
        except Exception as e:
            self.engine, self.error = None, f"{type(e).__name__}: {e}"
        if self.error:
            print("OCR unavailable:", self.error)

    def recognize(self, img, upscale=1.0):
        """img: PIL RGB. Returns {"text", "lines":[{"text","words":[{text,x,y,w,h}]}]}.
        upscale > 1 enlarges (LANCZOS) first so small text reads better; word boxes
        are always returned in the input image's pixels."""
        if not self.engine:
            return None
        import asyncio
        f = max(1.0, float(upscale or 1.0))
        f = min(f, self.max_dim / float(max(img.size)))
        if f != 1.0:
            img = img.resize((max(1, int(img.width * f)), max(1, int(img.height * f))),
                             Image.LANCZOS if f > 1 else Image.BILINEAR)
        rgba = img.convert("RGBA")
        r, g, b, a = rgba.split()
        data = Image.merge("RGBA", (b, g, r, a)).tobytes()
        w = self._dw()
        w.write_bytes(data)
        buf = w.detach_buffer()
        # pywinrt 3.x: 4-arg overload is create_copy_from_buffer; the 5-arg (alpha)
        # overload is exposed separately as create_copy_with_alpha_from_buffer.
        if hasattr(self._sb, "create_copy_with_alpha_from_buffer"):
            bmp = self._sb.create_copy_with_alpha_from_buffer(
                buf, self._fmt.BGRA8, rgba.width, rgba.height, self._alpha.PREMULTIPLIED)
        else:
            bmp = self._sb.create_copy_from_buffer(buf, self._fmt.BGRA8, rgba.width, rgba.height)

        async def run():
            return await self.engine.recognize_async(bmp)
        res = asyncio.run(run())
        lines = []
        for ln in res.lines:
            words = []
            for wd in ln.words:
                rc = wd.bounding_rect
                words.append({"text": wd.text, "x": round(rc.x / f), "y": round(rc.y / f),
                              "w": round(rc.width / f), "h": round(rc.height / f)})
            lines.append({"text": ln.text, "words": words})
        return {"text": "\n".join(l["text"] for l in lines), "lines": lines,
                "language": self.lang, "upscale": round(f, 3)}


# ---------------------------------------------------------------- suggestion panel
class SuggestionPanel:
    """Topmost, capture-excluded panel showing suggestions.json."""
    BG, FG, DIM, W = "#1E1E24", "#F2F2F5", "#9A9AA5", 300
    LIVE_DOT, IDLE_DOT, IDLE_FG, ACC = "#22C55E", "#6B6B75", "#6E6E78", "#38BDF8"

    def apply_theme(self, pal, live_dot="#22C55E"):
        self.BG, self.FG, self.DIM = pal["bg"], pal["fg"], pal["dim"]
        self.IDLE_FG, self.IDLE_DOT, self.ACC = pal["idle_fg"], pal["idle_dot"], pal["accent"]
        self.LIVE_DOT = live_dot
        for w in (self.win, self.head, self.dot, self.title, self.body):
            w.configure(bg=self.BG)
        self.refresh_header()
        self.render()

    def __init__(self, app):
        self.app, self.cfg = app, app.cfg
        self.path = self.cfg["suggestions_file"] or os.path.join(self.cfg["output_dir"],
                                                                 "suggestions.json")
        self.items, self.mtime = [], None     # items = full history (oldest first)
        self._drag = None
        self.placing = False                  # show even when empty so it can be placed
        self.sess = 0                         # Follow me session counter (items carry _sess)
        self.sess_label = None                # "HH:MM" of the current session start
        self.status = None                    # local status line ("Watching...")
        self.idle = True                      # no live Follow me session -> greyed panel
        self.faded = False                    # faded out after a session ended
        self._hide_job = None
        self.visible = bool(self.cfg.get("suggestions_enabled", True))
        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.92)
        self.win.configure(bg=self.BG)
        self.head = tk.Frame(self.win, bg=self.BG)
        self.head.pack(fill="x")
        # live dot: green while a Follow me session is live, grey when idle (tab colours)
        self.dot = tk.Label(self.head, text="\u25CF", bg=self.BG, fg=self.IDLE_DOT,
                            font=("Segoe UI", 8, "bold"), padx=0, pady=3)
        self.dot.pack(side="left", padx=(8, 3))
        self.title = tk.Label(self.head, text=app.display_name(), anchor="w", bg=self.BG,
                              fg=self.DIM, font=("Segoe UI", 8, "bold"), pady=3)
        self.title.pack(side="left", fill="x", expand=True)
        self.bind_drag(self.dot)
        self.bind_drag(self.title)
        self.body = tk.Frame(self.win, bg=self.BG)
        self.body.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.bind_drag(self.head)
        self.bind_drag(self.body)
        self.reapply()
        self.win.withdraw()
        self.shown = False
        self.poll()

    def reapply(self):
        self.win.update_idletasks()
        self.hwnd = hwnd_of(self.win)
        add = WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED
        rem = 0
        if self.cfg.get("suggestions_clickthrough", False):
            add |= WS_EX_TRANSPARENT
        else:
            rem = WS_EX_TRANSPARENT
        set_exstyle(self.hwnd, add=add, remove=rem)
        self.head.configure(cursor="arrow" if self.cfg.get("suggestions_clickthrough")
                            else "fleur")
        dwm_flat(self.hwnd)
        set_capture_exclusion(self.hwnd, self.cfg)

    # -- drag (header or body); position saved as panel_x / panel_y
    def bind_drag(self, w):
        w.bind("<ButtonPress-1>", self.on_press, add="+")
        w.bind("<B1-Motion>", self.on_drag, add="+")
        w.bind("<ButtonRelease-1>", self.on_release, add="+")

    def on_press(self, ev):
        self._drag = (ev.x_root, ev.y_root, self.win.winfo_x(), self.win.winfo_y())

    def on_drag(self, ev):
        if not self._drag:
            return
        sx, sy, x, y = self._drag
        self.win.geometry(f"+{x + ev.x_root - sx}+{y + ev.y_root - sy}")

    def on_release(self, ev):
        if not self._drag:
            return
        sx, sy, _, _ = self._drag
        self._drag = None
        if abs(ev.x_root - sx) + abs(ev.y_root - sy) < 3:
            return
        self.cfg["panel_x"], self.cfg["panel_y"] = self.win.winfo_x(), self.win.winfo_y()
        save_config(self.cfg)
        self._geo = None

    def reset_position(self):
        self.cfg["panel_x"] = self.cfg["panel_y"] = None
        save_config(self.cfg)
        self.dock(force=True)

    def saved_pos(self, h):
        """Saved position clamped onto a current monitor (monitors may change)."""
        px, py = self.cfg.get("panel_x"), self.cfg.get("panel_y")
        if px is None or py is None:
            return None
        try:
            px, py = int(px), int(py)
        except (TypeError, ValueError):
            return None
        mons = self.app.monitors[1:]
        cx, cy = px + self.W // 2, py + 10
        mon = next((m for m in mons if m["left"] <= cx < m["left"] + m["width"]
                    and m["top"] <= cy < m["top"] + m["height"]), None)
        if mon is None:                       # off every monitor: nearest one
            mon = min(mons, key=lambda m: abs(m["left"] + m["width"] / 2 - cx) +
                      abs(m["top"] + m["height"] / 2 - cy))
        x = max(mon["left"], min(px, mon["left"] + mon["width"] - self.W))
        y = max(mon["top"], min(py, mon["top"] + mon["height"] - min(h, mon["height"])))
        return x, y

    def limit(self):
        try:
            return max(1, int(self.cfg.get("suggestions_history", 5) or 1))
        except (TypeError, ValueError):
            return 5

    def _batch_items(self):
        """Latest file-write batch (file order) plus older items used to fill the limit."""
        n = self.limit()
        if not self.items:
            return [], []
        bid = max((i.get("_batch") or 0) for i in self.items)
        current = [i for i in self.items if (i.get("_batch") or 0) == bid][:n]
        older = [i for i in self.items if (i.get("_batch") or 0) != bid]
        fill = older[-(n - len(current)):] if len(current) < n else []
        return current, fill

    def shown_items(self):
        """Items the panel will display (length <= limit). Latest batch is first N of
        the file (typos first), not the last N (which used to drop leading typos)."""
        current, fill = self._batch_items()
        return fill + current

    def display_items(self):
        """Latest file batch stays in file order (typos first). Older history sits
        below unless suggestions_newest_bottom (then above)."""
        current, fill = self._batch_items()
        if self.cfg.get("suggestions_newest_bottom", False):
            return fill + current
        return current + list(reversed(fill))

    def poll(self):
        try:
            try:
                m = os.path.getmtime(self.path)
                if m != self.mtime:
                    self.mtime = m
                    self.load()
            except OSError:
                pass
            except Exception as e:      # bad content must not stop the poll loop
                err = f"{type(e).__name__}: {e}"
                if err != getattr(self, "_load_err", None):
                    self._load_err = err
                    print("suggestions.json unusable:", err)
            now = time.time()
            n = len(self.items)
            self.items = [i for i in self.items if i["_exp"] is None or i["_exp"] > now]
            if len(self.items) != n:
                self.render()
            else:
                self.dock()
        except Exception as e:
            print("Suggestion panel poll failed:", e)
        finally:
            self.win.after(int(self.cfg.get("suggestions_poll_ms", 300)), self.poll)

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8-sig") as f:   # BOM or not
                data = json.load(f)
            if not isinstance(data, (dict, list)):
                raise ValueError("top level must be an object or a list")
        except (OSError, ValueError) as e:
            self.mtime = None          # half-written / bad; retry next poll
            err = f"{type(e).__name__}: {e}"
            if not isinstance(e, FileNotFoundError) and err != getattr(self, "_load_err", None):
                self._load_err = err   # once per distinct failure
                print("suggestions.json unreadable:", err)
            return
        self._load_err = None
        if isinstance(data, list):
            data = {"items": data}
        if data.get("clear"):
            self.items = []
        # replace=true starts a new batch; older batches stay as history (last N shown)
        new = []
        now = time.time()
        try:
            file_t = os.path.getmtime(self.path)
        except OSError:
            file_t = now
        for it in data.get("items", []):
            if isinstance(it, str):
                it = {"text": it}
            if not isinstance(it, dict) or not it.get("text"):
                continue
            ttl = it.get("ttl_seconds", data.get("ttl_seconds"))
            try:
                it["_exp"] = now + float(ttl) if ttl else None
            except (TypeError, ValueError):
                it["_exp"] = None
            it["_sess"] = self.sess
            # anchors are saved-image px of the snapshot current when the suggestion arrived
            it["_anchor"] = self.app.last_meta_rect() if self.app.meta else None
            it["_meta"] = self.app.meta     # its ocr may land a moment later (same dict)
            it["_stale"] = file_t < getattr(self.app, "started", 0)   # previous run
            it["_batch"] = getattr(self, "_load_n", 0) + 1
            new.append(it)
        self._load_n = getattr(self, "_load_n", 0) + 1
        if data.get("replace", True):
            for old in self.items:
                old["_old"] = True
        sig = json.dumps([str(i.get("text")) for i in new])
        fresh = bool(new) and sig != getattr(self, "_last_sig", None)
        print(f"suggestions.json loaded: {len(new)} item(s)"
              f"{'' if fresh or not new else ' (unchanged)'}"
              f"{', clear' if data.get('clear') else ''}")
        self._last_sig = sig
        self.items = (self.items + new)[-50:]
        if self.status == "Starting assistant\u2026":
            self.status = "Watching\u2026"     # first suggestions.json write: assistant is up
        elif new:
            self.status = None                # later writes: items speak for themselves
        if fresh and self.faded:
            self.faded = False
            self.render()
            self.fade_in()                    # new content while auto-hidden: come back
            return
        self.render()

    def pointers(self):
        ov = self.app.overlay
        if not ov:
            return
        ov.items = [i for i in ov.items if not i.get("_sugg")]
        marks = [i for i in ov.items if not i.get("_sugg")]

        def covered(n, a):
            """markup.json already marks this suggestion (same label or rect around it)."""
            for m in marks:
                if str(m.get("label", "")).strip() == str(n):
                    return True
                try:
                    if m.get("type", "rect") in ("rect", "ellipse") and \
                            m["x"] <= a[0] <= m["x"] + m["w"] and m["y"] <= a[1] <= m["y"] + m["h"]:
                        return True
                except (KeyError, TypeError):
                    pass
            return False
        # numbering = position in the panel's current display order (same as the panel tags)
        for n, it in enumerate(self.display_items(), 1):
            if it.get("_old") or it.get("_stale") or it.get("_anchor") is None:
                continue                     # only current, this-run suggestions get marks
            if str(it.get("mark", "circle")).lower() == "none":
                continue
            if it.get("target"):             # auto box around the phrase (no circle)
                box = self.find_target(it)
                if box:
                    x0, y0, x1, y1 = box
                    ov.items.append({"type": "rect", "x": x0, "y": y0, "w": x1 - x0,
                                     "h": y1 - y0, "width": 2, "label": str(n),
                                     "color": str(self.cfg.get("auto_box_color") or "#FFD400"),
                                     "_anchor": it["_anchor"], "_exp": it["_exp"],
                                     "_sugg": True, "_auto": True})
                    continue
                if not isinstance(it.get("anchor"), (list, tuple)):
                    continue
            a = it.get("anchor")
            if not (isinstance(a, (list, tuple)) and len(a) == 2):
                continue
            if covered(n, a):
                continue
            ov.items.append({"type": "circle", "x": a[0], "y": a[1], "r": 14,
                             "color": it.get("color", "#38BDF8"), "width": 3,
                             "label": str(n), "_anchor": it["_anchor"], "_exp": it["_exp"],
                             "_sugg": True})
        ov.redraw()

    @staticmethod
    def _norm(t):
        return "".join(ch for ch in str(t).lower() if ch.isalnum())

    @staticmethod
    def _snorm(t):
        """Like _norm but keeps apostrophes, so a fixed its -> it's counts as changed."""
        t = str(t).lower().replace("\u2019", "'").replace("\u2018", "'").replace("`", "'")
        return "".join(ch for ch in t if ch.isalnum() or ch == "'").strip("'")

    def retrack(self, meta):
        """New snapshot OCR landed: move each visible target box to where its phrase is now,
        or hide it when the phrase is gone (fixed). Panel text stays; its number dims."""
        ov = self.app.overlay
        if not ov or not ov.visible or not (meta or {}).get("ocr"):
            return
        now = time.time()
        live = [it for it in self.display_items()
                if it.get("target") and not it.get("_old") and not it.get("_stale")
                and it.get("_anchor") is not None
                and str(it.get("mark", "circle")).lower() != "none"
                and (it.get("_exp") is None or it["_exp"] > now)]
        if not live:
            return                            # no boxes on screen: skip the matching work
        s = meta["screen_rect"]
        anchor = {"left": s["left"], "top": s["top"], "scale": meta["scale"]}
        changed = False
        for it in live:
            if it.get("_meta") is meta:
                continue
            it["_meta"], it["_anchor"], it["_tracking"] = meta, anchor, True
            gone = self.find_target(it) is None
            if "_strict0" not in it:
                continue                      # never found yet: may still show up later
            if gone != bool(it.get("_gone")):
                it["_gone"] = gone
                changed = True
                print(f"Target {'gone (hidden)' if gone else 'back'}: "
                      + ("(text hidden, secure mode)" if SECURE["on"] else repr(it["target"])))
        if changed:
            self.render()                     # dims/undims numbers; calls pointers()
        else:
            self.pointers()

    def find_target(self, it):
        """Box (saved-image px, +3 px pad) around it["target"] in its snapshot's OCR words.
        Exact token sequence first, then fuzzy (normalized, difflib ratio >= 0.6,
        similar length). None if not found."""
        import difflib
        meta = it.get("_meta") or {}
        ocr = meta.get("ocr") if isinstance(meta, dict) else None
        if not ocr:
            return None                      # OCR not ready yet; re-run when it lands
        if "_box" in it and it.get("_box_ocr") is ocr:
            return it["_box"]
        toks = [self._norm(t) for t in str(it["target"]).split()]
        toks = [t for t in toks if t]
        lines = [[w for w in ln.get("words", []) if self._norm(w.get("text", ""))]
                 for ln in ocr.get("lines", [])]
        order = list(range(len(lines)))
        hint = it.get("line_hint")
        if isinstance(hint, int) and 0 <= hint < len(lines):
            order.remove(hint)
            order.insert(0, hint)
        elif isinstance(hint, str) and hint.strip():
            h = self._norm(hint)
            order.sort(key=lambda i: h not in self._norm(
                " ".join(w["text"] for w in lines[i])))
        best, found = 0.0, None
        tracking = bool(it.get("_tracking"))
        stoks = [self._snorm(t) for t in str(it["target"]).split()]
        stoks = [t for t in stoks if t]
        strict = None                        # exact match keeping apostrophes
        if stoks:
            for i in order:
                ws = lines[i]
                nt = [self._snorm(w["text"]) for w in ws]
                for k in range(len(ws) - len(stoks) + 1):
                    if nt[k:k + len(stoks)] == stoks:
                        strict = ws[k:k + len(stoks)]
                        break
                if strict:
                    break
        if strict:
            found = strict
        elif tracking and it.get("_strict0"):
            toks = []                        # was exact before; now it isn't -> fixed / gone
        if toks and not found:
            target = " ".join(toks)
            for i in order:                  # exact token sequence
                ws = lines[i]
                nt = [self._norm(w["text"]) for w in ws]
                for k in range(len(ws) - len(toks) + 1):
                    if nt[k:k + len(toks)] == toks:
                        found = ws[k:k + len(toks)]
                        break
                if found:
                    break
            if not found:                    # fuzzy, window length +-1
                for i in order:
                    ws = lines[i]
                    nt = [self._norm(w["text"]) for w in ws]
                    for L in {max(1, len(toks) - 1), len(toks), len(toks) + 1}:
                        for k in range(len(ws) - L + 1):
                            cand = " ".join(nt[k:k + L])
                            if not approx_same_len(target, cand):
                                continue
                            r = difflib.SequenceMatcher(None, target, cand).ratio()
                            if r > best + 1e-9:
                                best, found = r, ws[k:k + L]
                if best < FUZZY_TARGET_MIN:
                    found = None
        box = None
        if found:
            if "_strict0" not in it:
                it["_strict0"] = bool(strict)  # how it matched the first time
            box = (min(w["x"] for w in found) - 3, min(w["y"] for w in found) - 3,
                   max(w["x"] + w["w"] for w in found) + 3,
                   max(w["y"] + w["h"] for w in found) + 3)
        elif not tracking:
            print("Target not found on screen: "
                  + ("(text hidden, secure mode)" if SECURE["on"] else repr(it["target"]))
                  + (f" (best fuzzy {best:.2f})" if best else ""))
        it["_box"], it["_box_ocr"] = box, ocr
        return box

    def render(self):
        for c in self.body.winfo_children():
            c.destroy()
        a = self.app
        shown = self.shown_items()
        self.refresh_header()
        bottom = self.cfg.get("suggestions_newest_bottom", False)
        clear = self.cfg.get("suggestions_clear_on_session", False)

        def line(text, fg, font, sep=False):
            if sep:
                f = tk.Frame(self.body, bg=self.DIM, height=1)
                f.pack(fill="x", pady=(4, 0))
            lab = tk.Label(self.body, text=text, bg=self.BG, fg=fg, anchor="w",
                           font=font)
            lab.pack(fill="x", pady=(0, 2))
            self.bind_drag(lab)
        status = (lambda: line(self.status, self.DIM if self.idle else self.ACC,
                               ("Segoe UI", 9, "italic"))) \
            if self.status else None
        if status and not bottom:
            status()
        if not shown and not self.status:
            ph = tk.Label(self.body, text="Suggestions appear here\n(drag to place)",
                          bg=self.BG, fg=self.DIM, justify="left", anchor="w",
                          font=("Segoe UI", 9, "italic"))
            ph.pack(fill="x", pady=4)
            self.bind_drag(ph)
        shown = self.display_items()
        divider_done = not self.sess_label or clear
        for n, it in enumerate(shown, 1):
            old = it.get("_sess", 0) < self.sess
            if not divider_done and (old if not bottom else not old):
                if not bottom:              # newest on top: divider above the old ones
                    line(f"New session started {self.sess_label}", self.DIM,
                         ("Segoe UI", 8, "bold"), sep=True)
                    divider_done = True
            if bottom and not divider_done and not old:
                line(f"New session started {self.sess_label}", self.DIM,
                     ("Segoe UI", 8, "bold"), sep=True)
                divider_done = True
            row = tk.Frame(self.body, bg=self.BG)
            row.pack(fill="x", pady=1)
            marked = (it.get("anchor") or it.get("target")) and not it.get("_stale")
            tag = str(n) if marked else "\u2022"
            tk.Label(row, text=tag, bg=self.BG,
                     fg=self.ACC if marked and not it.get("_gone") else self.DIM,
                     font=("Segoe UI", 9, "bold"), width=2, anchor="n").pack(side="left", anchor="n")
            lbl = tk.Label(row, text=str(it["text"]), bg=self.BG,
                           fg=self.IDLE_FG if (self.idle or it.get("_stale")) else
                           self.DIM if (it.get("_old") or it.get("_sess", 0) < self.sess)
                           else self.FG, justify="left",
                           anchor="w", wraplength=self.W - 40, font=("Segoe UI", 9))
            lbl.pack(side="left", fill="x", expand=True)
            for w in (row, lbl):
                self.bind_drag(w)
        if not divider_done:                # no items of the other side: divider at the end
            if not bottom or not shown:
                line(f"New session started {self.sess_label}", self.DIM,
                     ("Segoe UI", 8, "bold"), sep=bool(shown))
            else:
                line(f"New session started {self.sess_label}", self.DIM,
                     ("Segoe UI", 8, "bold"), sep=True)
        if status and bottom:
            status()
        self.pointers()
        self.dock(force=True)

    def new_session(self):
        """Follow me started: status line + divider (or a cleared panel)."""
        self.sess += 1
        self.sess_label = datetime.now().strftime("%H:%M")
        if self.cfg.get("suggestions_clear_on_session", False):
            self.items = []
            self.status = f"Session started {self.sess_label}"
        else:
            self.status = "Starting assistant\u2026"
        self.cancel_hide()
        self.idle = False
        self.render()

    def refresh_header(self):
        """Dot + title only (cheap); called on pause/unpause/session start/stop."""
        a = self.app
        live = bool(a.follow_until) and a.state() == "live"
        self.dot.configure(fg=self.LIVE_DOT if live else self.IDLE_DOT)
        sec = bool(self.cfg.get("secure_mode"))
        if sec and getattr(self, "_lock_img", None) is None:
            self._lock_img = lock_image(self.win, "#F2C94C")
        self.title.configure(image=self._lock_img if sec else "", compound="left")
        self.title.configure(fg=self.FG if live else self.DIM,
                             text=(" " if sec else "") +
                             f"{a.display_name()} \u00B7 Suggestions ({len(self.shown_items())})" +
                             ("" if live else "  \u00B7 idle"))

    def fade_in(self, step=0):
        steps = 8
        if step == 0:
            self.cancel_hide()
            self.win.attributes("-alpha", 0.0)
            self.dock(force=True)
        if step < steps:
            self.win.attributes("-alpha", 0.92 * (step + 1) / steps)
            self._hide_job = self.win.after(35, lambda: self.fade_in(step + 1))
            return
        self._hide_job = None
        if not self.app.follow_until:
            self.schedule_hide()                 # idle: hide again after the delay

    # -- hide after session
    def cancel_hide(self):
        if self._hide_job:
            try:
                self.win.after_cancel(self._hide_job)
            except tk.TclError:
                pass
            self._hide_job = None
        if self.faded:
            self.faded = False
            self.win.attributes("-alpha", 0.92)

    def schedule_hide(self):
        self.cancel_hide()
        try:
            d = float(self.cfg.get("panel_hide_after_s", 60))
        except (TypeError, ValueError):
            d = 60
        if d < 0:
            return                                   # never
        self._hide_job = self.win.after(int(d * 1000), self.fade_out)

    def fade_out(self, step=0):
        """Quick alpha fade (~300 ms), then hide until the next session / Show panel."""
        self._hide_job = None
        if self.placing or not self.idle:
            return
        steps = 8
        if step < steps:
            self.win.attributes("-alpha", 0.92 * (1 - (step + 1) / steps))
            self._hide_job = self.win.after(35, lambda: self.fade_out(step + 1))
            return
        self.faded = True
        self.dock(force=True)
        try:
            self.app.panel_faded()
        except Exception as e:
            print("Markup clear failed:", e)
        self.win.attributes("-alpha", 0.92)

    def end_session(self, reason="stopped"):
        """Follow me ended (Stop, hotkey or expiry): idle style, suggestions kept."""
        self.idle = True
        self.status = f"Session ended {datetime.now().strftime('%H:%M')}" + (
            f" ({reason})" if reason and reason != "stopped" else "")
        self.render()
        self.schedule_hide()

    def toggle_placing(self):
        self.placing = not self.placing
        if self.placing:
            self.cancel_hide()
        if self.placing and not self.visible:
            self.visible = True
            self.cfg["suggestions_enabled"] = True
            save_config(self.cfg)
        self.render()

    def toggle(self):
        self.visible = not self.visible
        self.cfg["suggestions_enabled"] = self.visible
        save_config(self.cfg)
        self.dock(force=True)

    def dock(self, force=False):
        want = self.visible and (bool(self.items) or self.placing or bool(self.status)) \
            and not self.app.off and (not self.faded or self.placing)
        if not want:
            if self.shown:
                self.win.withdraw()
                self.shown = False
            return
        rc = self.app.capture_rect() or {"left": 100, "top": 100, "width": 400, "height": 300}
        self.win.update_idletasks()
        h = self.win.winfo_reqheight()
        mon = self.app.monitors[self.app.monitor_at(rc["left"] + rc["width"] // 2,
                                                    rc["top"] + rc["height"] // 2)]
        mr = mon["left"] + mon["width"]
        gap = int(self.cfg["border_width"]) + 6
        x = rc["left"] + rc["width"] + gap                # outside, right of frame
        if x + self.W > mr:
            x = rc["left"] + rc["width"] - self.W - gap   # inside, right edge
        y = rc["top"] + gap + 30
        pos = self.saved_pos(h)
        if pos:
            x, y = pos
        if self._drag:
            return
        geo = f"{self.W}x{h}+{int(x)}+{int(y)}"
        if force or geo != getattr(self, "_geo", None) or not self.shown:
            self.win.geometry(geo)
            self._geo = geo
        if not self.shown:
            self.win.deiconify()
            self.reapply()
            self.shown = True
        self.win.attributes("-topmost", True)


# ---------------------------------------------------------------- control tab
class ControlTab:
    """Compact, clickable, always-on-top tab attached to the frame.

    Layout: [grip][* LIVE][5s][full][wrap][x]. Dragging anywhere on the tab
    moves the frame; a press without movement is a click on that segment.
    """
    H, R = 28, 9
    BG, FG, DIM, HI, HOVER = "#1E1E24", "#D8D8E0", "#8A8A96", "#3D7BFF", "#44444E"
    INTERVALS = INTERVAL_PRESETS
    # (key, width, tooltip)
    GRIP_BG = "#34343E"
    TAIL = [("full", 26, "Full screen (click again: next monitor)"),
            ("wrap", 26, "Wrap app: click a window to track it"),
            ("close", 26, "Quit " + APP_NAME),
            ("grip", 16, "Drag to slide the tab along the frame edge")]
    LIVE_TIP = "Live / stopped (click to toggle)"
    IV_TIP = "Capture interval (click to cycle presets)"
    GEAR = ("gear", 24, "Settings menu (same as tray)")

    def build_segs(self):
        c = self.app.cfg
        segs = [("dot", 20, self.LIVE_TIP)]                    # live dot: always shown
        if c.get("secure_mode"):
            segs.append(("lock", 16, "Secure mode: snapshots and OCR text encrypted, "
                                     "history wiped on quit"))
        if self.app.follow_until:
            segs.append(("follow", 50, "Follow me: time left (click to stop)"))
        if c.get("tab_show_interval", True):
            segs.append(("interval", 30, self.IV_TIP))
        if c.get("tab_show_elapsed", True):
            segs.append(("elapsed", 42, None))
        if c.get("tab_show_retention", False):
            segs.append(("retention", 34, None))
        tail = list(self.TAIL)
        if c.get("tab_show_gear", True):
            tail.insert(2, self.GEAR)                          # before close
        self.SEGS = segs + tail
        self.W = sum(w for _, w, _ in self.SEGS) + 6

    def relayout(self):
        self.build_segs()
        self.cv.config(width=self.W)
        self.win.geometry(f"{self.W}x{self.H}")
        self.refresh()
        self.follow()

    def __init__(self, app):
        self.app = app
        self.build_segs()
        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 1.0)   # opaque: outline must match the frame colour exactly
        self.win.configure(bg=KEY, bd=0, highlightthickness=0)
        self.win.attributes("-transparentcolor", KEY)
        self.cv = tk.Canvas(self.win, width=self.W, height=self.H, bg=KEY,
                            highlightthickness=0, bd=0, relief="flat", cursor="fleur")
        self.cv.pack(padx=0, pady=0, ipadx=0, ipady=0)
        self.tip = self.hover = self.press = self.picking = None
        self.dragging = False
        self.sliding = False
        self.pulse_on = False
        self.cv.bind("<Motion>", self.on_motion)
        self.cv.bind("<Leave>", lambda e: (self.set_hover(None), self.hide_tip()))
        self.cv.bind("<ButtonPress-1>", self.on_press)
        self.cv.bind("<B1-Motion>", self.on_drag)
        self.cv.bind("<ButtonRelease-1>", self.on_release)
        self.win.geometry(f"{self.W}x{self.H}+0+0")
        self.hwnd = hwnd_of(self.win)
        set_exstyle(self.hwnd, add=WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        dwm_flat(self.hwnd)
        set_capture_exclusion(self.hwnd, self.app.cfg)
        self.refresh()
        self.follow()

    def reapply(self):
        self.win.update_idletasks()
        self.hwnd = hwnd_of(self.win)
        self.win.attributes("-transparentcolor", KEY)
        self.win.attributes("-topmost", True)
        set_exstyle(self.hwnd, add=WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        dwm_flat(self.hwnd)
        set_capture_exclusion(self.hwnd, self.app.cfg)

    def hwnds(self):
        hs = {self.hwnd}
        if self.tip:
            hs.add(hwnd_of(self.tip))
        return hs

    # -- geometry helpers
    def seg_x(self, key):
        x = 3
        for k, w, _ in self.SEGS:
            if k == key:
                return x, w
            x += w
        return None

    def seg_at(self, px):
        x = 3
        for k, w, _ in self.SEGS:
            if x <= px < x + w:
                return k
            x += w
        return None

    def rounded(self, x0, y0, x1, y1, r, square=(), **kw):
        """Rounded rect; corners named in `square` ('tl','tr','br','bl') stay sharp
        (a corner point repeated 3x makes the smoothed spline turn sharply)."""
        def corner(name, cx, cy, ax, ay, bx, by):
            if name in square:
                return [cx, cy, cx, cy, cx, cy]
            return [ax, ay, cx, cy, bx, by]
        pts = (corner("tl", x0, y0, x0, y0 + r, x0 + r, y0) +
               corner("tr", x1, y0, x1 - r, y0, x1, y0 + r) +
               corner("br", x1, y1, x1, y1 - r, x1 - r, y1) +
               corner("bl", x0, y1, x0 + r, y1, x0, y1 - r))
        return self.cv.create_polygon(pts, smooth=True, **kw)

    SQUARE = {"top": ("bl", "br"), "bottom": ("tl", "tr"),
              "left": ("tr", "br"), "right": ("tl", "bl")}

    # -- drawing
    def refresh(self):
        cv, a = self.cv, self.app
        cv.delete("all")
        # state-coloured outline (same colour/width as the frame border). When the
        # tab is attached outside, the side touching the frame is square and its
        # outline strip lies exactly on the frame border, so both read as one shape.
        ow = self.ow = max(2, int(a.cfg["border_width"]))
        att = getattr(self, "attached", None)
        sq = self.SQUARE.get(att, ())
        col = a.color()
        W, H = self.W, self.H
        # Tk polygons leave the last row/column empty; on the joining side draw
        # past the window edge (+2, clipped) so there is no 1 px seam.
        ex = {"top": (0, 0, 0, 2), "bottom": (0, -2, 0, 0),
              "left": (0, 0, 2, 0), "right": (-2, 0, 0, 0)}.get(att, (0, 0, 0, 0))
        self.rounded(ex[0], ex[1], W - 1 + ex[2], H - 1 + ex[3], self.R, square=sq,
                     fill=col, outline="")
        self.rounded(ow, ow, W - 1 - ow, H - 1 - ow, max(2, self.R - ow),
                     square=sq, fill=self.BG, outline="")
        # bridge: solid frame-coloured strip on the joining side, exactly as thick
        # as the overlap, so the frame border runs straight through under the tab
        ov = self.overlap
        bridge = {"top": (0, H - ov, W + 1, H + 1), "bottom": (0, -1, W + 1, ov),
                  "left": (W - ov, 0, W + 1, H + 1), "right": (-1, 0, ov, H + 1)}.get(att)
        if bridge:
            cv.create_rectangle(*bridge, fill=col, outline="", width=0)
        gx, gw = self.seg_x("grip")
        gfill = "#4A4A58" if (self.hover == "grip" or self.sliding) else self.GRIP_BG
        self.rounded(gx, ow, self.W - 1 - ow, self.H - 1 - ow, max(2, self.R - ow),
                     square=tuple(c for c in sq if c in ("tr", "br")), fill=gfill, outline="")
        cv.create_line(gx, ow + 2, gx, self.H - ow - 2, fill="#55555F")
        for gy in range(8, self.H - 6, 4):                     # ridged dot grid
            for ddx in (5, 10):
                cv.create_oval(gx + ddx - 1, gy - 1, gx + ddx + 1, gy + 1,
                               fill="#A0A0AC", outline="")
        m, live = a.cfg["mode"], not a.paused
        cy = self.H / 2
        for k, w, _ in self.SEGS:
            x, _ = self.seg_x(k)
            cx = x + w / 2
            active = (k == "full" and m == "monitor") or \
                     (k == "wrap" and (m == "window" or self.picking))
            if k == "grip":
                pass
            elif active or self.hover == k:
                fill = self.HOVER
                if active:
                    fill = "#E0A000" if (k == "wrap" and self.picking) else self.HI
                self.rounded(x + 1, ow + 1, x + w - 1, self.H - ow - 2, 5, fill=fill, outline="")
            if k == "grip":
                continue
            elif k == "dot":
                if live and a.privacy:
                    dot, r = "#B57CFF", 4.5                     # privacy pause
                elif live:
                    dot = a.live_colors()[1 if self.pulse_on else 0]
                    r = 6 if self.pulse_on else 4.5
                else:
                    dot, r = "#6B6B75", 4.5
                cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=dot, outline="")
            elif k == "elapsed":
                cv.create_oval(x - 1.5, cy - 1.5, x + 1.5, cy + 1.5, fill="#7A7A86", outline="")  # sep dot
                cv.create_text(cx + 1, cy, text=fmt_session(a.session_elapsed()),
                               fill=self.FG if live else self.DIM, font=("Consolas", 9, "bold"))
            elif k == "lock":
                col = "#F2C94C"
                cv.create_arc(cx - 3.5, cy - 7, cx + 3.5, cy, start=0, extent=180, style="arc",
                              outline=col, width=1.6)
                cv.create_rectangle(cx - 5, cy - 2.5, cx + 5, cy + 5, fill=col, outline="")
                cv.create_rectangle(cx - 0.6, cy, cx + 0.8, cy + 2.5, fill="#1E1E24", outline="")
            elif k == "follow":
                cv.create_text(cx, cy, text="\u25C9" + fmt_session(a.follow_remaining()),
                               fill="#38BDF8", font=("Consolas", 9, "bold"))
            elif k == "retention":
                cv.create_oval(x - 1.5, cy - 1.5, x + 1.5, cy + 1.5, fill="#7A7A86", outline="")  # sep dot
                cv.create_text(cx + 1, cy, text=a.retention_label(),
                               fill=self.FG, font=("Segoe UI", 9, "bold"))
            elif k == "live":
                if live:
                    dot = a.live_colors()[1 if self.pulse_on else 0]
                    r = 6 if self.pulse_on else 4.5
                    cv.create_oval(x + 9 - r, cy - r, x + 9 + r, cy + r, fill=dot, outline="")
                    cv.create_text(x + 19, cy, anchor="w", text="LIVE", fill=a.live_colors()[0],
                                   font=("Segoe UI", 9, "bold"))
                else:
                    cv.create_oval(x + 5, cy - 4.5, x + 14, cy + 4.5, fill="#6B6B75", outline="")
                    cv.create_text(x + 19, cy, anchor="w", text="STOPPED", fill=self.DIM,
                                   font=("Segoe UI", 7, "bold"))
            elif k == "interval":
                iv = float(a.cfg["interval_seconds"])
                txt = "\u0394" if a.cfg.get("capture_mode") == "on_change" else f"{iv:g}s"
                cv.create_text(cx, cy, text=txt, fill="#FFFFFF" if self.pulse_on else self.FG,
                               font=("Segoe UI", 9, "bold"))
            elif k == "full":
                cv.create_text(cx, cy, text="\u26F6", fill=self.FG, font=("Segoe UI Symbol", 11))
                if m == "monitor":
                    cv.create_text(x + w - 3, self.H - 4, anchor="se",
                                   text=str(a.cfg["monitor_index"]), fill="#FFF", font=("Segoe UI", 6))
            elif k == "wrap":
                cv.create_text(cx, cy, text="\u29C9", fill=self.FG, font=("Segoe UI Symbol", 11))
                if self.picking:
                    secs = max(0, int(self.picking - time.time()) + 1)
                    cv.create_text(x + w - 3, self.H - 4, anchor="se", text=str(secs),
                                   fill="#FFF", font=("Segoe UI", 6))
            elif k == "gear":
                cv.create_text(cx, cy, text="\u2699", font=("Segoe UI Symbol", 11),
                               fill="#FFFFFF" if self.hover == "gear" else self.FG)
            elif k == "close":
                cv.create_text(cx, cy, text="\u2715", font=("Segoe UI Symbol", 10),
                               fill="#FF6B6B" if self.hover == "close" else self.FG)

    def pulse(self):
        """Brief blink on each capture."""
        self.pulse_on = True
        self.refresh()
        self.win.after(180, self._unpulse)

    def flash(self, n=6):
        """Longer blink (session saved)."""
        if n <= 0:
            return self._unpulse()
        self.pulse_on = not self.pulse_on
        self.refresh()
        self.win.after(150, lambda: self.flash(n - 1))

    def _unpulse(self):
        self.pulse_on = False
        self.refresh()

    def set_hover(self, key):
        if key != self.hover:
            self.hover = key
            self.refresh()

    # -- tooltip
    def show_tip(self, key):
        self.hide_tip()
        text = next((t for k, _, t in self.SEGS if k == key), None)
        if text is None:                    # segment gone (e.g. Follow me just ended)
            return
        if key == "elapsed":
            text = (f"Live time: {fmt_long(self.app.session_elapsed())}, "
                    f"{getattr(self.app, 'session_frames', 0)} frames "
                    "(resets on Save session / Clear history)")
        if key == "dot" and self.app.privacy:
            text = f"Privacy pause: '{self.app.privacy}' is in front - not capturing"
        if key == "follow":
            text = (f"Follow me: {fmt_long(self.app.follow_remaining())} left "
                    f"(click{self.app.hk_or('follow')} to stop)")
        if key == "retention":
            text = (f"History kept: {self.app.retention_label(long=True)} "
                    f"(click to cycle) | {self.app.cache_label()}")
        self.tip = tk.Toplevel(self.win)
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        tk.Label(self.tip, text=text, bg="#FFFFE0", fg="#000", font=("Segoe UI", 9),
                 padx=5, pady=2, relief="solid", bd=1).pack()
        x = self.win.winfo_x() + self.seg_x(key)[0]
        y = self.win.winfo_y() + self.H + 4
        self.tip.geometry(f"+{x}+{y}")
        th = hwnd_of(self.tip)
        set_exstyle(th, add=WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT | WS_EX_LAYERED)
        set_capture_exclusion(th, self.app.cfg)

    def toast(self, text, ms=1800):
        t = tk.Toplevel(self.win)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        tk.Label(t, text=text, bg="#38BDF8", fg="#000", font=("Segoe UI", 9, "bold"),
                 padx=8, pady=3).pack()
        t.geometry(f"+{self.win.winfo_x()}+{self.win.winfo_y() + self.H + 4}")
        try:
            th = hwnd_of(t)
            set_exstyle(th, add=WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT |
                        WS_EX_LAYERED)
            set_capture_exclusion(th, self.app.cfg)
        except Exception:
            pass
        t.after(ms, t.destroy)

    def hide_tip(self):
        if self.tip:
            self.tip.destroy()
            self.tip = None

    # -- events: whole body drags, press+release without movement = click
    def on_motion(self, ev):
        key = self.seg_at(ev.x)
        if key != self.hover:
            self.set_hover(key)
            self.hide_tip()
            if key and not self.press:
                self.win.after(500, lambda k=key: self.hover == k and not self.press
                               and self.show_tip(k))
        self.cv.config(cursor="size" if key == "grip" else "fleur" if key is None else "hand2")

    def on_press(self, ev):
        self.hide_tip()
        self.press = (ev.x_root, ev.y_root, self.seg_at(ev.x))
        self.dragging = False
        self.sliding = self.press[2] == "grip"
        if self.sliding:
            self.refresh()

    def on_drag(self, ev):
        if not self.press:
            return
        sx, sy, _ = self.press
        if self.sliding:
            return self.slide_to(ev.x_root, ev.y_root)
        if not self.dragging:
            if abs(ev.x_root - sx) + abs(ev.y_root - sy) < 4:
                return
            self.dragging = True
            self.app.to_region_here()
            fx, fy, _, _ = self.app.frame_rect()
            self.start = (fx, fy)
        fx, fy = self.start
        _, _, w, h = self.app.frame_rect()
        self.app.set_geo(w, h, fx + ev.x_root - sx, fy + ev.y_root - sy)
        self.follow()

    def on_release(self, ev):
        press, self.press = self.press, None
        if self.sliding:
            self.sliding = False
            save_config(self.app.cfg)
            self.refresh()
            return
        if self.dragging:
            self.dragging = False
            self.app.remember_geometry()
            return
        key = press[2] if press else None
        if key in ("live", "dot"):
            self.app.toggle_pause()
        elif key == "interval":
            self.app.cycle_interval(self.app.interval_steps())
        elif key == "retention":
            self.app.cycle_retention()
        elif key == "follow":
            self.app.stop_follow()
        elif key == "full":
            self.app.full_screen()
        elif key == "wrap":
            self.start_pick()
        elif key == "gear":
            self.refresh()
            gx, gw = self.seg_x("gear")
            wx, wy = self.win.winfo_rootx(), self.win.winfo_rooty()
            return self.app.popup_menu((wx + gx, wy, wx + gx + gw, wy + self.H))
        elif key == "close":
            return self.app.quit()
        self.refresh()

    # -- wrap-app picking: next left click on another window (10 s timeout)
    def start_pick(self):
        if self.picking:
            self.picking = None
            return self.refresh()
        self.picking = time.time() + 10
        self._was_down = True
        self.refresh()
        self.win.after(50, self.poll_pick)

    def poll_pick(self):
        if not self.picking:
            return
        u = ctypes.windll.user32
        down = bool(u.GetAsyncKeyState(0x01) & 0x8000)   # VK_LBUTTON
        if down and not self._was_down:
            pt = wintypes.POINT()
            u.GetCursorPos(ctypes.byref(pt))
            h = u.WindowFromPoint(pt)
            h = u.GetAncestor(h, 2) if h else None       # GA_ROOT
            if h and h not in self.app.own_hwnds():
                self.picking = None
                self.refresh()
                self.app.wrap_window(h)
                return
        self._was_down = down
        if time.time() > self.picking:
            self.picking = None
            print("Wrap app: timed out")
        self.refresh()
        if self.picking:
            self.win.after(50, self.poll_pick)

    # -- placement along the frame perimeter
    def slide_to(self, px, py):
        """Snap the tab to the frame edge nearest the cursor (grip follows cursor)."""
        fx, fy, fw, fh = self.app.frame_rect()
        cx = min(max(px, fx), fx + fw)
        cy = min(max(py, fy), fy + fh)
        d = {"top": abs(py - fy), "bottom": abs(py - (fy + fh)),
             "left": abs(px - fx), "right": abs(px - (fx + fw))}
        edge = min(d, key=d.get)
        gx, gw = self.seg_x("grip")
        if edge in ("top", "bottom"):
            span = max(1, fw - self.W)
            off = (cx - fx - (gx + gw / 2)) / span      # grip centre under cursor
        else:
            span = max(1, fh - self.H)
            off = (cy - fy - self.H / 2) / span
        self.app.cfg["tab_edge"] = edge
        self.app.cfg["tab_offset"] = round(min(1.0, max(0.0, off)), 4)
        self.follow()

    def place(self, edge, off, fx, fy, fw, fh, outside):
        g = 2
        b = self.overlap                  # outside: overlap border (+1 px safety)
        if edge in ("top", "bottom"):
            x = fx + off * max(0, fw - self.W)
            if edge == "top":       # inside: hang flush from the border (notch)
                y = fy - self.H + b if outside else fy
            else:
                y = fy + fh - b if outside else fy + fh - self.H
        else:   # left/right: stays horizontal, hangs beside the edge
            y = fy + off * max(0, fh - self.H)
            if edge == "left":
                x = fx - self.W + b if outside else fx
            else:
                x = fx + fw - b if outside else fx + fw - self.W
        return int(x), int(y)

    @property
    def overlap(self):
        return max(2, int(self.app.cfg["border_width"])) + 1

    def follow(self):
        a = self.app
        if getattr(a, "off", False):
            return
        if a.cfg["mode"] == "window" and not a.root.winfo_viewable():
            return
        fx, fy, fw, fh = a.frame_rect()
        self.bw = int(a.cfg["border_width"])
        edge = a.cfg.get("tab_edge", "top")
        if edge not in ("top", "bottom", "left", "right"):
            edge = "top"
        off = min(1.0, max(0.0, float(a.cfg.get("tab_offset", 0.0))))
        mon = a.monitors[a.monitor_at(fx + fw // 2, fy + fh // 2)]
        ml, mt = mon["left"], mon["top"]
        mr, mb = ml + mon["width"], mt + mon["height"]
        outside = a.cfg["mode"] != "monitor"
        OPP = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}
        # `attached` names the drawing variant: outside on edge E joins with the
        # tab side facing E's opposite, i.e. the same as "inside on OPP[E]".
        x, y = self.place(edge, off, fx, fy, fw, fh, outside)
        attached = edge if outside else OPP[edge]
        offmon = lambda x, y: x < ml or y < mt or x + self.W > mr or y + self.H > mb
        if outside and offmon(x, y):
            x, y = self.place(edge, off, fx, fy, fw, fh, False)   # flip inside, still joined
            attached = OPP[edge]
        if offmon(x, y):
            attached = None                                       # nothing to attach to
        x = min(max(x, ml), mr - self.W)
        y = min(max(y, mt), mb - self.H)
        if attached != getattr(self, "attached", None):
            self.attached = attached
            self.refresh()
        if (self.win.winfo_x(), self.win.winfo_y()) != (x, y):
            self.win.geometry(f"+{x}+{y}")
        self.win.lift()


# ---------------------------------------------------------------- shared menu
# One definition drives both the tray (pystray) and the tab gear (tk.Menu).
# Node: {"label": str|fn, "do": fn, "checked": fn, "radio": bool, "enabled": fn,
#        "visible": fn, "sub": fn -> [nodes]} or None for a separator.
def menu_spec(a):
    c = a.cfg
    N = lambda label, do=None, checked=None, radio=False, enabled=None, visible=None, sub=None: \
        {"label": label, "do": do, "checked": checked, "radio": radio,
         "enabled": enabled, "visible": visible, "sub": sub}
    on_change = lambda: c.get("capture_mode") == "on_change"

    def mode_label():
        m = c["mode"]
        return "Mode (" + ({"monitor": f"Monitor {c['monitor_index']}", "window": "Wrap window",
                            "region": "Region"}[m]) + ")"

    def mode_sub():
        items = [N(f"Monitor {i}", lambda i=i: a.set_monitor(i),
                   checked=lambda i=i: c["mode"] == "monitor" and int(c["monitor_index"]) == i,
                   radio=True) for i in range(1, len(a.monitors))]
        items.append(N("Wrap window (click a window)", a.start_wrap,
                       checked=lambda: c["mode"] == "window", radio=True))
        items.append(N("Region", lambda: (a.off and a.set_off(False), a.to_region_here()),
                       checked=lambda: c["mode"] == "region", radio=True,
                       visible=lambda: bool(c.get("show_region_mode")) or c["mode"] == "region"))
        return items

    def cap_label():
        return "Capture (" + ("on change" if on_change() else
                              f"{float(c['interval_seconds']):g}s") + ")"

    def cap_sub():
        return ([N("On change (follow-along)", lambda: a.set_capture(None),
                   checked=on_change, radio=True)] +
                [N(f"Every {v}s", lambda v=v: a.set_capture(v), radio=True,
                   checked=lambda v=v: not on_change() and float(c["interval_seconds"]) == v)
                 for v in INTERVAL_PRESETS] +
                ([N(lambda: f"{a.custom_interval():g}s (custom)",
                    lambda: a.set_capture(a.custom_interval()), radio=True,
                    checked=lambda: not on_change() and
                    float(c["interval_seconds"]) == a.custom_interval())]
                 if a.custom_interval() else []) +
                [N("Custom...", a.ask_custom_interval),
                 None,
                 N("Text reading (OCR)", a.toggle_ocr,
                   checked=lambda: bool(c.get("ocr_enabled", True))),
                 N("Privacy pause (password managers, banks)", a.toggle_privacy,
                   checked=lambda: bool(c.get("privacy_pause_enabled", True)))])

    def hist_sub():
        return ([N(f"{v} min", lambda v=v: a.set_retention(v), radio=True,
                   checked=lambda v=v: not a.keep_all() and
                   float(c.get("retention_minutes", 0) or 0) == v) for v in RETENTION_PRESETS] +
                [N("Keep all (no limit)", lambda: a.set_retention(None), radio=True,
                   checked=a.keep_all),
                 None,
                 N(lambda: a.cache_label(), enabled=lambda: False),
                 N("Clear history...", a.clear_history),
                 N(lambda: "Session format (" + ("JPEG" if c.get("session_format") == "zip_jpeg"
                                                 else "PNG") + ")", sub=lambda: [
                     N("ZIP - PNG (lossless)", lambda: a.set_session_format("zip"), radio=True,
                       checked=lambda: c.get("session_format") != "zip_jpeg"),
                     N("ZIP - JPEG q80 (smaller)", lambda: a.set_session_format("zip_jpeg"),
                       radio=True, checked=lambda: c.get("session_format") == "zip_jpeg")])])

    def hist_n():
        try:
            return int(c.get("suggestions_history", 5) or 1)
        except (TypeError, ValueError):
            return 5

    def sugg_sub():
        cx = int(c.get("custom_suggestions_history") or 0)
        return ([N(lambda: "Show panel" + a.hk_tag("panel"), a.toggle_suggestions,
                   checked=lambda: bool(a.panel and a.panel.visible),
                   enabled=lambda: a.panel is not None),
                 None,
                 N("Latest only", lambda: a.set_sugg_history(1), radio=True,
                   checked=lambda: hist_n() == 1),
                 N("Last 3", lambda: a.set_sugg_history(3), radio=True,
                   checked=lambda: hist_n() == 3),
                 N("Last 5", lambda: a.set_sugg_history(5), radio=True,
                   checked=lambda: hist_n() == 5)] +
                ([N(f"Last {cx}", lambda: a.set_sugg_history(cx), radio=True,
                    checked=lambda: hist_n() == cx)] if cx and cx not in HISTORY_PRESETS else []) +
                [N("Last X...", a.ask_sugg_history),
                 N(lambda: "Hide after session (" + a.hide_label() + ")", sub=lambda: (
                     [N(lbl, lambda v=v: a.set_panel_hide(v), radio=True,
                        checked=lambda v=v: a.panel_hide() == v)
                      for v, lbl in ((0, "Instant"), (30, "30s"), (60, "60s"))] +
                     ([N(f"{a.custom_hide():g}s (custom)",
                         lambda: a.set_panel_hide(a.custom_hide()), radio=True,
                         checked=lambda: a.panel_hide() == a.custom_hide())]
                      if a.custom_hide() and a.custom_hide() not in (30, 60) else []) +
                     [N("Custom...", a.ask_panel_hide),
                      N("Never", lambda: a.set_panel_hide(-1), radio=True,
                        checked=lambda: a.panel_hide() < 0)])),
                 N(lambda: "Clear markup after session (" + a.mclear_label() + ")",
                   sub=lambda: (
                     [N(lbl, lambda v=v: a.set_markup_clear(v), radio=True,
                        checked=lambda v=v: a.markup_clear_s() == v)
                      for v, lbl in ((0, "Instant"), (30, "30s"), (60, "60s"))] +
                     ([N(f"{a.custom_mclear():g}s (custom)",
                         lambda: a.set_markup_clear(a.custom_mclear()), radio=True,
                         checked=lambda: a.markup_clear_s() == a.custom_mclear())]
                      if a.custom_mclear() and a.custom_mclear() not in (30, 60) else []) +
                     [N("Custom...", a.ask_markup_clear),
                      N("Never", lambda: a.set_markup_clear(-1), radio=True,
                        checked=lambda: a.markup_clear_s() < 0)])),
                 N("Clear panel on new session", a.toggle_clear_on_session,
                   checked=lambda: bool(c.get("suggestions_clear_on_session", False))),
                 N("Newest at bottom", a.toggle_newest_bottom,
                   checked=lambda: bool(c.get("suggestions_newest_bottom", False))),
                 None,
                 N("Reset panel position", a.reset_panel,
                   enabled=lambda: a.panel is not None),
                 N("Lock panel (click-through)", a.toggle_panel_lock,
                   checked=lambda: bool(c.get("suggestions_clickthrough")))])

    def follow_label():
        if a.follow_until:
            return f"Stop following ({fmt_session(a.follow_remaining())} left)" + a.hk_tag("follow")
        return f"Follow me ({a.follow_limit():g} min)" + a.hk_tag("follow")

    def follow_sub():
        cf = a.custom_follow()
        return ([N(lambda: "Stop following" if a.follow_until else
                   f"Start ({a.follow_limit():g} min)", a.toggle_follow),
                 None] +
                [N(f"{v} min", lambda v=v: a.set_follow_limit(v), radio=True,
                   checked=lambda v=v: a.follow_limit() == v) for v in FOLLOW_PRESETS] +
                ([N(f"{cf:g} min (custom)", lambda: a.set_follow_limit(cf), radio=True,
                    checked=lambda: a.follow_limit() == cf)]
                 if cf and cf not in FOLLOW_PRESETS else []) +
                [N("Custom...", a.ask_follow_limit),
                 None,
                 N("Auto-save Follow me sessions", a.toggle_follow_autosave,
                   checked=lambda: bool(c.get("follow_autosave", False))),
                 N("Copy prompt on start", a.toggle_copy_prompt,
                   checked=lambda: bool(c.get("follow_copy_prompt", True))),
                 N(lambda: "Webhook URL... (set)" if c.get("follow_webhook_url")
                   else "Webhook URL...", a.ask_webhook_url),
                 N(lambda: "Webhook key... (set)" if c.get("follow_webhook_key")
                   else "Webhook key...", a.ask_webhook_key)])

    def view_sub():
        return [N("Tab shows", sub=lambda: [
                    N(lbl, lambda k=k: a.toggle_tab_item(k),
                      checked=lambda k=k: bool(c.get(k, DEFAULTS[k]))) for k, lbl in TAB_ITEMS]),
                N(lambda: f"Suggestions (last {hist_n()})" if hist_n() > 1 else
                  "Suggestions (latest only)", sub=sugg_sub),
                N("Show suggestion panel", a.toggle_placing,
                  checked=lambda: bool(a.panel and a.panel.placing),
                  enabled=lambda: a.panel is not None),
                N("Reset panel position", a.reset_panel, enabled=lambda: a.panel is not None),
                N("Show Region mode", a.toggle_region_mode,
                  checked=lambda: bool(c.get("show_region_mode"))),
                N("Show in screenshots", a.toggle_show_in_screenshots,
                  checked=lambda: bool(c.get("show_in_screenshots", False))),
                None,
                N(lambda: "Theme (" + dict(THEME_CHOICES).get(a.theme_setting(), "") + ")",
                  sub=lambda: [N(lbl, lambda k=k: a.set_theme(k), radio=True,
                                 checked=lambda k=k: a.theme_setting() == k)
                               for k, lbl in THEME_CHOICES]),
                N("Markup colors", sub=lambda: [
                    N("Suggestion boxes", sub=lambda: color_sub("auto_box_color", False)),
                    N("Other markup", sub=lambda: color_sub("markup_color", True))]),
                N("Indicator style", sub=indicator_sub),
                None,
                N("Keyboard shortcuts\u2026", a.show_shortcuts)]

    def color_sub(key, allow_default):
        cur = lambda: str(c.get(key) or "").upper()
        presets = [hx.upper() for _, hx in COLOR_PRESETS]
        return (([N("As drawn", lambda: a.set_markup_color(key, ""), radio=True,
                    checked=lambda: not cur())] if allow_default else []) +
                [N(name, lambda hx=hx: a.set_markup_color(key, hx), radio=True,
                   checked=lambda hx=hx: cur() == hx.upper()) for name, hx in COLOR_PRESETS] +
                ([N(lambda: f"Custom ({cur()})", lambda: None, radio=True,
                    checked=lambda: True, enabled=lambda: False)]
                 if cur() and cur() not in presets else []) +
                [N("Custom\u2026", lambda: a.ask_markup_color(key))])

    def indicator_sub():
        bw = lambda: int(c.get("border_width", 3) or 3)
        return [N("Edges", sub=lambda: [
                    N(lbl, lambda k=k: a.set_indicator("indicator_edges", k), radio=True,
                      checked=lambda k=k: (c.get("indicator_edges") or "all") == k)
                    for k, lbl in EDGE_CHOICES]),
                N("Thickness", sub=lambda: [
                    N(f"{lbl} ({px} px)", lambda px=px: a.set_border_width(px), radio=True,
                      checked=lambda px=px: bw() == px) for lbl, px in THICKNESS]),
                N("Colorblind mode", lambda: a.set_indicator(
                    "colorblind_mode", not c.get("colorblind_mode")),
                  checked=lambda: bool(c.get("colorblind_mode")))]

    return [
        N(lambda: ("Pause" if a.state() == "live" else "Go live") + a.hk_tag("pause"),
          a.toggle_pause),
        N(lambda: ("Turn on" if a.off else "Turn off") + a.hk_tag("onoff"), a.toggle_off),
        N(lambda: "Show markup" + a.hk_tag("markup"), a.toggle_markup, checked=lambda: bool(a.overlay and a.overlay.visible),
          enabled=lambda: a.overlay is not None),
        N(lambda: "Save session" + a.hk_tag("save"), a.save_session),
        N("Save last Follow me", a.save_last_follow,
          enabled=lambda: a.last_follow_range() is not None),
        N("Open session\u2026", a.open_session),
        N("Secure mode", a.toggle_secure, checked=lambda: a.secure()),
        N(follow_label, sub=follow_sub),
        None,
        N(mode_label, sub=mode_sub),
        N(cap_label, sub=cap_sub),
        N(lambda: f"History ({a.retention_label(long=True)})", sub=hist_sub),
        N("View", sub=view_sub),
        N("Open folder", sub=lambda: [N("Snapshots", a.open_folder),
                                      N("Sessions", a.open_sessions)]),
        None,
        dict(N(lambda: f"\u2665  Support {a.display_name()}\u2026", a.open_support),
             fg="#FF5C8A"),
        N("About", a.show_about),
        None,
        N("Quit", a.quit),
    ]


def _val(v):
    return v() if callable(v) else v


# pystray checks callback arity (default args count), so wrap via closures
def _cb1(f):
    return lambda item: f()


def _act(f, a):
    return lambda icon, item: a.post(f)


def _sub(f, a):
    return lambda: pystray_menu(f(), a, top=False)


def pystray_menu(nodes, a, top=True):
    I, M = pystray.MenuItem, pystray.Menu
    out = []
    for i, n in enumerate(nodes):
        if n is None:
            out.append(M.SEPARATOR)
            continue
        kw = {}
        lbl = n["label"]
        text = _cb1(lbl) if callable(lbl) else lbl
        if n.get("checked"):
            kw["checked"] = _cb1(n["checked"])
            kw["radio"] = n.get("radio", False)
        if n.get("enabled"):
            kw["enabled"] = _cb1(n["enabled"])
        if n.get("visible"):
            kw["visible"] = _cb1(n["visible"])
        if top and i == 0:
            kw["default"] = True
        if n.get("sub"):
            # callable Menu -> rebuilt each time it opens (labels/visibility stay live)
            sub = M(_sub(n["sub"], a))
            out.append(I(text, sub, **kw))
        else:
            act = _act(n["do"], a) if n.get("do") else None
            out.append(I(text, act, **kw))
    return M(*out) if top else out


def tk_menu(parent, nodes, guard=None):
    """Build a tk.Menu from the shared spec (rebuilt fresh for every popup).
    guard(): False -> ignore activation (debounce right after the popup opens)."""
    m = tk.Menu(parent, tearoff=0)
    wrap = (lambda f: (lambda: guard() and f())) if guard else (lambda f: f)
    m._vars = []
    for n in nodes:
        if n is None:
            m.add_separator()
            continue
        if n.get("visible") and not n["visible"]():
            continue
        lbl = str(_val(n["label"]))
        state = "normal" if not n.get("enabled") or n["enabled"]() else "disabled"
        if n.get("sub"):
            m.add_cascade(label=lbl, menu=tk_menu(m, n["sub"](), guard), state=state)
        elif n.get("checked"):
            v = tk.BooleanVar(m, value=bool(n["checked"]()))
            m._vars.append(v)
            m.add_checkbutton(label=lbl, variable=v, command=wrap(n["do"]), state=state,
                              indicatoron=True)
        else:
            kw = {"foreground": n["fg"], "activeforeground": n["fg"]} if n.get("fg") else {}
            m.add_command(label=lbl, command=wrap(n["do"]) if n["do"] else None, state=state,
                          **kw)
    return m


# ---------------------------------------------------------------- themed popup menu
class ThemedMenu:
    """Popup menu drawn with tkinter so it follows the app theme (native Win32 menus
    ignore the app's dark mode in practice). Same node spec as tk_menu: submenus, check and
    radio marks, disabled items, right-aligned shortcuts, keyboard navigation (arrows, Enter,
    Esc, Left/Right) and closes on a click elsewhere or focus loss."""
    PADX, ROW_H, FONT = 10, 26, ("Segoe UI", 9)

    def __init__(self, app, nodes, guard=None, parent=None):
        self.app, self.guard, self.parent = app, guard, parent
        self.root_menu = parent.root_menu if parent else self
        self.child = None
        self.rows = []                          # (frame, node, labels...)
        self.sel = -1
        self._open_job = None
        pal = app.pal
        self.pal = pal
        w = self.win = tk.Toplevel(app.root)
        w.withdraw()
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=pal["menu_border"])
        body = self.body = tk.Frame(w, bg=pal["menu_bg"], padx=0, pady=4)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        body.columnconfigure(1, weight=1)
        r = 0
        for n in nodes:
            if n is None:
                if self.rows and self.rows[-1] is not None:
                    sep = tk.Frame(body, bg=pal["menu_bg"], height=9)
                    sep.grid(row=r, column=0, columnspan=4, sticky="we")
                    tk.Frame(sep, bg=pal["menu_sep"], height=1).place(
                        relx=0, rely=0.5, relwidth=1, x=8, width=-16)
                    self.rows.append(None)
                    r += 1
                continue
            if n.get("visible") and not n["visible"]():
                continue
            self._add_row(r, n)
            r += 1
        while self.rows and self.rows[-1] is None:
            self.rows.pop()
        for k in ("<Up>", "<Down>", "<Left>", "<Right>", "<Return>", "<KP_Enter>",
                  "<Escape>", "<space>"):
            w.bind(k, self._key)
        w.bind("<FocusOut>", lambda e: self.root_menu._check_focus())
        w.bind("<FocusIn>", lambda e: setattr(self.root_menu, "_had_focus", True))
        self._armed = False                     # mouse released once since opening

    # -- building
    def _add_row(self, r, n):
        pal = self.pal
        enabled = not n.get("enabled") or bool(n["enabled"]())
        text = str(_val(n["label"]))
        short = ""
        if text.endswith(")") and "  (" in text:          # "Save session  (Ctrl+Alt+S)"
            text, short = text.rsplit("  (", 1)
            short = short[:-1]
        mark = ""
        if n.get("checked") and n["checked"]():
            mark = "\u25CF" if n.get("radio") else "\u2713"
        fg = n.get("fg") or pal["fg"]
        if not enabled:
            fg = pal["menu_dis"]
        cells = [tk.Label(self.body, text=mark, bg=pal["menu_bg"], fg=fg, width=2,
                          font=self.FONT, anchor="e"),
                 tk.Label(self.body, text=text, bg=pal["menu_bg"], fg=fg, font=self.FONT,
                          anchor="w", padx=4),
                 tk.Label(self.body, text=short, bg=pal["menu_bg"],
                          fg=pal["menu_dis"] if enabled else pal["menu_dis"],
                          font=self.FONT, anchor="e", padx=6),
                 tk.Label(self.body, text="\u203A" if n.get("sub") else "", bg=pal["menu_bg"],
                          fg=fg, font=("Segoe UI", 11), width=2, anchor="w")]
        for c, cell in enumerate(cells):
            cell.grid(row=r, column=c, sticky="nsew", ipady=3)
        idx = len(self.rows)
        self.rows.append({"node": n, "cells": cells, "enabled": enabled})
        for cell in cells:
            cell.bind("<Enter>", lambda e, i=idx: self._hover(i))
            cell.bind("<ButtonRelease-1>", lambda e, i=idx: self._click(i))

    # -- show / position
    def popup(self, x, y, avoid=None):
        """Show with the top-left near (x, y), kept inside the work area. avoid = (l, r)
        of the parent item so a submenu flips to the left when there's no room."""
        w = self.win
        w.update_idletasks()
        mw, mh = w.winfo_reqwidth(), w.winfo_reqheight()
        wl, wt, wr, wb = self.app.work_area(int(x), int(y))
        if avoid and x + mw > wr:
            x = avoid[0] - mw + 2
        x = max(wl, min(int(x), wr - mw))
        y = max(wt, min(int(y), wb - mh))
        w.geometry(f"+{x}+{y}")
        w.deiconify()
        w.lift()
        try:
            w.update_idletasks()
            h = ctypes.windll.user32.GetParent(w.winfo_id()) or w.winfo_id()
            self.hwnd = h
            set_capture_exclusion(h, self.app.cfg)
        except Exception:
            self.hwnd = None
        if self.parent is None:
            self._grab_focus()
            self._watch()
        return self

    def _grab_focus(self):
        try:
            if self.hwnd:
                ctypes.windll.user32.SetForegroundWindow(self.hwnd)
            self.win.focus_force()
        except Exception:
            pass

    def _menus(self):
        out, m = [], self.root_menu
        while m is not None:
            out.append(m)
            m = m.child
        return out

    def _inside(self, px, py):
        for m in self._menus():
            try:
                x, y = m.win.winfo_rootx(), m.win.winfo_rooty()
                if x <= px < x + m.win.winfo_width() and y <= py < y + m.win.winfo_height():
                    return True
            except tk.TclError:
                pass
        return False

    def _watch(self):
        """Close on a mouse press outside every open menu (works even without focus)."""
        if not self.alive():
            return
        try:
            u = ctypes.windll.user32
            down = any(u.GetAsyncKeyState(vk) & 0x8000 for vk in (0x01, 0x02, 0x04))
            if not down:
                self._armed = True
            elif self._armed:
                pt = wintypes.POINT()
                u.GetCursorPos(ctypes.byref(pt))
                if not self._inside(pt.x, pt.y):
                    return self.close_all()
        except Exception:
            pass
        self.win.after(40, self._watch)

    def _check_focus(self):
        def later():
            if not self.alive():
                return
            try:
                f = self.app.root.focus_get()
            except (tk.TclError, KeyError):
                f = None
            mine = f is not None and any(str(f).startswith(str(m.win)) for m in self._menus())
            if not mine and getattr(self, "_had_focus", False):
                self.close_all()
        self.win.after(60, later)

    def alive(self):
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    # -- interaction
    def _paint(self, i, on):
        row = self.rows[i] if 0 <= i < len(self.rows) else None
        if not row:
            return
        bg = self.pal["menu_hl"] if on and row["enabled"] else self.pal["menu_bg"]
        for c in row["cells"]:
            c.configure(bg=bg)

    def _select(self, i):
        if i == self.sel:
            return
        self._paint(self.sel, False)
        self.sel = i
        self._paint(i, True)

    def _hover(self, i):
        self._select(i)
        if self._open_job:
            self.win.after_cancel(self._open_job)
            self._open_job = None
        row = self.rows[i]
        if self.child and (not row or row["node"] is not getattr(self.child, "_node", None)):
            self.child.close()
            self.child = None
        if row and row["enabled"] and row["node"].get("sub") and not self.child:
            self._open_job = self.win.after(180, lambda: self._open_sub(i))

    def _open_sub(self, i, focus=False):
        self._open_job = None
        row = self.rows[i]
        if self.child:
            if getattr(self.child, "_node", None) is row["node"]:
                return
            self.child.close()
        cells = row["cells"]
        top = cells[0].winfo_rooty() - 5
        right = self.win.winfo_rootx() + self.win.winfo_width()
        m = ThemedMenu(self.app, row["node"]["sub"](), self.guard, parent=self)
        m._node = row["node"]
        self.child = m
        m.popup(right - 2, top, avoid=(self.win.winfo_rootx(), right))
        if focus:
            m._select(m._first())
            m.win.focus_force()

    def _first(self, start=0, step=1):
        n = len(self.rows)
        for k in range(n):
            i = (start + k * step) % n
            if self.rows[i] and self.rows[i]["enabled"]:
                return i
        return -1

    def _click(self, i):
        row = self.rows[i]
        if not row or not row["enabled"]:
            return
        n = row["node"]
        if n.get("sub"):
            return self._open_sub(i)
        if self.guard and not self.guard():
            return
        do = n.get("do")
        self.close_all()
        if do:
            self.app.root.after(1, do)

    def _key(self, e):
        self.root_menu._had_focus = True
        k = e.keysym
        if k in ("Down", "Up"):
            step = 1 if k == "Down" else -1
            start = (self.sel + step) if self.sel >= 0 else (0 if step > 0 else len(self.rows) - 1)
            self._select(self._first(start % max(1, len(self.rows)), step))
        elif k in ("Return", "KP_Enter", "space"):
            if self.sel >= 0:
                row = self.rows[self.sel]
                if row and row["node"].get("sub"):
                    self._open_sub(self.sel, focus=True)
                else:
                    self._click(self.sel)
        elif k == "Right":
            row = self.rows[self.sel] if self.sel >= 0 else None
            if row and row["enabled"] and row["node"].get("sub"):
                self._open_sub(self.sel, focus=True)
        elif k in ("Left", "Escape"):
            if self.parent is not None:
                p = self.parent
                self.close()
                p.child = None
                p.win.focus_force()
            elif k == "Escape":
                self.close_all()
        return "break"

    def close(self):
        if self.child:
            self.child.close()
            self.child = None
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def close_all(self):
        self.app._menu_closed_at = time.monotonic()
        self.root_menu.close()
        if getattr(self.app, "_menu", None) is self.root_menu:
            self.app._menu = None


# ---------------------------------------------------------------- tray
class Tray:
    """pystray icon on its own thread; actions are queued back to the Tk thread."""
    ICONS = {"live": "ShisaPoint_live.ico", "paused": "ShisaPoint_stopped.ico",
             "off": "ShisaPoint_error.ico"}

    def __init__(self, app):
        self.app = app
        self.icon = None
        self.sig = None
        self.images = {}
        for st, fn in self.ICONS.items():
            try:
                self.images[st] = Image.open(resource("icon", fn))
            except OSError as e:
                print("Tray icon missing:", fn, e)
                self.images[st] = Image.new("RGBA", (32, 32), (0, 160, 80, 255))
        self.menu = pystray_menu(menu_spec(app), app)
        self.icon = pystray.Icon("ShisaPoint", self.images["live"], app.display_name(), self.menu)
        self._hook_right_click()
        threading.Thread(target=self._run, daemon=True).start()

    def _hook_right_click(self):
        """Right-click shows the app's themed menu instead of pystray's Win32 menu
        (left-click keeps the default action). Off with config themed_menus = false."""
        hs = getattr(self.icon, "_message_handlers", None)
        if not isinstance(hs, dict):
            return
        try:
            from pystray._util import win32 as pw
            key = pw.WM_NOTIFY
        except Exception:
            return
        orig = hs.get(key)
        app = self.app

        def on_notify(wparam, lparam):
            if lparam == 0x0205 and app.cfg.get("themed_menus", True):     # WM_RBUTTONUP
                pt = wintypes.POINT()
                ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
                app.post(lambda: app.popup_tray_menu(pt.x, pt.y))
                return 0
            return orig(wparam, lparam) if orig else 0
        hs[key] = on_notify

    def _run(self):
        try:
            self.icon.run()
        except Exception as e:
            print("Tray failed:", e)

    def update(self):
        a = self.app
        st = a.state()
        mode = a.cfg["mode"]
        desc = {"region": "Region", "monitor": f"Monitor {a.cfg['monitor_index']}",
                "window": "Window"}[mode]
        label = {"live": "Live", "paused": "Paused", "off": "Off"}[st]
        if st == "live" and a.privacy:
            label = "Privacy pause"
        iv = "on change" if a.cfg.get("capture_mode") == "on_change" else \
            f"{float(a.cfg['interval_seconds']):g}s"
        tip = f"{a.display_name()} - {label} ({iv}, {desc})"
        tip = f"{tip}\nSession {fmt_session(a.session_elapsed())} | History {a.retention_label()} | {a.cache_label()}"
        sig = (st, tip, bool(a.overlay and a.overlay.visible), a.privacy)
        if sig == self.sig or not self.icon:
            return
        self.sig = sig
        try:
            self.icon.icon = self.images[st]
            self.icon.title = tip[:127]
            self.icon.update_menu()
        except Exception as e:
            print("Tray update failed:", e)

    def stop(self):
        try:
            self.icon.stop()
        except Exception:
            pass


# ---------------------------------------------------------------- session viewer
class SessionViewer:
    """Browse a saved session: frames with prev/next (arrow keys), the boxes and marks that
    were on screen at that moment, and that moment's suggestions. Reads .zip or .zip.dpapi
    in memory (nothing decrypted to disk)."""
    MAXW, MAXH, SIDE = 1100, 700, 300

    def __init__(self, app, path):
        import io
        import zipfile
        self.app, self.path = app, path
        data = read_maybe_secure(path)
        self.z = zipfile.ZipFile(io.BytesIO(data))
        names = set(self.z.namelist())
        try:
            man = json.loads(self.z.read("manifest.json").decode("utf-8"))
        except KeyError:
            man = {"frames": [{"file": n} for n in sorted(names)
                              if n.startswith("frames/") and not n.endswith("/")]}
        self.frames = [f for f in man.get("frames", []) if f.get("file") in names]
        if not self.frames:
            raise ValueError("no frames in this session")
        self.has_marks = bool(man.get("has_marks"))
        self.kind = man.get("kind") or "full"
        n = len(self.frames)
        self.franges = [r for r in man.get("follow_ranges") or []
                        if isinstance(r.get("first_frame"), int) and r["first_frame"] < n]
        self.i, self.photo = 0, None
        if self.kind != "follow" and self.franges:     # full save: open at the first Follow me
            self.i = self.franges[0]["first_frame"]
        pal = self.pal = app.pal
        t = self.win = tk.Toplevel(app.root)
        t.title(f"{os.path.basename(path)} - {app.display_name()}")
        try:
            t.iconbitmap(resource("icon", "ShisaPoint.ico"))
        except tk.TclError:
            pass
        t.configure(bg=pal["bg"])
        bar = tk.Frame(t, bg=pal["bg"])
        bar.pack(fill="x", padx=10, pady=(8, 4))
        bk = dict(bg=pal["button"], fg=pal["fg"], activebackground=pal["field_on"],
                  activeforeground=pal["fg"], relief="flat", padx=10, font=("Segoe UI", 10))
        tk.Button(bar, text="\u25C0", command=lambda: self.go(-1), **bk).pack(side="left")
        tk.Button(bar, text="\u25B6", command=lambda: self.go(1), **bk).pack(side="left", padx=(4, 10))
        self.fmark = tk.Label(bar, text="", bg=pal["bg"], fg=pal["accent"],
                              font=("Segoe UI", 10, "bold"))
        self.fmark.pack(side="left", padx=(0, 8))
        self.info = tk.Label(bar, bg=pal["bg"], fg=pal["fg"], font=("Segoe UI", 10), anchor="w")
        self.info.pack(side="left", fill="x", expand=True)
        keys = "\u2190 \u2192 Home End" + ("  F next Follow me" if self.franges else "") + \
            "  Esc closes"
        tk.Label(bar, text=keys, bg=pal["bg"], fg=pal["dim"],
                 font=("Segoe UI", 8)).pack(side="right")
        body = tk.Frame(t, bg=pal["bg"])
        body.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.cv = tk.Canvas(body, bg=pal["field"], highlightthickness=0, width=640, height=400)
        self.cv.pack(side="left", anchor="n")
        side = tk.Frame(body, bg=pal["bg"], width=self.SIDE)
        side.pack(side="left", fill="y", padx=(10, 0))
        side.pack_propagate(False)
        tk.Label(side, text="Suggestions at this moment", bg=pal["bg"], fg=pal["dim"],
                 font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x")
        self.side = tk.Frame(side, bg=pal["bg"])
        self.side.pack(fill="both", expand=True)
        for k, d in (("<Left>", -1), ("<Right>", 1), ("<Prior>", -10), ("<Next>", 10)):
            t.bind(k, lambda e, d=d: self.go(d))
        t.bind("<Home>", lambda e: self.go(-len(self.frames)))
        t.bind("<End>", lambda e: self.go(len(self.frames)))
        t.bind("<Escape>", lambda e: self.close())
        t.bind("<KeyPress-f>", lambda e: self.next_follow())
        t.bind("<KeyPress-F>", lambda e: self.next_follow())
        t.protocol("WM_DELETE_WINDOW", self.close)
        dark_title_bar(t, app.theme_name() == "dark")
        self.show()
        t.lift()
        t.focus_force()

    def close(self):
        try:
            self.z.close()
        except Exception:
            pass
        self.win.destroy()

    def next_follow(self):
        """Jump to the start of the next Follow me session (wraps around)."""
        if not self.franges:
            return
        starts = [r["first_frame"] for r in self.franges]
        nxt = next((s for s in starts if s > self.i), starts[0])
        self.go(nxt - self.i)

    def range_at(self, i):
        for k, r in enumerate(self.franges):
            if r["first_frame"] <= i <= r.get("last_frame", r["first_frame"]):
                return k, r
        return None, None

    def go(self, d):
        i = max(0, min(len(self.frames) - 1, self.i + d))
        if i != self.i:
            self.i = i
            self.show()

    def show(self):
        import io
        import base64
        pal, cfg = self.pal, self.app.cfg
        f = self.frames[self.i]
        with Image.open(io.BytesIO(self.z.read(f["file"]))) as im:
            im = im.convert("RGB")
            k = min(1.0, self.MAXW / float(im.width), self.MAXH / float(im.height))
            if k < 1.0:
                im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))),
                               Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "PNG")
            w, h = im.width, im.height
        self.photo = tk.PhotoImage(master=self.win, data=base64.b64encode(buf.getvalue()))
        cv = self.cv
        cv.delete("all")
        cv.configure(width=w, height=h)
        cv.create_image(0, 0, anchor="nw", image=self.photo)
        ov = f.get("overlay") or {}
        box_col = str(cfg.get("auto_box_color") or "#FFD400")
        mark_fix = str(cfg.get("markup_color") or "")
        for m in ov.get("marks", []) if ov.get("visible", True) else []:
            try:
                self.draw_mark(m, k, mark_fix or str(m.get("color") or "#FF0000"))
            except (KeyError, TypeError, ValueError):
                pass
        if ov.get("visible", True):
            for sg in ov.get("suggestions", []):
                if sg.get("box"):
                    x, y, bw, bh = [float(v) * k for v in sg["box"]]
                    cv.create_rectangle(x, y, x + bw, y + bh, outline=box_col, width=2)
                    cv.create_text(x, y - 3, anchor="sw", text=str(sg["n"]), fill=box_col,
                                   font=("Segoe UI", 11, "bold"))
                elif sg.get("circle"):
                    x, y, r = [float(v) * k for v in sg["circle"]]
                    col = mark_fix or "#38BDF8"
                    cv.create_oval(x - r, y - r, x + r, y + r, outline=col, width=3)
                    cv.create_text(x - r, y - r - 3, anchor="sw", text=str(sg["n"]), fill=col,
                                   font=("Segoe UI", 11, "bold"))
        ts = str(f.get("timestamp") or "")
        when = ts[11:19] if len(ts) >= 19 else ts
        extra = "" if self.has_marks else "   (saved before boxes were recorded)"
        hidden = "   (markup hidden)" if ov and not ov.get("visible", True) else ""
        k, r = self.range_at(self.i)
        if r is None and f.get("follow"):
            r = {}
        if r is not None:
            tag = "\u25CF Follow me"
            if k is not None and len(self.franges) > 1:
                tag += f" {k + 1}/{len(self.franges)}"
            if r and self.i == r.get("last_frame") and r.get("stop_reason"):
                tag += f" (ended: {r['stop_reason']})"
            elif r and self.i == r.get("first_frame"):
                tag += " (start)"
            self.fmark.configure(text=tag)
        else:
            self.fmark.configure(text="")
        self.info.configure(text=f"Frame {self.i + 1} / {len(self.frames)}   {when}{hidden}{extra}")
        for c in self.side.winfo_children():
            c.destroy()
        sugg = ov.get("suggestions") or []
        if not sugg:
            tk.Label(self.side, text="None", bg=pal["bg"], fg=pal["dim"], anchor="w",
                     font=("Segoe UI", 9, "italic")).pack(fill="x", pady=4)
        for sg in sugg:
            dim = sg.get("old") or sg.get("stale") or sg.get("gone")
            row = tk.Frame(self.side, bg=pal["bg"])
            row.pack(fill="x", pady=(4, 0))
            boxed = sg.get("box") or sg.get("circle")
            tk.Label(row, text=str(sg.get("n", "")) if boxed else "\u2022", width=2, anchor="n",
                     bg=pal["bg"], fg=pal["dim"] if dim or not boxed else pal["accent"],
                     font=("Segoe UI", 9, "bold")).pack(side="left", anchor="n")
            col = tk.Frame(row, bg=pal["bg"])
            col.pack(side="left", fill="x", expand=True)
            tk.Label(col, text=str(sg.get("text", "")), bg=pal["bg"],
                     fg=pal["dim"] if dim else pal["fg"], justify="left", anchor="w",
                     wraplength=self.SIDE - 40, font=("Segoe UI", 9)).pack(fill="x")
            notes = []
            if sg.get("target"):
                notes.append("\u2192 " + str(sg["target"]))
            if sg.get("gone"):
                notes.append("(fixed / gone)")
            elif sg.get("old"):
                notes.append("(earlier batch)")
            if notes:
                tk.Label(col, text="  ".join(notes), bg=pal["bg"], fg=pal["dim"], justify="left",
                         anchor="w", wraplength=self.SIDE - 40,
                         font=("Segoe UI", 8)).pack(fill="x")

    def draw_mark(self, m, k, col):
        cv, t = self.cv, m.get("type", "rect")
        w = max(1.0, float(m.get("width", 3)))
        S = lambda v: float(v) * k
        if t in ("rect", "ellipse", "circle"):
            if t == "circle" and "r" in m:
                cx, cy, r = S(m["x"]), S(m["y"]), S(m["r"])
                x0, y0, x1, y1 = cx - r, cy - r, cx + r, cy + r
            else:
                x0, y0 = S(m["x"]), S(m["y"])
                x1, y1 = x0 + S(m["w"]), y0 + S(m["h"])
            (cv.create_rectangle if t == "rect" else cv.create_oval)(x0, y0, x1, y1,
                                                                     outline=col, width=w)
            if m.get("label"):
                cv.create_text(x0, y0 - 3, anchor="sw", text=str(m["label"]), fill=col,
                               font=("Segoe UI", 11, "bold"))
        elif t in ("line", "arrow"):
            kw = {"arrow": tk.LAST} if t == "arrow" else {}
            cv.create_line(S(m["x1"]), S(m["y1"]), S(m["x2"]), S(m["y2"]), fill=col,
                           width=w, **kw)
        elif t in ("polyline", "freehand"):
            pts = [S(v) for pt in m["points"] for v in pt]
            if len(pts) >= 4:
                cv.create_line(*pts, fill=col, width=w, smooth=bool(m.get("smooth", True)))
        elif t == "text":
            cv.create_text(S(m["x"]), S(m["y"]), anchor="nw", text=str(m.get("text", "")),
                           fill=col, font=("Segoe UI", max(7, int(int(m.get("font_size", 16)) * k)),
                                           "bold"))


# ---------------------------------------------------------------- graceful quit
QUIT_EVENT = "Local\\ShisaPoint_Quit"   # deploy: OpenExisting(QUIT_EVENT).Set() -> clean exit


def start_quit_listener(on_quit):
    """Wait on a per-session named auto-reset event; when set, call on_quit() once.
    Lets a deploy close the app cleanly so the PyInstaller bootloader removes its _MEI temp."""
    k32 = ctypes.windll.kernel32
    k32.CreateEventW.restype = wintypes.HANDLE
    k32.CreateEventW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
    k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.ResetEvent.argtypes = (wintypes.HANDLE,)
    h = k32.CreateEventW(None, False, False, QUIT_EVENT)   # auto-reset, initially clear
    if not h:
        print("Quit event unavailable:", k32.GetLastError())
        return None
    k32.ResetEvent(h)                     # ignore a signal left from a previous instance

    def wait():
        try:
            if k32.WaitForSingleObject(h, 0xFFFFFFFF) == 0:
                print("Quit requested (deploy/external)")
                on_quit()
        except Exception as e:
            print("Quit listener failed:", e)
    threading.Thread(target=wait, daemon=True, name="quit-listener").start()
    return h



CARET_MAX_FRAC = 0.015
CARET_MAX_WIDTH_PX = 4
FUZZY_TARGET_MIN = 0.6


def approx_same_len(a, b):
    """True if two strings are roughly the same length (within 2 chars or 30%)."""
    la, lb = len(str(a)), len(str(b))
    if not la or not lb:
        return False
    return abs(la - lb) <= max(2, int(round(0.3 * max(la, lb))))


def is_caret_diff(mask, max_frac=CARET_MAX_FRAC, max_width_px=CARET_MAX_WIDTH_PX):
    """True if the changed pixels look like a blinking caret: tiny area and very narrow."""
    try:
        bbox = mask.getbbox()
    except Exception:
        return False
    if not bbox:
        return False
    area = mask.width * mask.height
    if area <= 0:
        return False
    hist = mask.histogram()
    changed = hist[255] if len(hist) > 255 else 0
    if changed / float(area) >= max_frac:
        return False
    return (bbox[2] - bbox[0]) <= max_width_px


# ---------------------------------------------------------------- main app
class ShisaPoint:
    def __init__(self):
        self.cfg = load_config()
        SECURE["on"] = bool(self.cfg.get("secure_mode", False))
        self.started = time.time()  # files written before this are from a previous run
        self.edit = self.paused = False
        self.off = False
        self._sess_acc, self._sess_last = 0.0, time.monotonic()
        self.session_frames = 0
        self.drag = None
        self.target = None          # hwnd in window mode
        self.meta = None
        self.privacy = None         # matched privacy title while paused for privacy
        self.follow_until = None    # epoch seconds while a Follow me session runs
        self._marks_sig = None      # last state written to marks.jsonl
        self._pending = None        # tentative snapshot awaiting its second look
        self._change_start = None   # start of continuous (unsettled) change
        self._meta_lock = threading.Lock()
        os.makedirs(self.cfg["output_dir"], exist_ok=True)
        with MSS() as s:
            self.monitors = s.monitors   # [0] = virtual desktop
        self.virtual = dict(self.monitors[0])

        self.pal = THEMES["dark"]            # replaced by apply_theme() right after start
        self._patch_dialogs()
        self.root = tk.Tk()
        self.root.title(self.display_name())
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=KEY)
        self.root.attributes("-transparentcolor", KEY)
        c = self.cfg
        self.root.geometry(f"{c['width']}x{c['height']}+{c['x']}+{c['y']}")
        self.cv = tk.Canvas(self.root, bg=KEY, highlightthickness=0, bd=0, relief="flat")
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Configure>", lambda e: self.redraw())
        self.cv.bind("<ButtonPress-1>", self.on_press)
        self.cv.bind("<B1-Motion>", self.on_drag)
        self.cv.bind("<ButtonRelease-1>", lambda e: setattr(self, "drag", None))
        self.cv.bind("<Motion>", self.on_motion)
        self.hwnd = hwnd_of(self.root)
        self.set_clickthrough(True)
        # Frame never appears in captures when supported; region mode still
        # insets by border_width as a fallback. show_in_screenshots leaves the
        # frame visible to external tools and sets frame_excluded False so
        # monitor-mode grabs inset the in-monitor border (and own snapshots
        # hide chrome during the grab).
        dwm_flat(self.hwnd)
        self.frame_excluded = set_capture_exclusion(self.hwnd, self.cfg)

        self.overlay = MarkupOverlay(self) if c["markup_enabled"] else None
        self.sanitize_region()              # fixes leaked monitor-sized region geometry
        self.apply_mode()
        self.off = False
        self.q = queue.Queue()
        self.hotkeys = HotkeyThread(lambda h: self.post(lambda: self.on_hotkey(h)),
                                    self.cfg["hotkeys"])
        self.hotkeys.start()
        self._quit_evt = start_quit_listener(lambda: self.post(self.quit))
        self.root.protocol("WM_DELETE_WINDOW", lambda: self.post(self.quit))   # WM_CLOSE = clean quit
        self.tab = ControlTab(self)
        self.ocr = None
        self._ocr_lock = threading.Lock()
        if c.get("ocr_enabled", True):
            self.ocr = Ocr()
        self.panel = None
        try:
            self.panel = SuggestionPanel(self)
        except Exception as e:
            print("Suggestion panel unavailable:", e)
        self._ref = self._prev = None
        self._stable_since = self._last_snap = 0.0
        self._start_change_worker()
        self.tray = None
        if pystray:
            try:
                self.tray = Tray(self)
            except Exception as e:
                print("Tray unavailable:", e)
        else:
            print("pystray not installed; running without tray icon")
        try:
            self.root.iconbitmap(default=resource("icon", "ShisaPoint.ico"))
        except tk.TclError:
            pass
        self.root.after(100, self.pump)
        self.root.after(150, lambda: (self.apply_theme(force=True), self.theme_loop()))
        self.root.after(500, self.capture_loop)
        self.root.after(700, self.change_loop)
        if getattr(self, "tab", None):
            self.tab.follow()
        self.root.after(200, self.track_loop)
        self.root.after(1000, self.follow_tick)

    # -------------------------------------------- frame window
    def set_clickthrough(self, on):
        base = WS_EX_LAYERED | WS_EX_TOOLWINDOW
        if on:
            set_exstyle(self.hwnd, add=base | WS_EX_TRANSPARENT)
        else:
            set_exstyle(self.hwnd, add=base, remove=WS_EX_TRANSPARENT)

    def place_frame(self, left, top, right, bottom, outside=True):
        bw = int(self.cfg["border_width"]) if outside else 0
        w, h = right - left + 2 * bw, bottom - top + 2 * bw
        self.set_geo(w, h, left - bw, top - bw)

    def set_geo(self, w, h, x, y):
        self._size = (int(w), int(h))
        geo = f"{int(w)}x{int(h)}+{int(x)}+{int(y)}"
        if self.root.geometry() != geo:
            self.root.geometry(geo)
            self.root.after_idle(self.redraw)

    def refresh_frame(self):
        """(Re)show the frame and re-apply everything Windows/Tk may drop on
        withdraw/deiconify: wrapper HWND, colour key, topmost, click-through,
        capture exclusion. Then redraw with the intended size."""
        if getattr(self, "off", False) or (self.cfg["mode"] == "window" and not self.target_ok()):
            return
        r = self.root
        r.deiconify()
        r.update_idletasks()
        self.hwnd = hwnd_of(r)                      # Tk may recreate the wrapper
        r.attributes("-transparentcolor", KEY)
        r.attributes("-topmost", True)
        self.set_clickthrough(not self.edit)        # WS_EX_LAYERED | (TRANSPARENT)
        dwm_flat(self.hwnd)
        self.frame_excluded = set_capture_exclusion(self.hwnd, self.cfg)
        self.raise_topmost()
        self.redraw()

    def raise_topmost(self):
        # HWND_TOPMOST, SWP_NOMOVE|NOSIZE|NOACTIVATE|SHOWWINDOW
        ctypes.windll.user32.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0,
                                          0x0002 | 0x0001 | 0x0010 | 0x0040)

    def color(self):
        cb = bool(self.cfg.get("colorblind_mode"))
        if self.edit:
            return CB_EDIT if cb else self.cfg["edit_color"]
        if self.paused:
            return CB_PAUSED if cb else self.cfg["paused_color"]
        return CB_LIVE if cb else self.cfg["border_color"]

    def live_colors(self):
        """(normal, pulse) colour of the live dot on the tab / panel."""
        return (CB_LIVE, CB_LIVE_PULSE) if self.cfg.get("colorblind_mode") else \
            ("#22C55E", "#7CFF9A")

    def redraw(self):
        cv = self.cv
        cv.delete("all")
        w, h = getattr(self, "_size", (0, 0))
        if w <= 1 or h <= 1:
            w, h = cv.winfo_width(), cv.winfo_height()
        bw = max(1, int(self.cfg["border_width"]))   # same width in every mode
        if getattr(self, "tab", None) and not getattr(self, "off", False):
            self.root.after_idle(self.tab.refresh)
        col = self.color()
        edges = str(self.cfg.get("indicator_edges") or "all")
        if self.edit:
            edges = "all"                      # edit mode always shows the whole frame
        sides = {"top": ("t",), "bottom": ("b",), "top_bottom": ("t", "b"),
                 "sides": ("l", "r")}.get(edges, ("t", "b", "l", "r"))
        rects = {"t": (0, 0, w, bw), "b": (0, h - bw, w, h), "l": (0, 0, bw, h),
                 "r": (w - bw, 0, w, h)}
        dashed = bool(self.cfg.get("colorblind_mode")) and self.paused and not self.edit
        for k in sides:
            x0, y0, x1, y1 = rects[k]
            if not dashed:
                cv.create_rectangle(x0, y0, x1, y1, fill=col, outline="")
                continue
            seg, gap = 18, 10                  # dashed = stopped (colorblind mode)
            if k in ("t", "b"):
                for x in range(int(x0), int(x1), seg + gap):
                    cv.create_rectangle(x, y0, min(x + seg, x1), y1, fill=col, outline="")
            else:
                for y in range(int(y0), int(y1), seg + gap):
                    cv.create_rectangle(x0, y, x1, min(y + seg, y1), fill=col, outline="")
        if self.edit:
            cv.create_rectangle(bw, bw, w - bw, h - bw, fill="#020304",
                                stipple="gray12", outline="")
            cv.create_rectangle(w - GRIP, h - GRIP, w, h, fill=col, outline="")
            cv.create_text(w // 2, h // 2, fill=col,
                           text="EDIT MODE - drag to move, edges to resize" +
                           (f"\n{self.hk('edit')} to lock" if self.hk("edit") else ""))

    # -------------------------------------------- modes
    def apply_mode(self):
        m = self.cfg["mode"]
        if self.edit and m != "region":
            self.edit = False
            self.set_clickthrough(True)
        if m == "monitor":
            idx = int(self.cfg["monitor_index"])
            if not 1 <= idx < len(self.monitors):
                idx = self.cfg["monitor_index"] = 1
            mon = self.monitors[idx]
            self.place_frame(mon["left"], mon["top"], mon["left"] + mon["width"],
                             mon["top"] + mon["height"], outside=False)
        elif m == "window":
            if not self.target and self.cfg["window_title"]:
                self.target = find_window(self.cfg["window_title"], self.own_hwnds())
            self.track_window()
        else:
            c = self.cfg
            self.set_geo(c["width"], c["height"], c["x"], c["y"])
            if not getattr(self, "off", False):
                self.root.deiconify()
        self.refresh_frame()   # deiconify + re-apply styles + redraw
        if getattr(self, "tab", None):
            self.tab.refresh()
            self.tab.follow()
        print("Mode:", m, f"(monitor {self.cfg['monitor_index']})" if m == "monitor" else "")

    def own_hwnds(self):
        hs = {self.hwnd} | ({self.overlay.hwnd} if self.overlay else set())
        tab = getattr(self, "tab", None)
        return hs | (tab.hwnds() if tab else set())

    def target_ok(self):
        u = ctypes.windll.user32
        if self.target and not u.IsWindow(self.target):
            self.target = None
        if not self.target and self.cfg["window_title"]:
            self.target = find_window(self.cfg["window_title"], self.own_hwnds())
        return self.target is not None

    def track_window(self):
        u = ctypes.windll.user32
        if getattr(self, "off", False):
            return
        if not self.target_ok() or u.IsIconic(self.target):
            if self.root.winfo_viewable():
                self.root.withdraw()
            return
        self.place_frame(*window_rect(self.target), outside=True)
        if not self.root.winfo_viewable():
            self.refresh_frame()

    def track_loop(self):
        if self.cfg["mode"] == "window":
            try:
                self.track_window()
            except Exception as e:
                print("Track failed:", e)
        try:
            if getattr(self, "tab", None):
                self.tab.follow()
            self._ticks = getattr(self, "_ticks", 0) + 1
            if not self.off:
                self.check_privacy()
            txt = fmt_session(self.session_elapsed())
            if getattr(self, "tab", None) and txt != getattr(self, "_sess_txt", None):
                self._sess_txt = txt
                if self.cfg.get("tab_show_elapsed", True) and not self.off:
                    self.tab.refresh()
            if self._ticks % 10 == 0 and not self.off and self.root.winfo_viewable():
                self.raise_topmost()            # stay above taskbar/other topmost windows
        except Exception as e:
            if str(e) != getattr(self, "_track_err", None):   # log each distinct error once
                self._track_err = str(e)
                print("Track loop error:", e)
        try:
            self.root.after(200, self.track_loop)
        except tk.TclError:
            pass

    def snap_foreground(self):
        h = ctypes.windll.user32.GetForegroundWindow()
        if h and h not in self.own_hwnds():
            self.target = h
            self.cfg["mode"] = "window"
            title = window_title(h)
            if title:
                self.cfg["window_title"] = title
            print("Tracking window:", "(title hidden, secure mode)" if SECURE["on"] else title)
            save_config(self.cfg)
            self.apply_mode()

    # -------------------------------------------- tab actions
    def frame_rect(self):
        r = self.root
        return r.winfo_x(), r.winfo_y(), r.winfo_width(), r.winfo_height()

    # -------------------------------------------- state (live / paused / off)
    def post(self, fn):
        """Thread-safe: run fn on the Tk thread."""
        self.q.put(fn)

    def pump(self):
        try:
            while True:
                fn = self.q.get_nowait()
                try:
                    fn()
                except Exception as e:
                    print("Action failed:", e)
        except queue.Empty:
            pass
        if self.tray:
            self.tray.update()
        try:
            self.root.after(100, self.pump)
        except tk.TclError:
            pass

    # -------------------------------------------- session timer
    def session_tick(self):
        now = time.monotonic()
        if self.state() == "live":
            self._sess_acc += now - self._sess_last
        self._sess_last = now

    def session_elapsed(self):
        self.session_tick()
        return self._sess_acc

    def session_reset(self):
        self._sess_acc, self._sess_last = 0.0, time.monotonic()
        self.session_frames = 0
        if getattr(self, "tab", None):
            self.tab.refresh()

    def toggle_tab_item(self, key):
        self.cfg[key] = not self.cfg.get(key, DEFAULTS[key])
        self.cfg.pop("tab_display", None)          # superseded setting
        save_config(self.cfg)
        self.tab.relayout()

    def state(self):
        return "off" if self.off else "paused" if self.paused else "live"

    def toggle_pause(self):
        self.session_tick()
        if self.off:                      # tray "Go live" from OFF turns on
            self.paused = False
            return self.set_off(False)
        self.paused = not self.paused
        self.refresh_frame()                # colour back to green + re-assert styles
        self.tab.refresh()
        if self.panel:
            self.panel.refresh_header()

    def toggle_off(self):
        self.set_off(not self.off)

    def set_off(self, off):
        self.session_tick()
        self.off = off
        if off:
            self.edit = False
            self.set_clickthrough(True)
            self.tab.picking = None
            self.tab.hide_tip()
            self.root.withdraw()
            self.tab.win.withdraw()
            if self.overlay:
                self.overlay.win.withdraw()
            print("Turned OFF (tray only)")
        else:
            self.tab.win.deiconify()
            if self.overlay:
                self.overlay.win.deiconify()
                self.overlay.reapply()
            self.tab.reapply()
            self.apply_mode()
            print("Turned ON")
        self.redraw()
        self.tab.refresh()
        if getattr(self, "panel", None):
            self.panel.refresh_header()

    def work_area(self, x, y):
        """Work area (excludes taskbar) of the monitor containing (x, y)."""
        try:
            class MI(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
            u = ctypes.windll.user32
            u.MonitorFromPoint.restype = wintypes.HMONITOR
            u.MonitorFromPoint.argtypes = (wintypes.POINT, wintypes.DWORD)
            h = u.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)   # NEAREST
            mi = MI()
            mi.cbSize = ctypes.sizeof(MI)
            if u.GetMonitorInfoW(h, ctypes.byref(mi)):
                r = mi.rcWork
                return r.left, r.top, r.right, r.bottom
        except Exception:
            pass
        m = self.monitors[self.monitor_at(x, y)]
        return m["left"], m["top"], m["left"] + m["width"], m["top"] + m["height"]

    def popup_menu(self, anchor):
        """anchor = gear button screen rect (l, t, r, b). The menu opens below the
        tab (above if no room), left edge just right of the gear's left edge so the
        pointer never rests on an item; item clicks are ignored for 250 ms."""
        opened = time.monotonic()
        if self.cfg.get("themed_menus", True):
            return self._themed_popup(anchor, opened)
        menu = tk_menu(self.root, menu_spec(self),
                       guard=lambda: time.monotonic() - opened >= 0.25)
        menu.update_idletasks()
        mw, mh = menu.winfo_reqwidth(), menu.winfo_reqheight()
        l, t, r, b = anchor
        wl, wt, wr, wb = self.work_area((l + r) // 2, (t + b) // 2)
        gap = 2
        y = b + gap                                   # below the tab
        if self.cfg.get("tab_edge") == "bottom" or y + mh > wb:
            y = t - gap - mh                          # above the tab
        if y < wt:                                    # neither fits: beside the gear
            y = max(wt, min(t, wb - mh))
            x = r + gap if r + gap + mw <= wr else l - gap - mw
        else:
            x = l
        x = max(wl, min(x, wr - mw))
        y = max(wt, min(y, wb - mh))
        try:
            menu.tk_popup(int(x), int(y))
        finally:
            menu.grab_release()

    def _close_menu(self):
        m = getattr(self, "_menu", None)
        if m is not None and m.alive():
            m.close_all()
            return True
        return False

    def _themed_popup(self, anchor, opened):
        if self._close_menu():                       # second click on the gear closes it
            return
        if time.monotonic() - getattr(self, "_menu_closed_at", 0) < 0.4:
            return                                   # that click just closed it (press)
        m = self._menu = ThemedMenu(self, menu_spec(self),
                                    guard=lambda: time.monotonic() - opened >= 0.25)
        m.win.update_idletasks()
        mw, mh = m.win.winfo_reqwidth(), m.win.winfo_reqheight()
        l, t, r, b = anchor
        wl, wt, wr, wb = self.work_area((l + r) // 2, (t + b) // 2)
        gap = 2
        y = b + gap
        if self.cfg.get("tab_edge") == "bottom" or y + mh > wb:
            y = t - gap - mh
        if y < wt:
            y = max(wt, min(t, wb - mh))
            x = r + gap if r + gap + mw <= wr else l - gap - mw
        else:
            x = l
        m.popup(x, y)

    def popup_tray_menu(self, x, y):
        """Tray right-click: themed menu above/left of the cursor like a Windows tray menu."""
        self._close_menu()
        opened = time.monotonic()
        m = self._menu = ThemedMenu(self, menu_spec(self),
                                    guard=lambda: time.monotonic() - opened >= 0.15)
        m.win.update_idletasks()
        mw, mh = m.win.winfo_reqwidth(), m.win.winfo_reqheight()
        wl, wt, wr, wb = self.work_area(x, y)
        px = x - mw if x + mw > wr else x
        py = y - mh if y + mh > wb else y
        m.popup(px, py)

    # -------------------------------------------- follow me / suggestions settings
    def on_change_active(self):
        return self.cfg.get("capture_mode") == "on_change" or bool(self.follow_until)

    def follow_limit(self):
        try:
            v = float(self.cfg.get("follow_limit_min", 15) or 15)
        except (TypeError, ValueError):
            v = 15
        return v if 1 <= v <= 1440 else 15

    def custom_follow(self):
        try:
            v = float(self.cfg.get("custom_follow_limit_min") or 0)
        except (TypeError, ValueError):
            return 0
        return v if 1 <= v <= 1440 else 0

    def set_follow_limit(self, v):
        self.cfg["follow_limit_min"] = v
        save_config(self.cfg)

    def ask_follow_limit(self):
        from tkinter import simpledialog
        v = simpledialog.askinteger(self.display_name(), "Follow me for how many minutes? (1-1440)",
                                    initialvalue=int(self.custom_follow() or 45),
                                    minvalue=1, maxvalue=1440, parent=self.root)
        if v is None:
            return
        self.cfg["custom_follow_limit_min"] = v
        self.set_follow_limit(v)

    def toggle_placing(self):
        if self.panel:
            self.panel.toggle_placing()

    def toggle_copy_prompt(self):
        self.cfg["follow_copy_prompt"] = not self.cfg.get("follow_copy_prompt", True)
        save_config(self.cfg)

    def ask_webhook_url(self):
        from tkinter import simpledialog
        v = simpledialog.askstring(self.display_name(), "Follow me webhook URL (blank = off):",
                                   initialvalue=self.cfg.get("follow_webhook_url", ""),
                                   parent=self.root)
        if v is None:
            return
        v = v.strip()
        if v and not v.lower().startswith(("http://", "https://")):
            print("Webhook URL ignored (must start with http:// or https://)")
            return
        self.cfg["follow_webhook_url"] = v
        save_config(self.cfg)

    def ask_webhook_key(self):
        from tkinter import simpledialog
        v = simpledialog.askstring(self.display_name(), "Webhook key (sent as 'Authorization: "
                                   "Bearer <key>'; blank = none):", show="*",
                                   initialvalue=self.cfg.get("follow_webhook_key", ""),
                                   parent=self.root)
        if v is None:
            return
        self.cfg["follow_webhook_key"] = v.strip()
        save_config(self.cfg)

    def webhook(self, event, **extra):
        """POST a Follow me event in the background (5 s timeout, never blocks the UI)."""
        url = str(self.cfg.get("follow_webhook_url") or "").strip()
        key = str(self.cfg.get("follow_webhook_key") or "").strip()
        if not url:
            return
        out = self.cfg["output_dir"]
        body = {"event": event, "limit_min": self.follow_limit(),
                "expires": self.follow_fields()["follow_expires"], "output_dir": out,
                "meta_path": os.path.join(out, "meta.json"),
                "time": datetime.now().astimezone().isoformat(timespec="seconds"),
                "secure": self.secure()}
        body.update(extra)
        if self.secure():                      # never send screen text in secure mode
            body.pop("ocr_excerpt", None)

        def send():
            import urllib.request
            t0 = time.monotonic()
            print(f"Webhook {event}: sending")
            try:
                import ssl
                hdr = {"Content-Type": "application/json", "User-Agent": "ShisaPoint"}
                if key:
                    hdr["Authorization"] = "Bearer " + key
                req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                             headers=hdr, method="POST")
                ctx = ssl.create_default_context()          # Windows store
                try:
                    import certifi                          # bundled CA fallback
                    ctx.load_verify_locations(certifi.where())
                except Exception:
                    pass
                with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
                    print(f"Webhook {event}: HTTP {r.status} "
                          f"({int((time.monotonic() - t0) * 1000)} ms)")
            except Exception as e:
                print(f"Webhook {event} failed after {int((time.monotonic() - t0) * 1000)} ms:"
                      f" {type(e).__name__}: {e}")
        threading.Thread(target=send, daemon=True).start()

    def copy_prompt(self):
        tpl = str(self.cfg.get("follow_prompt_template") or DEFAULTS["follow_prompt_template"])
        try:
            text = tpl.format(limit=f"{self.follow_limit():g}", output_dir=self.cfg["output_dir"],
                              meta_path=os.path.join(self.cfg["output_dir"], "meta.json"))
        except (KeyError, IndexError, ValueError) as e:
            print("follow_prompt_template invalid:", e)
            text = tpl
        if self.secure():
            text += (" Secure mode is on: snapshots and OCR text are DPAPI-encrypted; read them "
                     "with read_snapshot.ps1 (follow.ps1 decrypts automatically).")
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()
            self.tab.toast("Prompt copied")
        except tk.TclError as e:
            print("Clipboard failed:", e)

    def follow_remaining(self):
        return max(0, (self.follow_until or 0) - time.time())

    def toggle_follow(self):
        self.stop_follow() if self.follow_until else self.start_follow()

    def start_follow(self):
        self.follow_until = time.time() + self.follow_limit() * 60
        self._log_follow_event("follow_start", limit_min=self.follow_limit())
        self.webhook("follow_start")           # first: the routine's cold start is the long pole
        if self.off:
            self.set_off(False)
        if self.paused:
            self.toggle_pause()
        self._ref = self._prev = None
        self._pending = self._change_start = None
        print(f"Follow me: started for {self.follow_limit():g} min")
        self.tab.relayout()
        self.cancel_markup_clear()
        if self.panel:
            try:
                self.panel.new_session()
            except Exception as e:
                print("Panel session start failed:", e)
        self._trigger = "session_start"        # baseline snapshot right away (confirmed)
        try:
            if self.privacy or self.capture_once() is False:
                self.write_follow_state()
            elif getattr(self, "tab", None):
                self.tab.pulse()
        except Exception as e:
            print("Baseline snapshot failed:", e)
            self.write_follow_state()
        finally:
            self._trigger = "interval"
        self._last_snap = time.monotonic()
        if self.cfg.get("follow_copy_prompt", True):
            self.copy_prompt()

    def stop_follow(self, reason="stopped"):
        if not self.follow_until:
            return
        if self.cfg.get("follow_webhook_on_stop", True):
            self.webhook("follow_stop", reason=reason)
        self.follow_until = None
        self._pending = None
        self._log_follow_event("follow_stop", reason=reason)
        print("Follow me:", reason)
        self.tab.relayout()
        self.schedule_markup_clear()
        if self.panel:
            try:
                self.panel.end_session(reason)
            except Exception as e:
                print("Panel session end failed:", e)
        self.write_follow_state(reason)
        if self.cfg.get("follow_autosave", False):
            self.root.after(1500, lambda: self.save_last_follow(auto=True))  # after last frame

    def _log_follow_event(self, ev, **kw):
        """follow_start / follow_stop line in history.jsonl (encrypted in secure mode)."""
        rec = {"event": ev, "t": datetime.now().astimezone().isoformat(timespec="milliseconds"),
               "ts": round(time.time(), 3)}
        rec.update(kw)
        try:
            with open(os.path.join(self.cfg["output_dir"], HISTORY_LOG), "a",
                      encoding="utf-8") as f:
                f.write(hist_encode(rec))
        except OSError as e:
            print("Follow event log failed:", e)
        self._follow_cache = None

    def last_follow_range(self):
        """Newest Follow me session that still has snapshots in the history, or None.
        Cached on the history file's size/mtime so the menu can ask cheaply."""
        folder = self.cfg["output_dir"]
        try:
            st = os.stat(os.path.join(folder, HISTORY_LOG))
            key = (st.st_size, st.st_mtime, bool(self.follow_until))
        except OSError:
            return None
        c = getattr(self, "_follow_cache", None)
        if c and c[0] == key:
            return c[1]
        found = None
        try:
            stamps = [name_ts(n) or m for n, m, _ in history_files(folder)]
            for r in reversed(follow_ranges(folder, bool(self.follow_until))):
                lo, hi = r["start"] - 0.5, _range_end(r)
                if r.get("live") or any(lo <= t <= hi for t in stamps):
                    found = r
                    break
        except Exception as e:
            print("Follow me range lookup failed:", e)
        self._follow_cache = (key, found)
        return found

    def toggle_follow_autosave(self):
        self.cfg["follow_autosave"] = not self.cfg.get("follow_autosave", False)
        save_config(self.cfg)
        print("Auto-save Follow me sessions:", "on" if self.cfg["follow_autosave"] else "off")

    def save_last_follow(self, auto=False):
        """Save only the newest Follow me session (baseline .. stop, or .. now if live)
        as sessions/follow_*.zip(.dpapi)."""
        if getattr(self, "_saving", False):
            if auto:
                self.root.after(3000, lambda: self.save_last_follow(auto=True))
                return
            return print("Session save already running")
        rng = self.last_follow_range()
        if not rng:
            return print("Save last Follow me: no Follow me session in the history")
        self._saving = True
        folder, fmt = self.cfg["output_dir"], self.cfg.get("session_format", "zip")
        live, t0 = bool(self.follow_until), time.time()
        what = "auto-saved" if auto else "saved"

        def work():
            try:
                path, size, n = build_session(folder, fmt, secure=self.secure(), follow=rng,
                                              live_follow=live)
                msg = f"Follow me session {what}: {path} ({n} frames, {size / 1048576:.1f} MB, " \
                      f"{time.time() - t0:.1f}s)"

                def done():
                    print(msg)
                    if getattr(self, "tab", None):
                        self.tab.flash()
                        try:
                            self.tab.toast(f"Follow me session {what}", 2500)
                        except Exception:
                            pass
                self.post(done)
            except Exception as e:
                err = e
                self.post(lambda: print(f"Follow me session save failed ({what}):", err))
            finally:
                self._saving = False
        threading.Thread(target=work, daemon=True).start()

    def follow_fields(self):
        on = bool(self.follow_until)
        return {"follow_active": on,
                "follow_expires": datetime.fromtimestamp(self.follow_until).astimezone()
                .isoformat(timespec="seconds") if on else None,
                "follow_remaining_s": int(self.follow_remaining()) if on else 0}

    def write_meta(self, meta):
        """meta.json stays plain (follow state, trigger, scale...). In secure mode the OCR text
        and window title move to meta.secure.dpapi (full meta, DPAPI-encrypted)."""
        out = self.cfg["output_dir"]
        full = {k: v for k, v in list(meta.items()) if not k.startswith("_")}
        with self._meta_lock:
            if self.secure():
                full["secure"] = True
                write_secure(os.path.join(out, "meta.secure.dpapi"),
                             json.dumps(full).encode("utf-8"))
                plain = dict(full)
                plain["ocr"] = {"encrypted": True} if full.get("ocr") else None
                plain["window_title"] = None
                plain["secure_meta"] = "meta.secure.dpapi"
                plain["note"] = ("secure mode: images, OCR text and window title are "
                                 "DPAPI-encrypted; read with read_snapshot.ps1")
                write_json_atomic(os.path.join(out, "meta.json"), plain)
            else:
                full["secure"] = False
                write_json_atomic(os.path.join(out, "meta.json"), full)

    def write_follow_state(self, reason=None):
        meta = self.meta if self.meta is not None else {"note": "no snapshot yet"}
        meta.update(self.follow_fields())
        if reason:
            meta["follow_end_reason"] = reason
        else:
            meta.pop("follow_end_reason", None)
        try:
            self.write_meta(meta)
        except OSError as e:
            print("meta write failed:", e)

    def follow_tick(self):
        try:
            if self.follow_until:
                if time.time() >= self.follow_until:
                    self.stop_follow("time limit reached")
                else:
                    self.tab.refresh()
        except Exception as e:
            print("Follow tick failed:", e)
        self.root.after(1000, self.follow_tick)

    def set_sugg_history(self, n):
        self.cfg["suggestions_history"] = max(1, int(n))
        save_config(self.cfg)
        if self.panel:
            self.panel.render()

    def ask_sugg_history(self):
        from tkinter import simpledialog
        v = simpledialog.askinteger(self.display_name(), "Show the last how many suggestions? (1-20)",
                                    initialvalue=int(self.cfg.get("custom_suggestions_history")
                                                     or 5), minvalue=1, maxvalue=20,
                                    parent=self.root)
        if v is None:
            return
        self.cfg["custom_suggestions_history"] = v
        self.set_sugg_history(v)

    def display_name(self):
        return str(self.cfg.get("app_display_name") or APP_NAME)

    def support_url(self):
        u = str(self.cfg.get("support_url") or DEFAULTS["support_url"]).strip()
        return u if u.lower().startswith(("http://", "https://")) else DEFAULTS["support_url"]

    def open_support(self):
        import webbrowser
        try:
            webbrowser.open(self.support_url())
        except Exception as e:
            print("Could not open support link:", e)

    # -------------------------------------------- secure mode
    def secure(self):
        return bool(self.cfg.get("secure_mode", False))

    def toggle_secure(self):
        from tkinter import messagebox
        if not self.secure():
            folder = self.cfg["output_dir"]
            n = len(history_files(folder))
            if not messagebox.askyesno(
                    self.display_name(),
                    "Turn on Secure mode?\n\n"
                    "\u2022 Snapshots, history, OCR text and saved sessions are encrypted with "
                    "your Windows account (DPAPI).\n"
                    "\u2022 History is wiped when you quit (saved sessions are kept, "
                    "encrypted).\n"
                    "\u2022 Webhooks and the clipboard prompt never include screen text.\n\n"
                    f"The current unsaved history ({n} snapshots) is deleted now so nothing "
                    "stays unencrypted."):
                return
            try:                                  # DPAPI works? (fail before switching)
                dpapi_unprotect(dpapi_protect(b"ok"))
            except Exception as e:
                messagebox.showerror(self.display_name(),
                                     f"Secure mode unavailable: {e}")
                return
            wiped = wipe_history(folder)
            self._marks_sig = None
            self.cfg["secure_mode"] = True
            SECURE["on"] = True
            print(f"Secure mode: on (cleared {wiped} plain file(s))")
        else:
            self.cfg["secure_mode"] = False
            SECURE["on"] = False
            print("Secure mode: off (encrypted files stay encrypted until pruned/wiped)")
        save_config(self.cfg)
        self.write_follow_state()                 # keep follow state (wipe reset meta.json)
        if self.secure() and self.state() == "live" and not self.privacy:
            self.root.after(300, self.capture_once)   # fresh, encrypted latest frame
        self.run_prune()
        if getattr(self, "tab", None):
            self.tab.relayout()
        if self.panel:
            self.panel.refresh_header()
        if self.tray:
            self.tray.sig = None
            self.tray.update()

    # -------------------------------------------- theme / colours / indicator
    def _patch_dialogs(self):
        """simpledialog boxes (Custom..., webhook URL, ...) follow the theme too."""
        from tkinter import simpledialog
        if getattr(simpledialog.Dialog, "_pp_themed", False):
            return
        orig = simpledialog.Dialog.buttonbox
        app = self

        def buttonbox(dlg):
            orig(dlg)
            theme_tree(dlg, app.pal)
            dark_title_bar(dlg, getattr(app, "_theme", "dark") == "dark")
        simpledialog.Dialog.buttonbox = buttonbox
        simpledialog.Dialog._pp_themed = True

    def theme_setting(self):
        v = str(self.cfg.get("theme") or "system").lower()
        return v if v in ("system", "light", "dark") else "system"

    def theme_name(self):
        v = self.theme_setting()
        if v == "system":
            return "light" if windows_light_theme() else "dark"
        return v

    def apply_theme(self, force=False):
        name = self.theme_name()
        if name == getattr(self, "_theme", None) and not force:
            return
        first = getattr(self, "_theme", None) is None
        self._theme = name
        self.pal = THEMES[name]
        hw = [getattr(self, "hwnd", None)]
        if self.tray and self.tray.icon is not None:
            hw.append(getattr(self.tray.icon, "_hwnd", None))
        ok = set_dark_menus(name == "dark", hw)
        if first:
            print(f"Theme: {name} (native menu dark mode {'set' if ok else 'unavailable'}; "
                  f"{'themed' if self.cfg.get('themed_menus', True) else 'native'} menus)")
        self._close_menu()
        if self.panel:
            try:
                self.panel.apply_theme(self.pal, self.live_colors()[0])
            except Exception as e:
                print("Panel theme failed:", e)
        for attr, reopen in (("_keys", self.show_shortcuts), ("_about", self.show_about)):
            w = getattr(self, attr, None)
            if w is not None and w.winfo_exists():
                try:
                    w.event_generate("<<ThemeClose>>")
                except tk.TclError:
                    pass
                w.destroy()
                reopen()
        if self.tray:
            self.tray.sig = None
            self.tray.update()
        if not first:
            print("Theme:", name)

    def theme_loop(self):
        try:
            if self.theme_setting() == "system":
                self.apply_theme()
        except Exception as e:
            print("Theme check failed:", e)
        self.root.after(3000, self.theme_loop)

    def set_theme(self, v):
        self.cfg["theme"] = v
        save_config(self.cfg)
        self.apply_theme(force=True)

    def set_markup_color(self, key, hx):
        self.cfg[key] = hx
        save_config(self.cfg)
        if self.overlay:
            self.overlay.redraw()

    def ask_markup_color(self, key):
        from tkinter import colorchooser
        init = self.cfg.get(key) or ("#FFD400" if key == "auto_box_color" else "#FF0000")
        try:
            _, hx = colorchooser.askcolor(color=init, parent=self.root,
                                          title=f"{self.display_name()} - pick a colour")
        except tk.TclError:
            hx = None
        if hx:
            self.set_markup_color(key, hx.upper())

    def set_indicator(self, key, v):
        self.cfg[key] = v
        save_config(self.cfg)
        if key == "colorblind_mode" and self.panel:
            self.panel.LIVE_DOT = self.live_colors()[0]
            self.panel.refresh_header()
        self.refresh_frame()
        if getattr(self, "tab", None):
            self.tab.refresh()

    def set_border_width(self, px):
        self.cfg["border_width"] = int(px)
        save_config(self.cfg)
        self.apply_mode()                       # re-place the frame for the new width
        if getattr(self, "tab", None):
            self.tab.relayout()

    # -------------------------------------------- keyboard shortcuts window
    def show_shortcuts(self):
        if getattr(self, "_keys", None) and self._keys.winfo_exists():
            self._keys.lift()
            self._keys.focus_force()
            return
        pal = self.pal
        bg, fg, dim, hi = pal["bg"], pal["fg"], pal["dim"], pal["accent"]
        t = self._keys = tk.Toplevel(self.root)
        t.title(f"Keyboard shortcuts - {self.display_name()}")
        t.resizable(False, False)
        t.attributes("-topmost", True)
        try:
            t.iconbitmap(resource("icon", "ShisaPoint.ico"))
        except tk.TclError:
            pass
        t.configure(bg=bg, padx=14, pady=12)
        tk.Label(t, text="Click a shortcut, then press the new keys.", bg=bg, fg=dim,
                 font=("Segoe UI", 9)).grid(row=0, column=0, columnspan=2, sticky="w",
                                            pady=(0, 8))
        st = {"cap": None}
        combos = {}
        msg = tk.Label(t, text="", bg=bg, fg=pal["err"], font=("Segoe UI", 9), justify="left",
                       wraplength=360)

        def say(text, ok=False):
            msg.configure(text=text, fg=pal["ok"] if ok else pal["err"])

        def show(a):
            m = hotkey_map(self.cfg)
            taken = a in (self.hotkeys.failed or {})
            combos[a].configure(text=(m[a] or "(none)") + ("  \u26a0 in use" if taken else ""),
                                fg=pal["warn"] if taken else fg, bg=pal["field"])

        def stop_capture():
            if st["cap"]:
                a, st["cap"] = st["cap"], None
                self.hotkeys.resume()
                show(a)

        def start_capture(a):
            stop_capture()
            st["cap"] = a
            self.hotkeys.suspend()             # let the keys reach this window
            combos[a].configure(text="Press new keys\u2026 (Esc cancels)", fg=hi, bg=pal["field_on"])
            say("")
            t.focus_force()

        def on_key(ev):
            a = st["cap"]
            if not a:
                return
            ks = ev.keysym
            if ks in ("Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R",
                      "Win_L", "Win_R", "Super_L", "Super_R", "Meta_L", "Meta_R"):
                return "break"
            gk = ctypes.windll.user32.GetAsyncKeyState
            mods = ((MOD_CONTROL if gk(0x11) & 0x8000 else 0) |
                    (MOD_ALT if gk(0x12) & 0x8000 else 0) |
                    (MOD_SHIFT if gk(0x10) & 0x8000 else 0) |
                    (MOD_WIN if (gk(0x5B) | gk(0x5C)) & 0x8000 else 0))
            if ks == "Escape" and not mods:
                stop_capture()
                say("Cancelled.", ok=True)
                return "break"
            vk = ev.keycode if (0x30 <= ev.keycode <= 0x39 or 0x41 <= ev.keycode <= 0x5A or
                                0x70 <= ev.keycode <= 0x87) else None
            combo = format_combo(mods, vk) if vk else None
            if not combo or not parse_combo(combo):
                say("Use Ctrl, Alt or Win (plus optional Shift) with a letter, digit or "
                    "F1-F24.")
                return "break"
            m = hotkey_map(self.cfg)
            dup = next((b for b, c in m.items() if b != a and c == combo), None)
            stop_capture()                      # old bindings back first
            if combo == m[a]:
                say(f"{combo} is already {HOTKEY_LABELS[a]}.", ok=True)
                return "break"
            if dup:
                say(f"{combo} is already used for \u201c{HOTKEY_LABELS[dup]}\u201d. "
                    f"Kept {m[a] or '(none)'}.")
                return "break"
            if not self.hotkeys.try_bind(a, combo):
                say(f"{combo} is taken by another app. Kept {m[a] or '(none)'}.")
                return "break"
            hk = dict(m)
            hk[a] = combo
            self.cfg["hotkeys"] = hk
            save_config(self.cfg)
            print(f"Hotkey {a}: {combo}")
            show(a)
            self.after_hotkeys_changed()
            say(f"{HOTKEY_LABELS[a]}: {combo}", ok=True)
            return "break"

        for r, (a, _, label) in enumerate(HOTKEY_ACTIONS, 1):
            tk.Label(t, text=label, bg=bg, fg=fg, anchor="w",
                     font=("Segoe UI", 9)).grid(row=r, column=0, sticky="w", padx=(0, 12),
                                                pady=1)
            b = tk.Label(t, text="", bg=pal["field"], fg=fg, width=24, anchor="w", padx=6,
                         cursor="hand2", font=("Segoe UI", 9, "bold"))
            b.grid(row=r, column=1, sticky="we", pady=1)
            b.bind("<Button-1>", lambda e, a=a: start_capture(a))
            combos[a] = b
            show(a)
        n = len(HOTKEY_ACTIONS) + 1
        msg.grid(row=n, column=0, columnspan=2, sticky="w", pady=(8, 0))

        def reset():
            stop_capture()
            failed = self.hotkeys.apply(DEFAULT_HOTKEYS) or {}
            self.cfg["hotkeys"] = dict(DEFAULT_HOTKEYS)
            save_config(self.cfg)
            for a in combos:
                show(a)
            self.after_hotkeys_changed()
            if failed:
                say("Defaults restored, but in use by another app: " +
                    ", ".join(failed.values()))
            else:
                say("Defaults restored.", ok=True)

        def close():
            stop_capture()
            t.destroy()
        bar = tk.Frame(t, bg=bg)
        bar.grid(row=n + 1, column=0, columnspan=2, sticky="we", pady=(10, 0))
        tk.Button(bar, text="Reset to defaults", command=reset).pack(side="left")
        tk.Button(bar, text="Close", command=close, width=10).pack(side="right")
        theme_tree(bar, pal)
        dark_title_bar(t, self.theme_name() == "dark")
        t.bind("<Destroy>", lambda e: stop_capture() if e.widget is t else None)
        t.bind("<KeyPress>", on_key)
        t.protocol("WM_DELETE_WINDOW", close)
        t.update_idletasks()
        x = self.root.winfo_screenwidth() // 2 - t.winfo_width() // 2
        y = self.root.winfo_screenheight() // 2 - t.winfo_height() // 2
        t.geometry(f"+{x}+{y}")
        t.focus_force()

    def after_hotkeys_changed(self):
        """Refresh everything that shows a shortcut."""
        try:
            self.redraw()
        except Exception:
            pass
        if self.tray:
            self.tray.sig = None             # force update_menu (labels show combos)
            self.tray.update()

    def show_about(self):
        if getattr(self, "_about", None) and self._about.winfo_exists():
            self._about.lift()
            return
        t = self._about = tk.Toplevel(self.root)
        t.title("About " + self.display_name())
        t.resizable(False, False)
        t.attributes("-topmost", True)
        try:
            t.iconbitmap(resource("icon", "ShisaPoint.ico"))
        except tk.TclError:
            pass
        pal = self.pal
        bg = pal["bg"]
        t.configure(bg=bg, padx=18, pady=14)
        tk.Label(t, text=self.display_name(), bg=bg, fg=pal["fg"],
                 font=("Segoe UI", 13, "bold")).pack(anchor="w")
        tk.Label(t, text=f"Version {APP_VERSION}", bg=bg, fg=pal["dim"],
                 font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(t, text="Free forever. Tips keep it going.", bg=bg, fg=pal["fg"],
                 font=("Segoe UI", 10)).pack(anchor="w", pady=(10, 4))
        link = tk.Label(t, text=f"\u2665 Support {self.display_name()}", bg=bg, fg=pal["link"],
                        cursor="hand2", font=("Segoe UI", 10, "bold underline"))
        link.pack(anchor="w")
        link.bind("<Button-1>", lambda e: self.open_support())
        tk.Label(t, text=self.support_url(), bg=bg, fg=pal["subtle"],
                 font=("Segoe UI", 8)).pack(anchor="w")
        tk.Label(t, text="Files live in your ShisaPoint folder (ShisaPoint.exe, config.json, "
                 "ShisaPoint\\snapshots).", bg=bg, fg=pal["subtle"], font=("Segoe UI", 8),
                 wraplength=280, justify="left").pack(anchor="w", pady=(8, 0))
        cb = tk.Button(t, text="Close", command=t.destroy, width=10)
        cb.pack(anchor="e", pady=(12, 0))
        theme_tree(cb, pal)
        dark_title_bar(t, self.theme_name() == "dark")
        t.update_idletasks()
        x = self.root.winfo_screenwidth() // 2 - t.winfo_width() // 2
        y = self.root.winfo_screenheight() // 2 - t.winfo_height() // 2
        t.geometry(f"+{x}+{y}")

    # -- clear markup after session
    def markup_clear_s(self):
        try:
            return float(self.cfg.get("markup_clear_after_s", 60))
        except (TypeError, ValueError):
            return 60.0

    def custom_mclear(self):
        try:
            v = float(self.cfg.get("custom_markup_clear_s") or 0)
        except (TypeError, ValueError):
            return 0
        return v if v > 0 else 0

    def mclear_label(self):
        v = self.markup_clear_s()
        return "never" if v < 0 else "instant" if v == 0 else f"{v:g}s"

    def set_markup_clear(self, v):
        self.cfg["markup_clear_after_s"] = v
        save_config(self.cfg)

    def ask_markup_clear(self):
        from tkinter import simpledialog
        v = simpledialog.askinteger(self.display_name(), "Clear markup how many seconds after a "
                                    "session ends? (1-3600)",
                                    initialvalue=int(self.custom_mclear() or 120), minvalue=1,
                                    maxvalue=3600, parent=self.root)
        if v is None:
            return
        self.cfg["custom_markup_clear_s"] = v
        self.set_markup_clear(v)

    def cancel_markup_clear(self):
        job = getattr(self, "_mclear_job", None)
        if job:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        self._mclear_job = None

    def schedule_markup_clear(self):
        self.cancel_markup_clear()
        d = self.markup_clear_s()
        if d < 0 or not self.overlay:
            return
        self._mclear_job = self.root.after(int(d * 1000), self.markup_clear_now)

    def markup_clear_now(self):
        self._mclear_job = None
        if self.overlay and not self.follow_until:
            self.overlay.fade_clear()

    def panel_faded(self):
        """Linked: when the panel fades after a session its markup goes too (unless Never)."""
        if self.markup_clear_s() >= 0 and not self.follow_until:
            self.cancel_markup_clear()
            self.markup_clear_now()

    def panel_hide(self):
        try:
            return float(self.cfg.get("panel_hide_after_s", 60))
        except (TypeError, ValueError):
            return 60.0

    def custom_hide(self):
        try:
            v = float(self.cfg.get("custom_panel_hide_s") or 0)
        except (TypeError, ValueError):
            return 0
        return v if v > 0 else 0

    def hide_label(self):
        v = self.panel_hide()
        return "never" if v < 0 else "instant" if v == 0 else f"{v:g}s"

    def set_panel_hide(self, v):
        self.cfg["panel_hide_after_s"] = v
        save_config(self.cfg)

    def ask_panel_hide(self):
        from tkinter import simpledialog
        v = simpledialog.askinteger(self.display_name(), "Hide the panel how many seconds after a "
                                    "session ends? (1-3600)",
                                    initialvalue=int(self.custom_hide() or 120), minvalue=1,
                                    maxvalue=3600, parent=self.root)
        if v is None:
            return
        self.cfg["custom_panel_hide_s"] = v
        self.set_panel_hide(v)

    def toggle_clear_on_session(self):
        self.cfg["suggestions_clear_on_session"] = not self.cfg.get(
            "suggestions_clear_on_session", False)
        save_config(self.cfg)

    def toggle_newest_bottom(self):
        self.cfg["suggestions_newest_bottom"] = not self.cfg.get("suggestions_newest_bottom", False)
        save_config(self.cfg)
        if self.panel:
            self.panel.render()

    def reset_panel(self):
        if self.panel:
            self.panel.reset_position()

    def toggle_panel_lock(self):
        self.cfg["suggestions_clickthrough"] = not self.cfg.get("suggestions_clickthrough", False)
        save_config(self.cfg)
        if self.panel:
            self.panel.reapply()

    def confirm_meta(self, meta, reason):
        """Second look passed: mark the tentative snapshot as a finished thought."""
        if not meta.get("tentative"):
            return
        meta["tentative"] = False
        meta["confirmed"] = True
        meta["confirm_reason"] = reason
        meta["confirmed_at"] = datetime.now().astimezone().isoformat(timespec="milliseconds")
        meta.update(self.follow_fields())
        if self._pending and self._pending["meta"] is meta:
            self._pending = None
        if self.meta is meta:
            self.write_meta(meta)
        if meta.get("_hist"):
            self._log_confirm(meta)
        self.webhook_snapshot(meta)

    def webhook_snapshot(self, meta):
        if self.follow_until and self.cfg.get("follow_webhook_on_confirm", False):
            o = meta.get("ocr") or {}
            self.webhook("follow_snapshot", trigger=meta.get("trigger"),
                         confirm_reason=meta.get("confirm_reason"), file=meta.get("file"),
                         ocr_excerpt=str(o.get("text_clean") or o.get("text") or "")[-500:])

    def set_capture(self, v):
        """None = on change, else fixed interval (seconds)."""
        self.cfg["capture_mode"] = "on_change" if v is None else "interval"
        self._ref = self._prev = None
        if v is None:
            save_config(self.cfg)
            self.tab.refresh()
        else:
            self.set_interval(v)
        print("Capture:", self.cfg["capture_mode"], "" if v is None else f"{v}s")

    def custom_interval(self):
        try:
            v = float(self.cfg.get("custom_interval_seconds") or 0)
        except (TypeError, ValueError):
            return 0
        return v if 1 <= v <= 3600 else 0

    def interval_steps(self):
        steps = set(INTERVAL_PRESETS)
        if self.custom_interval():
            steps.add(self.custom_interval())
        return sorted(steps)

    def ask_custom_interval(self):
        from tkinter import simpledialog
        v = simpledialog.askfloat(self.display_name(), "Capture every how many seconds? (1-3600)",
                                  initialvalue=self.custom_interval() or 7,
                                  minvalue=1, maxvalue=3600, parent=self.root)
        if v is None:
            return
        v = round(v, 2)
        self.cfg["custom_interval_seconds"] = v
        self.set_capture(v)

    def toggle_privacy(self):
        self.cfg["privacy_pause_enabled"] = not self.cfg.get("privacy_pause_enabled", True)
        save_config(self.cfg)
        self.check_privacy()

    def toggle_region_mode(self):
        self.cfg["show_region_mode"] = not self.cfg.get("show_region_mode", False)
        save_config(self.cfg)

    def toggle_show_in_screenshots(self):
        self.cfg["show_in_screenshots"] = not bool(self.cfg.get("show_in_screenshots", False))
        save_config(self.cfg)
        self.apply_capture_exclusion()
        print("Show in screenshots:", "on" if self.cfg["show_in_screenshots"] else "off")

    def apply_capture_exclusion(self):
        """Re-apply WDA affinity to frame, toolbar, panel, markup, and open menus."""
        if getattr(self, "hwnd", None):
            self.frame_excluded = set_capture_exclusion(self.hwnd, self.cfg)
        ov = getattr(self, "overlay", None)
        if ov and getattr(ov, "hwnd", None):
            if not self.cfg.get("include_markup_in_capture"):
                ov.excluded = set_capture_exclusion(ov.hwnd, self.cfg)
            else:
                # Markup is meant to appear in the app's own captures; keep it visible.
                exclude_from_capture(ov.hwnd, False)
                ov.excluded = False
        skip = {h for h in (getattr(self, "hwnd", None),
                            getattr(ov, "hwnd", None) if ov else None) if h}
        for h in self._app_capture_hwnds(include_overlay=False):
            if h not in skip:
                set_capture_exclusion(h, self.cfg)

    def check_privacy(self):
        """Foreground title contains a privacy_pause_titles entry -> skip captures."""
        hit = None
        if self.cfg.get("privacy_pause_enabled", True):
            try:
                h = ctypes.windll.user32.GetForegroundWindow()
                title = window_title(h).lower() if h else ""
            except Exception:
                title = ""
            for t in self.cfg.get("privacy_pause_titles") or []:
                if t and str(t).lower() in title:
                    hit = str(t)
                    break
        if hit != self.privacy:
            self.privacy = hit
            print("Privacy pause:", f"ON ('{hit}' in front)" if hit else "off, resuming")
            self._ref = self._prev = None
            if getattr(self, "tab", None):
                self.tab.refresh()
        return hit

    def toggle_ocr(self):
        self.cfg["ocr_enabled"] = not self.cfg.get("ocr_enabled", True)
        save_config(self.cfg)
        if self.cfg["ocr_enabled"] and self.ocr is None:
            self.ocr = Ocr()

    def toggle_suggestions(self):
        if self.panel:
            self.panel.toggle()

    def set_interval(self, v):
        self.cfg["interval_seconds"] = v
        save_config(self.cfg)
        if getattr(self, "_cap_job", None):
            self.root.after_cancel(self._cap_job)
        self._cap_job = self.root.after(int(v * 1000), self.capture_loop)
        self.tab.refresh()

    def set_monitor(self, i):
        if self.off:
            self.set_off(False)
        self.cfg["monitor_index"] = i
        self.cfg["mode"] = "monitor"
        save_config(self.cfg)
        self.apply_mode()

    def start_wrap(self):
        if self.off:
            self.set_off(False)
        if not self.tab.picking:
            self.tab.start_pick()

    def toggle_markup(self):
        if self.overlay:
            self.overlay.toggle()

    # -------------------------------------------- retention / sessions
    def run_prune(self):
        c = self.cfg
        self.cache = prune(c["output_dir"], int(c.get("max_images", 0) or 0),
                           float(c.get("retention_minutes", 0) or 0),
                           float(c.get("max_cache_mb", 0) or 0))
        self._prunes = getattr(self, "_prunes", 0) + 1
        if self._prunes % 50 == 1:
            trim_history_log(c["output_dir"])
            trim_marks_log(c["output_dir"])

    def cache_label(self):
        b, n = getattr(self, "cache", (0, 0))
        return f"Cache: {b / 1048576:.0f} MB / {n} shots"

    def set_retention(self, minutes):
        c = self.cfg
        if minutes is None:                       # keep all
            c.update(retention_minutes=0, max_images=0, max_cache_mb=0)
        else:
            c["retention_minutes"] = minutes
            if not c.get("max_cache_mb"):
                c["max_cache_mb"] = DEFAULTS["max_cache_mb"]
        save_config(c)
        self.run_prune()
        if getattr(self, "tab", None):
            self.tab.refresh()
        print("History:", "keep all" if minutes is None else f"{minutes} min", "|", self.cache_label())

    def keep_all(self):
        return not any(float(self.cfg.get(k, 0) or 0) for k in
                       ("retention_minutes", "max_images", "max_cache_mb"))

    def retention_label(self, long=False):
        if self.keep_all():
            return "keep all" if long else "\u221e"
        r = float(self.cfg.get("retention_minutes", 0) or 0)
        if not r:                       # only size/count limits active
            return "size/count limit" if long else "lim"
        return f"{r:g} min" if long else f"{r:g}m"

    def cycle_retention(self):
        """1 -> 2 -> 5 -> 10 -> 15 -> 30 min -> keep all -> 1 ..."""
        steps = list(RETENTION_PRESETS) + [None]
        cur = None if self.keep_all() else float(self.cfg.get("retention_minutes", 0) or 0)
        if cur is None:
            nxt = steps[0]
        else:
            nxt = next((v for v in RETENTION_PRESETS if v > cur + 1e-6), None)
        self.set_retention(nxt)
        self.tab.refresh()

    def set_session_format(self, fmt):
        self.cfg["session_format"] = fmt
        save_config(self.cfg)

    def save_session(self):
        if getattr(self, "_saving", False):
            return print("Session save already running")
        self._saving = True
        self.session_reset()
        folder, fmt = self.cfg["output_dir"], self.cfg.get("session_format", "zip")
        t0 = time.time()

        def work():
            try:
                path, size, n = build_session(folder, fmt, secure=self.secure())
                msg = f"Session saved: {path} ({n} frames, {size / 1048576:.1f} MB, " \
                      f"{time.time() - t0:.1f}s)"
                self.post(lambda: (print(msg), self.tab.flash()))
            except Exception as e:
                err = e
                self.post(lambda: print("Session save failed:", err))
            finally:
                self._saving = False
        threading.Thread(target=work, daemon=True).start()

    def clear_history(self):
        from tkinter import messagebox
        b, n = getattr(self, "cache", (0, 0))
        if not messagebox.askyesno(self.display_name(),
                                   f"Delete {n} history snapshots ({b / 1048576:.0f} MB)?\n"
                                   "The latest frame and saved sessions are kept."):
            return
        folder = self.cfg["output_dir"]
        for name, _, _ in history_files(folder):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass
        for log in (HISTORY_LOG, MARKS_LOG):
            try:
                os.remove(os.path.join(folder, log))
            except OSError:
                pass
        self._marks_sig = None
        self.run_prune()
        self.session_reset()
        print("History cleared")

    def open_session(self, path=None):
        """Pick a saved session (.zip / .zip.dpapi) and show it in the session viewer."""
        from tkinter import filedialog, messagebox
        d = os.path.join(self.cfg["output_dir"], "sessions")
        os.makedirs(d, exist_ok=True)
        if not path:
            path = filedialog.askopenfilename(
                parent=self.root, title=f"Open session - {self.display_name()}", initialdir=d,
                filetypes=[("Sessions", "*.zip *.dpapi"), ("All files", "*.*")])
        if not path:
            return
        try:
            SessionViewer(self, path)
        except Exception as e:
            print("Open session failed:", type(e).__name__, e)
            messagebox.showerror(self.display_name(), f"Could not open this session:\n{e}",
                                 parent=self.root)

    def open_sessions(self):
        d = os.path.join(self.cfg["output_dir"], "sessions")
        os.makedirs(d, exist_ok=True)
        os.startfile(d)

    def open_folder(self):
        os.makedirs(self.cfg["output_dir"], exist_ok=True)
        os.startfile(self.cfg["output_dir"])

    def cycle_interval(self, steps):
        if self.cfg.get("capture_mode") == "on_change":   # tab click leaves on-change
            self.cfg["capture_mode"] = "interval"
            self._ref = self._prev = None
            return self.set_interval(steps[0])
        cur = float(self.cfg["interval_seconds"])
        nxt = next((v for v in steps if v > cur + 1e-6), None)
        if nxt is None:                                    # wrap around to on-change
            return self.set_capture(None)
        print("Interval:", nxt, "s")
        self.set_interval(nxt)

    def to_region_here(self):
        """Switch to region mode keeping the current capture rect."""
        if self.cfg["mode"] == "region":
            return
        bw = int(self.cfg["border_width"])
        rc = self.capture_rect() if self.cfg["mode"] == "window" else None
        if rc:   # wrap -> region: keep the window's rect, but sanitised
            self.cfg.update(x=rc["left"] - bw, y=rc["top"] - bw,
                            width=rc["width"] + 2 * bw, height=rc["height"] + 2 * bw)
        # monitor -> region: keep the saved region geometry (never the monitor rect)
        self.cfg["mode"] = "region"
        self.sanitize_region()
        self.apply_mode()
        self.root.update_idletasks()

    def sanitize_region(self):
        """Keep region geometry on-screen and smaller than its monitor; a frame
        the size of a monitor puts its border off-screen / under the taskbar."""
        c = self.cfg
        x, y, w, h = int(c["x"]), int(c["y"]), int(c["width"]), int(c["height"])
        mon = self.monitors[self.monitor_at(x + w // 2, y + h // 2)]
        ml, mt, mw, mh = mon["left"], mon["top"], mon["width"], mon["height"]
        if w >= mw - 20 or h >= mh - 20 or w < 4 * GRIP or h < 4 * GRIP:
            w, h = min(DEFAULTS["width"], mw - 40), min(DEFAULTS["height"], mh - 40)
            x, y = ml + (mw - w) // 2, mt + (mh - h) // 2
        x = min(max(x, ml), ml + mw - w)
        y = min(max(y, mt), mt + mh - h)
        if (x, y, w, h) != (c["x"], c["y"], c["width"], c["height"]):
            print("Region geometry sanitised:", (c["x"], c["y"], c["width"], c["height"]),
                  "->", (x, y, w, h))
            c.update(x=x, y=y, width=w, height=h)
            save_config(c)

    def monitor_at(self, x, y):
        for i, m in enumerate(self.monitors[1:], 1):
            if m["left"] <= x < m["left"] + m["width"] and m["top"] <= y < m["top"] + m["height"]:
                return i
        return 1

    def full_screen(self):
        if self.cfg["mode"] == "monitor":
            n = len(self.monitors) - 1
            self.cfg["monitor_index"] = int(self.cfg["monitor_index"]) % n + 1
        else:
            x, y, w, h = self.frame_rect()
            self.cfg["monitor_index"] = self.monitor_at(x + w // 2, y + h // 2)
            self.cfg["mode"] = "monitor"
        save_config(self.cfg)
        self.apply_mode()

    def wrap_window(self, hwnd):
        self.target = hwnd
        self.cfg["mode"] = "window"
        title = window_title(hwnd)
        if title:
            self.cfg["window_title"] = title
        print("Tracking window:", "(title hidden, secure mode)" if SECURE["on"] else title)
        save_config(self.cfg)
        self.apply_mode()

    # -------------------------------------------- hotkeys
    def hk(self, action):
        """Current combo for an action ('' if unbound)."""
        return hotkey_map(self.cfg).get(action, "")

    def hk_tag(self, action):
        k = self.hk(action)
        return f"  ({k})" if k else ""

    def hk_or(self, action):
        k = self.hk(action)
        return f" or {k}" if k else ""

    def on_hotkey(self, action):
        k = {"edit": "L", "pause": "P", "quit": "Q", "monitor": "M", "window": "W",
             "markup": "K", "clear": "C", "onoff": "O", "save": "S", "panel": "G",
             "follow": "F"}.get(action)
        if k == "L" and self.cfg["mode"] == "region":
            self.edit = not self.edit
            self.set_clickthrough(not self.edit)
            if not self.edit:
                self.remember_geometry()
        elif k == "L":
            # leave monitor/window mode back to region
            self.cfg["mode"] = "region"
            self.apply_mode()
        elif k == "P":
            self.toggle_pause()
            return
        elif k == "Q":
            return self.quit()
        elif k == "M":
            if self.cfg["mode"] == "monitor":
                n = len(self.monitors) - 1
                self.cfg["monitor_index"] = int(self.cfg["monitor_index"]) % n + 1
            self.cfg["mode"] = "monitor"
            save_config(self.cfg)
            self.apply_mode()
        elif k == "W":
            self.snap_foreground()
        elif k == "S":
            self.save_session()
        elif k == "O":
            self.toggle_off()
        elif k == "K" and self.overlay:
            self.overlay.toggle()
        elif k == "G" and self.panel:
            self.panel.toggle()
        elif k == "F":
            self.toggle_follow()
        elif k == "C" and self.overlay:
            self.overlay.clear()
        self.redraw()

    # -------------------------------------------- move / resize (region)
    def edge_at(self, x, y):
        w, h = self.cv.winfo_width(), self.cv.winfo_height()
        e = "n" if y < GRIP else "s" if y > h - GRIP else ""
        return e + ("w" if x < GRIP else "e" if x > w - GRIP else "")

    def on_motion(self, ev):
        if self.edit:
            cur = {"n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
                   "e": "sb_h_double_arrow", "w": "sb_h_double_arrow",
                   "ne": "size_ne_sw", "sw": "size_ne_sw",
                   "nw": "size_nw_se", "se": "size_nw_se"}
            self.cv.config(cursor=cur.get(self.edge_at(ev.x, ev.y), "fleur"))

    def on_press(self, ev):
        if self.edit:
            r = self.root
            self.drag = (self.edge_at(ev.x, ev.y), ev.x_root, ev.y_root,
                         r.winfo_x(), r.winfo_y(), r.winfo_width(), r.winfo_height())

    def on_drag(self, ev):
        if not (self.edit and self.drag):
            return
        edge, sx, sy, x, y, w, h = self.drag
        dx, dy, mn = ev.x_root - sx, ev.y_root - sy, 4 * GRIP
        if not edge:
            x, y = x + dx, y + dy
        if "e" in edge: w = max(mn, w + dx)
        if "s" in edge: h = max(mn, h + dy)
        if "w" in edge:
            nw = max(mn, w - dx); x += w - nw; w = nw
        if "n" in edge:
            nh = max(mn, h - dy); y += h - nh; h = nh
        self.set_geo(w, h, x, y)

    def remember_geometry(self):
        if self.cfg["mode"] == "region" and not getattr(self, "off", False) \
                and self.root.winfo_viewable():
            r = self.root
            r.update_idletasks()
            self.cfg.update(x=r.winfo_x(), y=r.winfo_y(),
                            width=r.winfo_width(), height=r.winfo_height())
        save_config(self.cfg)

    # -------------------------------------------- capture
    def _app_capture_hwnds(self, include_overlay=True):
        """HWNDs of the frame, tab, panel, markup, and tips, for grab cloaking."""
        hs = []
        if getattr(self, "hwnd", None):
            hs.append(self.hwnd)
        if include_overlay and self.overlay and getattr(self.overlay, "hwnd", None):
            hs.append(self.overlay.hwnd)
        if self.panel and getattr(self.panel, "hwnd", None):
            hs.append(self.panel.hwnd)
        if getattr(self, "tab", None):
            try:
                hs.extend(self.tab.hwnds())
            except Exception:
                if getattr(self.tab, "hwnd", None):
                    hs.append(self.tab.hwnd)
        m = getattr(self, "_menu", None)
        if m is not None:
            try:
                for menu in m._menus():
                    h = getattr(menu, "hwnd", None)
                    if h:
                        hs.append(h)
            except Exception:
                pass
        return [h for h in hs if h]

    def _chrome_windows_for_grab(self):
        """Tk windows to withdraw when WDA cloaking is unavailable."""
        wins = []
        if self.overlay and self.overlay.hide_for_capture():
            wins.append(self.overlay.win)
        if self.panel and getattr(self.panel, "shown", False):
            wins.append(self.panel.win)
        if getattr(self, "tab", None):
            if getattr(self.tab, "tip", None):
                wins.append(self.tab.tip)
            wins.append(self.tab.win)
        m = getattr(self, "_menu", None)
        if m is not None:
            try:
                for menu in m._menus():
                    w = getattr(menu, "win", None)
                    if w is not None:
                        wins.append(w)
            except Exception:
                pass
        return wins

    @contextlib.contextmanager
    def _clean_own_grab(self):
        """Keep the app's own snapshots/OCR free of its frame, panel and markup.

        Default (show_in_screenshots false, WDA working): no-op, same as before.
        If the overlay is not capture-excluded, it is withdrawn during the grab
        (the existing 'hidden during grabs' fallback). When show_in_screenshots
        is on, windows stay visible to external tools (WDA_NONE) except during
        this grab, when they are temporarily excluded (or withdrawn if that
        fails). Restore always runs in finally so an interrupted grab cannot
        leave windows stuck at WDA_EXCLUDEFROMCAPTURE.
        """
        show = bool(self.cfg.get("show_in_screenshots", False))
        hide_ov = self.overlay and self.overlay.hide_for_capture()
        cloaked = False
        hidden = []
        hwnds = []
        try:
            if show:
                # Leave markup in the grab when the user asked to include it.
                include_ov = not bool(self.cfg.get("include_markup_in_capture"))
                hwnds = self._app_capture_hwnds(include_overlay=include_ov)
                cloaked = all(exclude_from_capture(h, True) for h in hwnds) if hwnds else True
                if not cloaked:
                    # Partial cloak would otherwise leave a subset excluded.
                    for h in hwnds:
                        exclude_from_capture(h, False)
                    hwnds = []
            if hide_ov and not cloaked:
                hidden.append(self.overlay.win)
            if show and not cloaked:
                for w in self._chrome_windows_for_grab():
                    if w not in hidden:
                        hidden.append(w)
            for w in hidden:
                try:
                    w.withdraw()
                    w.update()
                except tk.TclError:
                    pass
            yield
        finally:
            for h in hwnds:
                set_capture_exclusion(h, self.cfg)
            for w in hidden:
                try:
                    w.deiconify()
                except tk.TclError:
                    pass

    def capture_rect(self):
        """Screen rect (physical px) to grab, or None to skip this tick."""
        m = self.cfg["mode"]
        if m == "monitor":
            mon = dict(self.monitors[int(self.cfg["monitor_index"])])
            if not self.frame_excluded:     # border is drawn inside the monitor
                bw = int(self.cfg["border_width"])
                mon = {"left": mon["left"] + bw, "top": mon["top"] + bw,
                       "width": mon["width"] - 2 * bw, "height": mon["height"] - 2 * bw}
            return mon
        if m == "window":
            if not self.target_ok() or ctypes.windll.user32.IsIconic(self.target):
                return None
            l, t, r, b = window_rect(self.target)
        else:
            rc = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(self.hwnd, ctypes.byref(rc))
            bw = int(self.cfg["border_width"])
            l, t, r, b = rc.left + bw, rc.top + bw, rc.right - bw, rc.bottom - bw
        if r - l <= 0 or b - t <= 0:
            return None
        return {"left": l, "top": t, "width": r - l, "height": b - t}

    def last_meta_rect(self):
        if self.meta:
            s = self.meta["screen_rect"]
            return {"left": s["left"], "top": s["top"], "scale": self.meta["scale"]}
        rc = self.capture_rect()
        if not rc:
            return None
        # no snapshot yet: use the scale the next saved image will have (image_max_width)
        maxw = int(self.cfg.get("image_max_width", 1280) or 0)
        sc = maxw / float(rc["width"]) if maxw and rc["width"] > maxw else 1.0
        return {"left": rc["left"], "top": rc["top"], "scale": sc}

    def capture_once(self):
        region = self.capture_rect()
        if not region:
            return False
        with self._clean_own_grab():
            with MSS() as sct:
                shot = sct.grab(region)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        full = img                          # OCR runs on full resolution (small UI text)
        maxw = int(self.cfg.get("image_max_width", 1280) or 0)
        if maxw and img.width > maxw:      # all coords (markup/OCR) use this pixel space
            img = img.resize((maxw, max(1, round(img.height * maxw / img.width))), Image.LANCZOS)
        out = self.cfg["output_dir"]
        now = datetime.now()
        jpg = str(self.cfg.get("image_format", "jpg")).lower() in ("jpg", "jpeg")
        ext = ".jpg" if jpg else ".png"
        save = (lambda im, path: im.save(path, "JPEG", quality=int(self.cfg.get(
            "jpeg_quality", 80)), optimize=True)) if jpg else (lambda im, path: im.save(path, "PNG"))
        sec = self.secure()
        name = f"lens_{now.strftime('%Y%m%d_%H%M%S_%f')[:-3]}{ext}"
        if sec:                            # encrypted at rest: lens_*.jpg.dpapi, latest.jpg.dpapi
            import io
            buf = io.BytesIO()
            save(img, buf)
            data = buf.getvalue()
            name += ".dpapi"
            write_secure(os.path.join(out, name), data)
            latest = "latest" + ext + ".dpapi"
            write_secure(os.path.join(out, latest), data)
        else:
            save(img, os.path.join(out, name))
            latest = "latest" + ext
            tmp = os.path.join(out, "latest.tmp" + ext)
            save(img, tmp)
            os.replace(tmp, os.path.join(out, latest))
        for st in ("latest.png", "latest.jpg", "latest.png.dpapi", "latest.jpg.dpapi"):
            if st != latest and os.path.exists(os.path.join(out, st)):
                try:                       # avoid reading an outdated / plain frame
                    os.remove(os.path.join(out, st))
                except OSError:
                    pass
        scale = img.width / float(region["width"])
        self.meta = {
            "timestamp": now.astimezone().isoformat(timespec="milliseconds"),
            "file": name,
            "latest": latest,
            "mode": self.cfg["mode"],
            "monitor_index": self.cfg["monitor_index"] if self.cfg["mode"] == "monitor" else None,
            "window_title": window_title(self.target) if self.cfg["mode"] == "window" and self.target else None,
            "screen_rect": {"left": region["left"], "top": region["top"],
                            "right": region["left"] + region["width"],
                            "bottom": region["top"] + region["height"],
                            "width": region["width"], "height": region["height"]},
            "image_size": {"width": img.width, "height": img.height},
            "scale": scale,
            "virtual_screen": self.virtual,
            "monitors": self.monitors[1:],
            "markup_in_capture": bool(self.cfg["include_markup_in_capture"]),
            "note": f"coords are {latest} pixels; screen_x = screen_rect.left + image_x / scale "
                    "(physical pixels)",
        }
        trig = getattr(self, "_trigger", "interval")
        self.meta["trigger"] = trig
        tentative = trig == "change" and int(self.cfg.get("confirm_ms", 2000) or 0) > 0
        self.meta.update({"tentative": tentative, "confirmed": not tentative,
                          "confirm_reason": None if tentative else "immediate",
                          "confirmed_at": None if tentative else self.meta["timestamp"]})
        self.meta.update(self.follow_fields())
        self.meta["ocr"] = None
        self.meta["_hist"] = False          # set True once its history.jsonl line is written
        meta = self.meta
        self.write_meta(meta)
        use_ocr = self.ocr and self.ocr.engine and self.cfg.get("ocr_enabled", True)
        if use_ocr:
            wb = None                       # taskbar top edge in saved-image px
            try:
                l, t = region["left"], region["top"]
                wa = self.work_area(l + region["width"] // 2, t + region["height"] // 2)
                if wa[3] - t < region["height"]:
                    wb = (wa[3] - t) * scale
            except Exception:
                pass
            threading.Thread(target=self._ocr_and_log,
                             args=(full, meta, img.width / float(full.width), wb),
                             daemon=True).start()
        else:
            self._log_history(meta)
        self.run_prune()
        self.session_frames += 1

    def done_lines(self, r, work_bottom=None):
        """OCR lines that changed since the previous snapshot, minus taskbar, clock/date and
        editor status-bar lines. Used for the punctuation done-check."""
        prev = getattr(self, "_prev_ocr_lines", None)
        out = []
        for ln in r.get("lines", []):
            t = str(ln.get("text", "")).strip()
            if not t or (prev is not None and t in prev):
                continue
            ws = ln.get("words") or []
            if work_bottom is not None and ws and min(w["y"] for w in ws) >= work_bottom - 1:
                continue                      # taskbar strip
            if CLOCK_RE.match(t) or NOISE_RE.match(t):
                continue
            out.append(t)
        return out

    def _ocr_and_log(self, img, meta, k=1.0, work_bottom=None):
        """img: full-res capture; k maps its pixels to the saved image's pixels."""
        out = self.cfg["output_dir"]
        try:
            up = float(self.cfg.get("ocr_upscale_factor", 2.0) or 1.0) \
                if self.cfg.get("ocr_upscale", True) else 1.0
            with self._ocr_lock:
                t0 = time.monotonic()
                r = self.ocr.recognize(img, upscale=up)
            if r is not None:
                r["ms"] = int((time.monotonic() - t0) * 1000)
                if k != 1.0:
                    for ln in r["lines"]:
                        for wd in ln["words"]:
                            for key in ("x", "y", "w", "h"):
                                wd[key] = round(wd[key] * k)
                r["source_size"] = {"width": img.width, "height": img.height}
                r["coords"] = meta.get("latest", "latest.png") + " pixels"
                r["text_clean"] = fix_apostrophes(r["text"])   # r["text"] stays raw
                meta["ocr"] = r
                if self.panel and any(i.get("_meta") is meta and i.get("target")
                                      for i in self.panel.items):
                    self.post(self.panel.pointers)       # targets can be boxed now
                elif self.panel and self.meta is meta and self.overlay and self.overlay.visible:
                    self.post(lambda: self.panel.retrack(meta))   # boxes follow the text
                if self.meta is meta:      # still the newest frame
                    self.write_meta(meta)
                    if self.secure():
                        write_secure(os.path.join(out, "latest.txt.dpapi"),
                                     r["text"].encode("utf-8"))
                        stale = os.path.join(out, "latest.txt")
                    else:
                        tmp = os.path.join(out, "latest.tmp.txt")
                        with open(tmp, "w", encoding="utf-8") as f:
                            f.write(r["text"])
                        os.replace(tmp, os.path.join(out, "latest.txt"))
                        stale = os.path.join(out, "latest.txt.dpapi")
                    if os.path.exists(stale):
                        try:
                            os.remove(stale)
                        except OSError:
                            pass
            if r is not None:
                changed = self.done_lines(r, work_bottom)
                self._prev_ocr_lines = {str(ln.get("text", "")).strip() for ln in r["lines"]}
                if meta.get("tentative") and changed and \
                        any(t.rstrip().rstrip('"\'\u201d\u2019)').endswith((".", "?", "!"))
                            for t in changed):
                    self.post(lambda: self.confirm_meta(meta, "punctuation"))
        except Exception as e:
            print("OCR failed:", e)
            if not getattr(self, "_ocr_tb_logged", False):
                self._ocr_tb_logged = True
                import traceback
                print(traceback.format_exc())
        self._log_history(meta)

    def _log_history(self, meta):
        try:
            line = {k: meta.get(k) for k in (
                "file", "timestamp", "mode", "monitor_index", "window_title",
                "screen_rect", "image_size", "scale", "trigger", "tentative",
                "confirmed", "confirm_reason", "confirmed_at", "follow_active")}
            o = meta.get("ocr")
            if o:
                line["ocr_text"] = o["text"]
                line["ocr_words"] = [w for l in o["lines"] for w in l["words"]]
            with open(os.path.join(self.cfg["output_dir"], HISTORY_LOG), "a",
                      encoding="utf-8") as f:
                f.write(hist_encode(line))
            meta["_hist"] = True
        except OSError:
            pass

    def log_marks(self):
        """Append the on-screen state (suggestion boxes + markup marks, in the latest
        snapshot's image px) to marks.jsonl when it changed. Called after every overlay redraw,
        so boxes that move or hide are recorded as they happen. Encrypted lines in secure mode."""
        meta, ov = self.meta, getattr(self, "overlay", None)
        if not meta or not ov or not meta.get("file"):
            return
        try:
            dst = frame_rect(meta)
            if not dst:
                return
            marks, boxes = [], {}
            for it in ov.items:
                try:
                    m = convert_mark(it, it.get("_anchor") or SCREEN_ANCHOR, dst)
                except (KeyError, TypeError, ValueError):
                    continue
                if it.get("_sugg"):
                    boxes[str(it.get("label"))] = m
                else:
                    marks.append(m)
            sugg = []
            panel = getattr(self, "panel", None)
            if panel:
                now = time.time()
                for n, it in enumerate(panel.display_items(), 1):
                    if it.get("_exp") is not None and it["_exp"] <= now:
                        continue
                    b = boxes.get(str(n))
                    rect = b and b.get("type") == "rect"
                    circ = b and b.get("type") == "circle"
                    sugg.append({"n": n, "text": str(it.get("text", "")),
                                 "target": it.get("target"),
                                 "box": [b["x"], b["y"], b["w"], b["h"]] if rect else None,
                                 "circle": [b["x"], b["y"], b.get("r", 14)] if circ else None,
                                 "gone": bool(it.get("_gone")), "old": bool(it.get("_old")),
                                 "stale": bool(it.get("_stale"))})
            rec = {"file": meta["file"], "rect": dst, "visible": bool(ov.visible),
                   "suggestions": sugg, "marks": marks}
            sig = json.dumps(rec, sort_keys=True, default=str)
            if sig == self._marks_sig:
                return
            self._marks_sig = sig
            out = {"t": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                   "ts": round(time.time(), 3)}
            out.update(rec)
            with open(os.path.join(self.cfg["output_dir"], MARKS_LOG), "a",
                      encoding="utf-8") as f:
                f.write(hist_encode(out))
        except Exception as e:
            if str(e) != getattr(self, "_marks_err", None):
                self._marks_err = str(e)
                print("Marks log failed:", e)

    def _log_confirm(self, meta):
        """Second look finished after the history line was written: append a confirm event."""
        try:
            with open(os.path.join(self.cfg["output_dir"], HISTORY_LOG), "a",
                      encoding="utf-8") as f:
                f.write(hist_encode({"event": "confirm", "of_file": meta.get("file"),
                                     "confirmed": True,
                                     "confirm_reason": meta.get("confirm_reason"),
                                     "confirmed_at": meta.get("confirmed_at")}))
        except OSError:
            pass

    def change_loop(self):
        """on_change mode: cheap grayscale diff; snap once the screen settles."""
        try:
            if (self.on_change_active() and not self.paused
                    and not self.edit and not self.off and not self.privacy):
                self._request_change_tick()
        except Exception as e:
            print("Change check failed:", e)
        self.root.after(max(100, int(self.cfg.get("change_poll_ms", 250))), self.change_loop)

    def crop_taskbar(self, img, region):
        """Diff only the part of the capture inside the monitor's work area, so the
        taskbar strip (clock, tray icons) never counts as a change."""
        l, t = region["left"], region["top"]
        wl, wt, wr, wb = self.work_area(l + region["width"] // 2, t + region["height"] // 2)
        box = (max(0, wl - l), max(0, wt - t), min(img.width, wr - l), min(img.height, wb - t))
        if box[2] - box[0] < 16 or box[3] - box[1] < 16 or box == (0, 0, img.width, img.height):
            return img
        return img.crop(box)

    def _start_change_worker(self):
        """Background thread for change-detection grabs. One mss instance, reused."""
        self._change_stop = threading.Event()
        self._change_jobs = queue.Queue(maxsize=1)
        self._change_busy = False
        t = threading.Thread(target=self._change_worker, name="change-detect", daemon=True)
        t.start()
        self._change_thread = t

    def _stop_change_worker(self):
        stop = getattr(self, "_change_stop", None)
        if stop is not None:
            stop.set()
        jobs = getattr(self, "_change_jobs", None)
        if jobs is not None:
            try:
                jobs.put_nowait(None)
            except Exception:
                pass

    def _change_worker(self):
        try:
            sct = MSS()
        except Exception as e:
            print("Change worker failed to start:", e)
            return
        try:
            while not self._change_stop.is_set():
                try:
                    job = self._change_jobs.get(timeout=0.15)
                except queue.Empty:
                    continue
                if job is None:
                    break
                try:
                    small = self._grab_diff_frame(sct, job)
                except Exception as e:
                    print("Change check failed:", e)
                    small = None
                self.post(lambda s=small: self._change_apply(s))
        finally:
            try:
                sct.close()
            except Exception:
                pass

    def _request_change_tick(self):
        """Collect capture geometry on the Tk thread and hand a grab job to the worker.
        Does not cloak windows: own chrome is masked out of the diff instead."""
        if getattr(self, "_change_busy", False):
            return
        region = self.capture_rect()
        if not region:
            return
        wa = None
        if self.cfg.get("ignore_taskbar_changes", True):
            try:
                wa = self.work_area(region["left"] + region["width"] // 2,
                                    region["top"] + region["height"] // 2)
            except Exception:
                wa = None
        job = {
            "region": dict(region),
            "mask_rects": self._own_mask_rects(region),
            "ignore_regs": list(self.cfg.get("ignore_regions_px") or []),
            "crop_taskbar": bool(self.cfg.get("ignore_taskbar_changes", True)),
            "work_area": wa,
            "diff_width": int(self.cfg.get("diff_width", 640) or 640),
        }
        self._change_busy = True
        try:
            self._change_jobs.put_nowait(job)
        except queue.Full:
            self._change_busy = False

    def _screen_to_capture_rect(self, region, l, t, r, b):
        """Screen (l,t,r,b) -> capture-relative (x,y,w,h), or None if no overlap."""
        x = l - region["left"]
        y = t - region["top"]
        w = r - l
        h = b - t
        if w <= 0 or h <= 0:
            return None
        return (x, y, w, h)

    def _markup_mask_rects(self, region):
        """Capture-relative boxes around on-screen markup (not the full overlay window)."""
        ov = getattr(self, "overlay", None)
        if not ov or not ov.visible or not getattr(ov, "items", None):
            return []
        v = self.virtual
        out = []
        pad = 8
        for it in ov.items:
            try:
                box = self._mark_screen_box(ov, it, v)
            except Exception:
                continue
            if not box:
                continue
            sl, st, sr, sb = box
            rec = self._screen_to_capture_rect(region, sl - pad, st - pad, sr + pad, sb + pad)
            if rec:
                out.append(rec)
        return out

    def _mark_screen_box(self, ov, it, v):
        """Axis-aligned screen rect of one markup item, or None."""
        P = lambda x, y: ov.map_pt(it, x, y)
        to_s = lambda cx, cy: (cx + v["left"], cy + v["top"])
        t = it.get("type", "rect")
        pts = []
        if t in ("rect", "circle", "ellipse"):
            if t == "circle" and "r" in it:
                cx, cy = P(it["x"], it["y"])
                rad = float(it["r"]) / ((it.get("_anchor") or {}).get("scale", 1.0) or 1.0)
                pts = [(cx - rad, cy - rad), (cx + rad, cy + rad)]
            else:
                pts = [P(it["x"], it["y"]), P(it["x"] + it["w"], it["y"] + it["h"])]
        elif t in ("line", "arrow"):
            pts = [P(it["x1"], it["y1"]), P(it["x2"], it["y2"])]
        elif t in ("polyline", "freehand"):
            pts = [P(x, y) for x, y in it.get("points") or []]
        elif t == "text":
            x, y = P(it["x"], it["y"])
            size = float(it.get("font_size", 16) or 16)
            text = str(it.get("text", "") or "")
            pts = [(x, y), (x + max(8, 0.6 * size * max(len(text), 1)), y + size * 1.4)]
        if not pts:
            return None
        xs, ys = zip(*pts)
        sl, st = to_s(min(xs), min(ys))
        sr, sb = to_s(max(xs), max(ys))
        return sl, st, sr, sb

    def _own_mask_rects(self, region):
        """Capture-relative (x,y,w,h) of own chrome to ignore in the change diff.

        The frame and markup overlay cover the capture area, so their full HWNDs are
        not painted over (that would hide real content). Instead: frame border only,
        individual markup boxes, and compact chrome (tab, panel, menus, tips).
        """
        out = []
        # compact chrome
        hs = []
        if self.panel and getattr(self.panel, "hwnd", None):
            hs.append(self.panel.hwnd)
        if getattr(self, "tab", None):
            try:
                hs.extend(self.tab.hwnds())
            except Exception:
                if getattr(self.tab, "hwnd", None):
                    hs.append(self.tab.hwnd)
        m = getattr(self, "_menu", None)
        if m is not None:
            try:
                for menu in m._menus():
                    h = getattr(menu, "hwnd", None)
                    if h:
                        hs.append(h)
            except Exception:
                pass
        for h in hs:
            try:
                l, t, r, b = window_rect(h)
            except Exception:
                continue
            rec = self._screen_to_capture_rect(region, l, t, r, b)
            if rec:
                out.append(rec)
        # frame border ring only (interior is the see-through capture)
        if getattr(self, "hwnd", None):
            try:
                l, t, r, b = window_rect(self.hwnd)
                bw = int(self.cfg.get("border_width", 3) or 3)
                if bw > 0 and r > l and b > t:
                    for sl, st, sr, sb in (
                            (l, t, r, t + bw),
                            (l, b - bw, r, b),
                            (l, t, l + bw, b),
                            (r - bw, t, r, b)):
                        rec = self._screen_to_capture_rect(region, sl, st, sr, sb)
                        if rec:
                            out.append(rec)
            except Exception:
                pass
        out.extend(self._markup_mask_rects(region))
        return out

    def _grab_diff_frame(self, sct, job):
        """Worker-thread grab: no cloaking; own chrome already listed in mask_rects."""
        from PIL import ImageDraw
        region = job["region"]
        shot = sct.grab(region)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        dr = ImageDraw.Draw(img)
        for r in (job.get("ignore_regs") or []) + (job.get("mask_rects") or []):
            try:
                x, y, w, h = (int(v) for v in r)
                dr.rectangle((x, y, x + w - 1, y + h - 1), fill=(0, 0, 0))
            except (TypeError, ValueError):
                pass
        if job.get("crop_taskbar") and job.get("work_area"):
            l, t = region["left"], region["top"]
            wl, wt, wr, wb = job["work_area"]
            box = (max(0, wl - l), max(0, wt - t),
                   min(img.width, wr - l), min(img.height, wb - t))
            if not (box[2] - box[0] < 16 or box[3] - box[1] < 16
                    or box == (0, 0, img.width, img.height)):
                img = img.crop(box)
        dw = max(160, min(img.width, int(job.get("diff_width", 640) or 640)))
        return img.convert("L").resize((dw, max(1, int(dw * img.height / img.width))),
                                       Image.BOX)

    def _diff_frac(self, a, b):
        """> change_threshold means changed. Ignores caret-like (tiny + narrow) diffs."""
        thr = float(self.cfg.get("change_threshold", 0.002))
        tile = max(4, int(self.cfg.get("diff_tile_px", 16) or 16))
        tmin = max(1, int(self.cfg.get("diff_tile_min_px", 6) or 6))
        delta = int(self.cfg.get("diff_pixel_delta", 24) or 24)
        if b is None or a.size != b.size:
            return 1.0
        mask = ImageChops.difference(a, b).point(lambda v: 255 if v > delta else 0)
        if is_caret_diff(mask):
            return 0.0
        g = mask.histogram()[255] / float(a.width * a.height)
        if g > thr:
            return g
        tiles = mask.resize((max(1, -(-a.width // tile)), max(1, -(-a.height // tile))),
                            Image.BOX)
        peak = tiles.getextrema()[1] / 255.0 * tile * tile
        return 1.0 if peak >= tmin - 0.5 else g

    def _change_apply(self, small):
        """Tk-thread half of a change tick: compare, maybe snap. capture_once still cloaks."""
        try:
            if small is None:
                return
            now = time.monotonic()
            thr = float(self.cfg.get("change_threshold", 0.002))
            if self._diff_frac(small, self._prev) > thr:
                self._stable_since = now
                if self._change_start is None:
                    self._change_start = now
            self._prev = small
            changed = self._diff_frac(small, self._ref) > thr
            gap = (now - self._last_snap) * 1000
            settled = (now - self._stable_since) * 1000 >= float(self.cfg.get("settle_ms", 1200))
            if settled:
                self._change_start = None
            p = self._pending
            if p and (now - p["at"]) * 1000 >= float(self.cfg.get("confirm_ms", 2000)):
                if self._diff_frac(small, p["small"]) <= thr:
                    self.confirm_meta(p["meta"], "stable")
                else:
                    self._pending = None
            max_gap = float(self.cfg.get("max_gap_s", 60) or 0)
            max_cont = float((self.cfg.get("follow_max_continuous_s", 9) if self.follow_until
                              else self.cfg.get("max_continuous_s", 20)) or 0)
            min_gap = float(self.cfg.get("min_gap_ms", 1500))
            trig = None
            if changed and settled and gap >= min_gap:
                trig = "change"
            elif (max_cont and self._change_start is not None and
                  now - self._change_start >= max_cont and gap >= min_gap):
                trig = "checkin"
                self._change_start = now
            elif max_gap and gap >= max_gap * 1000:
                trig = "max_gap"
            if trig:
                self._trigger = trig
                try:
                    if self.capture_once() is not False and getattr(self, "tab", None):
                        self.tab.pulse()
                finally:
                    self._trigger = "interval"
                self._ref, self._last_snap = small, now
                self._pending = ({"meta": self.meta, "small": small, "at": now}
                                 if self.meta and self.meta.get("tentative") else None)
                if trig == "checkin" and self.meta:
                    self.webhook_snapshot(self.meta)
        except Exception as e:
            print("Change check failed:", e)
        finally:
            self._change_busy = False

    def capture_loop(self):
        if (not self.paused and not self.edit and not self.off and not self.privacy
                and not self.on_change_active()):
            try:
                if self.capture_once() is not False and getattr(self, "tab", None):
                    self.tab.pulse()
            except Exception as e:
                print("Capture failed:", e)
        ms = max(250, int(float(self.cfg["interval_seconds"]) * 1000))
        self._cap_job = self.root.after(ms, self.capture_loop)

    def quit(self):
        if getattr(self, "_quitting", False):
            return
        self._quitting = True
        self._stop_change_worker()
        steps = []
        if self.follow_until:
            self._log_follow_event("follow_stop", reason="app quit")
        if self.secure() and self.cfg.get("secure_wipe_on_quit", True):
            def wipe():
                n = wipe_history(self.cfg["output_dir"],
                                 sessions=bool(self.cfg.get("secure_wipe_sessions", False)))
                print(f"Secure mode: wiped {n} file(s) on quit")
            steps.append(wipe)
        steps += [self.remember_geometry, self.hotkeys.stop]
        if self.tray:
            steps.append(self.tray.stop)
        for fn in steps:                     # one failing step must not block the exit
            try:
                fn()
            except Exception as e:
                print("Quit step failed:", e)
        print("Quit")
        self.root.destroy()

    def run(self):
        m = hotkey_map(self.cfg)
        print(f"{self.display_name()} running (ShisaPoint.exe). " + " | ".join(
            f"{m[a]} {a}" for a, _, _ in HOTKEY_ACTIONS if m.get(a)))
        print("Output:", self.cfg["output_dir"])
        self.run_prune()                    # apply retention at startup
        self.root.mainloop()


def main():
    if not IS_WIN:
        print(APP_NAME + " only runs on Windows.")
        return 1
    set_dpi_awareness()
    setup_logging(load_config()["output_dir"])
    try:
        set_dark_menus(not windows_light_theme())   # app mode before any window exists
        ShisaPoint().run()
    except Exception:
        import traceback
        traceback.print_exc()
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
