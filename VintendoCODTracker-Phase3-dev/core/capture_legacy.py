"""Independent local scoreboard capture and Discord uploader.

This tool captures only a user-selected monitor region. It does not read the
game process, game files, CODMunity files, or CODMunity's network traffic.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import re
import ssl
import threading
import time
from datetime import datetime, timezone
from tkinter import messagebox, ttk
import tkinter as tk
import urllib.error
import urllib.request

try:
    import dxcam
    from PIL import Image, ImageTk
    from rapidocr_onnxruntime import RapidOCR
except ImportError as exc:
    raise SystemExit(
        "Dependencies are missing. Start the app with Launch-Scoreboard-Capture.ps1."
    ) from exc


APP_NAME = "Vintendo Scoreboard Capture"
APP_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
SETTINGS_PATH = APP_DATA / "settings.json"
STATE_PATH = APP_DATA / "posted-events.json"
CAPTURE_DIR = APP_DATA / "captures"
POLL_SECONDS = 1.0
STABLE_FRAMES = 3
MAX_DISCORD_IMAGE_BYTES = 20 * 1024 * 1024


def protect_text(value: str) -> str:
    """Encrypt a user-entered webhook for this Windows account with DPAPI."""
    if not value:
        return ""

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    raw = value.encode("utf-8")
    source = ctypes.create_string_buffer(raw)
    source_blob = DataBlob(len(raw), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
    target_blob = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_wchar_p,
                                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_ulong, ctypes.POINTER(DataBlob)]
    crypt32.CryptProtectData.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    ok = crypt32.CryptProtectData(
        ctypes.byref(source_blob), "Vintendo Scoreboard Capture", None, None, None, 0,
        ctypes.byref(target_blob),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "Windows could not protect the webhook setting")
    try:
        encrypted = ctypes.string_at(target_blob.pbData, target_blob.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        kernel32.LocalFree(target_blob.pbData)


def unprotect_text(value: str) -> str:
    if not value:
        return ""

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    raw = base64.b64decode(value)
    source = ctypes.create_string_buffer(raw)
    source_blob = DataBlob(len(raw), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
    target_blob = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.c_ulong, ctypes.POINTER(DataBlob)]
    crypt32.CryptUnprotectData.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(source_blob), None, None, None, None, 0, ctypes.byref(target_blob)
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "Windows could not unlock the saved webhook")
    try:
        return ctypes.string_at(target_blob.pbData, target_blob.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(target_blob.pbData)


def get_outputs() -> list[dict]:
    """Return display/output identifiers and dimensions reported by DXcam."""
    info = dxcam.output_info()
    outputs = []
    for line in str(info).splitlines():
        match = re.search(
            r"Device\[(\d+)\]\s+Output\[(\d+)\]:\s+Res:\((\d+),\s*(\d+)\).*?Primary:(True|False)",
            line,
        )
        if match:
            device, output, width, height, primary = match.groups()
            outputs.append({
                "device": int(device), "output": int(output),
                "width": int(width), "height": int(height),
                "primary": primary == "True",
                "label": f"Display {output} · {width}×{height}" + (" · primary" if primary == "True" else ""),
            })
    return outputs


def group_ocr_lines(result) -> tuple[str, list[str], list[tuple]]:
    """Turn RapidOCR boxes into readable lines, preserving box coordinates."""
    if not result:
        return "", [], []
    items = []
    heights = []
    for row in result:
        try:
            box, text, confidence = row
            text = str(text).strip()
            if not text:
                continue
            ys = [float(point[1]) for point in box]
            xs = [float(point[0]) for point in box]
            top, bottom = min(ys), max(ys)
            left, right = min(xs), max(xs)
            heights.append(max(1.0, bottom - top))
            items.append({"text": text, "score": float(confidence), "x": left,
                          "y": (top + bottom) / 2, "box": (left, top, right, bottom)})
        except (TypeError, ValueError, IndexError):
            continue
    if not items:
        return "", [], []
    tolerance = max(9.0, (sum(heights) / max(1, len(heights))) * 0.72)
    groups: list[list[dict]] = []
    for item in sorted(items, key=lambda v: (v["y"], v["x"])):
        target = next((g for g in reversed(groups) if abs(g[0]["y"] - item["y"]) <= tolerance), None)
        if target is None:
            groups.append([item])
        else:
            target.append(item)
            target[0]["y"] = sum(v["y"] for v in target) / len(target)
    groups.sort(key=lambda g: g[0]["y"])
    lines = ["  ".join(v["text"] for v in sorted(g, key=lambda v: v["x"])) for g in groups]
    boxes = [v["box"] for v in items]
    return "\n".join(lines), lines, boxes


def looks_like_scoreboard(lines: list[str]) -> bool:
    """Conservative gate: require several stat headings and four numeric rows."""
    text = " ".join(lines).casefold()
    headings = ("kill", "damage", "assist", "score", "elimination", "revive", "placement")
    heading_hits = sum(1 for word in headings if word in text)
    numeric_rows = sum(1 for line in lines if re.search(r"\d", line))
    return heading_hits >= 2 and numeric_rows >= 4 and len(lines) >= 5


def image_bytes(frame, fmt="JPEG") -> bytes:
    image = Image.fromarray(frame, mode="RGB")
    stream = io.BytesIO()
    if fmt == "JPEG":
        image.save(stream, format="JPEG", quality=91, optimize=True)
    else:
        image.save(stream, format=fmt)
    return stream.getvalue()


def send_discord(webhook: str, image_data: bytes, text: str, game: str) -> None:
    if not webhook.startswith(("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")):
        raise ValueError("Enter a valid Discord webhook URL in Settings.")
    if len(image_data) > MAX_DISCORD_IMAGE_BYTES:
        raise ValueError("The scoreboard image exceeds the 20 MB safety limit.")
    boundary = "----VintendoScoreboard" + os.urandom(12).hex()
    caption = f"**{game} scoreboard**\n```text\n{text[:1500]}\n```"
    payload = json.dumps({"content": caption, "allowed_mentions": {"parse": []}}).encode("utf-8")
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
        "Content-Type: application/json\r\n\r\n"
    ).encode() + payload + b"\r\n"
    body += (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; "
        "filename=\"scoreboard.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n"
    ).encode() + image_data + b"\r\n" + f"--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        webhook, body,
        {"Content-Type": f"multipart/form-data; boundary={boundary}",
         "User-Agent": "VintendoScoreboardCapture/0.1"},
        method="POST",
    )
    last_error = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status not in (200, 204):
                    raise RuntimeError(f"Discord returned HTTP {response.status}.")
                return
        except urllib.error.HTTPError as exc:
            last_error = f"Discord HTTP {exc.code}"
            if exc.code == 429:
                try:
                    delay = min(60.0, max(1.0, float(exc.headers.get("Retry-After", "1"))))
                except (TypeError, ValueError):
                    delay = 1.0
            elif exc.code >= 500:
                delay = min(30.0, 2.0 ** attempt)
            else:
                raise RuntimeError(last_error) from None
        except (urllib.error.URLError, TimeoutError, OSError, ssl.SSLError):
            # Never surface the exception text: it can contain the webhook URL.
            last_error = "network/TLS error"
            delay = min(30.0, 2.0 ** attempt)
        if attempt < 4:
            time.sleep(delay)
    raise RuntimeError(f"Discord delivery failed after five attempts ({last_error}).")


class RegionPicker(tk.Toplevel):
    def __init__(self, parent, image: Image.Image, on_select):
        super().__init__(parent)
        self.title("Select the full scoreboard region")
        self.transient(parent)
        self.grab_set()
        self.on_select = on_select
        self.source_size = image.size
        preview = image.copy()
        preview.thumbnail((1100, 650), Image.Resampling.LANCZOS)
        self.preview_size = preview.size
        self.scale_x = self.source_size[0] / self.preview_size[0]
        self.scale_y = self.source_size[1] / self.preview_size[1]
        self.photo = ImageTk.PhotoImage(preview)
        ttk.Label(self, text="Drag around all four player rows and the column headings.").pack(padx=10, pady=8)
        self.canvas = tk.Canvas(self, width=preview.width, height=preview.height, cursor="crosshair")
        self.canvas.pack(padx=10, pady=4)
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.start = None
        self.rect = None
        self.selection = None
        self.canvas.bind("<ButtonPress-1>", self.begin)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.finish)
        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=10, pady=8)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(buttons, text="Use this region", command=self.accept).pack(side="right", padx=4)

    def begin(self, event):
        self.start = (event.x, event.y)
        if self.rect:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(event.x, event.y, event.x, event.y,
                                                  outline="#42e8a4", width=3)

    def drag(self, event):
        if self.start and self.rect:
            self.canvas.coords(self.rect, self.start[0], self.start[1], event.x, event.y)

    def finish(self, event):
        if not self.start:
            return
        x1, x2 = sorted((self.start[0], event.x))
        y1, y2 = sorted((self.start[1], event.y))
        self.selection = (
            max(0, int(x1 * self.scale_x)), max(0, int(y1 * self.scale_y)),
            min(self.source_size[0], int(x2 * self.scale_x)),
            min(self.source_size[1], int(y2 * self.scale_y)),
        )

    def accept(self):
        if not self.selection or self.selection[2] - self.selection[0] < 80 or self.selection[3] - self.selection[1] < 60:
            messagebox.showinfo("Select a larger region", "Drag a rectangle around the complete scoreboard.", parent=self)
            return
        self.on_select(self.selection)
        self.destroy()


class ScoreboardApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1040x800")
        self.minsize(850, 650)
        self.outputs = []
        self.camera = None
        self.camera_lock = threading.RLock()
        self.monitor_game = ""
        self.monitor_roi = None
        self.roi = None
        self.ocr = None
        self.monitor_thread = None
        self.stop_event = threading.Event()
        self.events = queue.Queue()
        self.last_frame = None
        self.last_text = ""
        self.last_lines = []
        self._stable_key = ""
        self._stable_count = 0
        self._empty_count = 0
        self._armed = False
        self._seen = self.load_seen()
        self.settings = self.load_settings()
        self.output_var = tk.StringVar()
        self.game_var = tk.StringVar(value=self.settings.get("game", "Warzone Resurgence"))
        self.status_var = tk.StringVar(value="Select a display, capture a sample, and mark the full scoreboard.")
        self.roi_var = tk.StringVar(value="Scoreboard region: not selected")
        self.webhook_var = tk.StringVar(value="")
        self.auto_var = tk.BooleanVar(value=False)
        self.save_image_var = tk.BooleanVar(value=self.settings.get("save_images", False))
        self._build_ui()
        self.refresh_outputs()
        self.after(120, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self.close)
        if self.settings.get("protected_webhook"):
            try:
                self.webhook_var.set(unprotect_text(self.settings["protected_webhook"]))
            except Exception:
                self.status_var.set("Saved webhook could not be unlocked; paste it again in Settings.")
        self.roi = self.settings.get("roi")
        if self.roi:
            self.roi_var.set(f"Scoreboard region: {self.roi[2] - self.roi[0]}×{self.roi[3] - self.roi[1]} selected")

    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="Independent screen capture · no CODMunity files or game memory",
                  font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 10))
        capture = ttk.LabelFrame(root, text="1 · Choose the screen region", padding=10)
        capture.pack(fill="x", pady=5)
        row = ttk.Frame(capture)
        row.pack(fill="x")
        ttk.Label(row, text="Display").pack(side="left")
        self.output_combo = ttk.Combobox(row, textvariable=self.output_var, state="readonly", width=42)
        self.output_combo.pack(side="left", padx=8)
        ttk.Button(row, text="Refresh displays", command=self.refresh_outputs).pack(side="left")
        ttk.Button(row, text="Capture sample & select board", command=self.select_region).pack(side="right")
        ttk.Label(capture, textvariable=self.roi_var).pack(anchor="w", pady=(8, 0))

        settings = ttk.LabelFrame(root, text="2 · Discord and capture settings", padding=10)
        settings.pack(fill="x", pady=5)
        ttk.Label(settings, text="Game").grid(row=0, column=0, sticky="w")
        ttk.Combobox(settings, textvariable=self.game_var, state="readonly",
                     values=("Warzone Resurgence", "Warzone Battle Royale", "MW4 Multiplayer"), width=26).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(settings, text="Webhook").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(settings, textvariable=self.webhook_var, show="•", width=82).grid(row=1, column=1, sticky="ew", padx=8, pady=(8, 0))
        ttk.Button(settings, text="Save settings", command=self.save_settings).grid(row=1, column=2, padx=8, pady=(8, 0))
        settings.columnconfigure(1, weight=1)
        ttk.Checkbutton(settings, text="Save posted scoreboard images locally (7-day retention)",
                        variable=self.save_image_var, command=self.save_settings).grid(row=2, column=1, sticky="w", padx=8, pady=(8, 0))

        actions = ttk.LabelFrame(root, text="3 · Preview or watch", padding=10)
        actions.pack(fill="x", pady=5)
        ttk.Button(actions, text="Capture & OCR once", command=self.capture_once).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Post current scoreboard", command=self.post_current).pack(side="left", padx=8)
        ttk.Checkbutton(actions, text="Auto-post a stable scoreboard once per appearance", variable=self.auto_var,
                        command=self.toggle_monitor).pack(side="left", padx=10)
        ttk.Label(actions, text="Auto-post is off each time the app starts.", foreground="#666").pack(side="left")

        ttk.Label(root, textvariable=self.status_var, wraplength=980).pack(anchor="w", pady=(8, 4))
        body = ttk.Panedwindow(root, orient="horizontal")
        body.pack(fill="both", expand=True, pady=(4, 0))
        preview_frame = ttk.LabelFrame(body, text="Captured scoreboard crop (kept in memory unless saving is enabled)", padding=6)
        ocr_frame = ttk.LabelFrame(body, text="Local OCR text", padding=6)
        body.add(preview_frame, weight=3)
        body.add(ocr_frame, weight=2)
        self.preview = ttk.Label(preview_frame, text="No screenshot captured")
        self.preview.pack(fill="both", expand=True)
        self.text = tk.Text(ocr_frame, wrap="word", font=("Consolas", 10), height=18)
        self.text.pack(fill="both", expand=True)
        self.text.insert("1.0", "OCR runs on this PC. Images are sent only when you post or enable auto-post.")

    def load_settings(self):
        try:
            value = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def load_seen(self):
        try:
            obj = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            return set(obj.get("fingerprints", []))
        except (OSError, ValueError, AttributeError):
            return set()

    def save_seen(self):
        APP_DATA.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"fingerprints": list(self._seen)[-500:]}, indent=2), encoding="utf-8")
        os.replace(tmp, STATE_PATH)

    def refresh_outputs(self):
        try:
            self.outputs = get_outputs()
            self.output_combo["values"] = [x["label"] for x in self.outputs]
            saved = int(self.settings.get("output", 0))
            index = next((i for i, x in enumerate(self.outputs) if x["output"] == saved), 0)
            if self.outputs:
                self.output_combo.current(index)
                self.status_var.set(f"Found {len(self.outputs)} display output(s). Capture remains off until you start it.")
            else:
                self.status_var.set("No displays were reported by the screen-capture driver.")
        except Exception:
            self.status_var.set("Could not enumerate displays. Confirm Windows desktop access and try Refresh displays.")

    def selected_output(self):
        index = self.output_combo.current()
        if index < 0 or index >= len(self.outputs):
            raise RuntimeError("Select a display first.")
        return self.outputs[index]

    def ensure_camera(self):
        output = self.selected_output()
        selected_id = output["output"]
        if self.camera is not None and self.settings.get("active_output") == selected_id:
            return self.camera
        if self.camera is not None:
            try:
                self.camera.release()
            except Exception:
                pass
        self.camera = dxcam.create(device_idx=output["device"], output_idx=selected_id,
                                   output_color="RGB", backend="dxgi", processor_backend="cv2",
                                   max_buffer_len=2)
        self.settings["active_output"] = selected_id
        return self.camera

    def capture_full(self):
        cam = self.ensure_camera()
        with self.camera_lock:
            frame = cam.grab(new_frame_only=False)
        if frame is None:
            raise RuntimeError("No screen frame was available. Make sure the display is awake and visible.")
        return Image.fromarray(frame, mode="RGB")

    def select_region(self):
        try:
            frame = self.capture_full()
        except Exception:
            messagebox.showerror("Capture unavailable", "Windows did not provide a frame. Make sure the game is visible on the selected display and try again.")
            return

        def accepted(region):
            self.roi = list(region)
            self.settings["roi"] = self.roi
            self.settings["output"] = self.selected_output()["output"]
            self.roi_var.set(f"Scoreboard region: {region[2]-region[0]}×{region[3]-region[1]} selected")
            self.status_var.set("Region saved locally. Use Capture & OCR once to review the full board before enabling auto-post.")
            self.save_settings()

        RegionPicker(self, frame, accepted)
        del frame

    def capture_roi_image(self):
        if not self.roi:
            raise RuntimeError("Select the complete scoreboard region first.")
        cam = self.ensure_camera()
        with self.camera_lock:
            frame = cam.grab(region=tuple(self.roi), new_frame_only=False)
        if frame is None:
            raise RuntimeError("No fresh display frame was available.")
        return frame.copy()

    def init_ocr(self):
        if self.ocr is None:
            self.status_var.set("Loading local OCR models… the first launch may download model files.")
            self.update_idletasks()
            self.ocr = RapidOCR()
        return self.ocr

    def recognize(self, frame):
        result, _elapsed = self.init_ocr()(frame)
        text, lines, boxes = group_ocr_lines(result)
        return text, lines, boxes

    def capture_once(self):
        try:
            frame = self.capture_roi_image()
            text, lines, _boxes = self.recognize(frame)
        except Exception as exc:
            self.status_var.set(f"Capture/OCR failed: {type(exc).__name__}. Check display access and retry.")
            return
        self.last_frame = frame
        self.last_text = text
        self.last_lines = lines
        self.show_preview(frame)
        self.set_text(text or "No readable scoreboard text yet. Try selecting a tighter region or capture a sharper frame.")
        rows = sum(1 for line in lines if re.search(r"\d", line))
        self.status_var.set(f"Local OCR finished: {len(lines)} text lines, {rows} numeric lines. " +
                            ("Scoreboard-like layout detected." if looks_like_scoreboard(lines) else "Not enough evidence for auto-post yet."))

    def show_preview(self, frame):
        image = Image.fromarray(frame, mode="RGB")
        image.thumbnail((620, 500), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(image)
        self.preview.configure(image=photo, text="")
        self.preview.image = photo

    def set_text(self, content):
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)

    def save_settings(self):
        APP_DATA.mkdir(parents=True, exist_ok=True)
        webhook = self.webhook_var.get().strip()
        if webhook and not webhook.startswith(("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")):
            messagebox.showerror("Invalid webhook", "Enter a Discord webhook URL beginning with https://discord.com/api/webhooks/.")
            return
        self.settings.update({
            "output": self.selected_output()["output"] if self.outputs and self.output_combo.current() >= 0 else 0,
            "game": self.game_var.get(), "roi": self.roi,
            "save_images": bool(self.save_image_var.get()),
            "protected_webhook": protect_text(webhook),
        })
        tmp = SETTINGS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")
        os.replace(tmp, SETTINGS_PATH)
        self.status_var.set("Settings saved. The webhook is encrypted for this Windows account.")

    def post_current(self):
        if self.last_frame is None or not self.last_text:
            messagebox.showinfo("Capture a scoreboard first", "Use Capture & OCR once, then review the image and text before posting.")
            return
        webhook = self.webhook_var.get().strip()
        if not webhook:
            messagebox.showinfo("Webhook needed", "Paste your Discord webhook and save settings first.")
            return
        if not messagebox.askyesno("Post this scoreboard?", "This sends the selected scoreboard image and OCR text to your Discord webhook."):
            return
        threading.Thread(
            target=self._send,
            args=(self.last_frame.copy(), self.last_text, webhook, self.game_var.get(),
                  self.save_image_var.get(), True),
            daemon=True,
        ).start()
        self.status_var.set("Sending the reviewed scoreboard to Discord…")

    @staticmethod
    def _fingerprint(game, text):
        compact = re.sub(r"\s+", " ", text.casefold()).strip()
        return hashlib.sha256((game + "|" + compact).encode("utf-8")).hexdigest()

    def _send(self, frame, text, webhook, game, save_image, manual=False):
        if not webhook:
            self.events.put(("status", "Auto-post paused: add a Discord webhook in Settings."))
            return
        fingerprint = self._fingerprint(game, text)
        if fingerprint in self._seen and not manual:
            self.events.put(("status", "This scoreboard was already posted; duplicate suppressed."))
            return
        try:
            data = image_bytes(frame)
            send_discord(webhook, data, text, game)
            self._seen.add(fingerprint)
            self.save_seen()
            if save_image:
                self.save_capture(frame, fingerprint)
            self.events.put(("status", "Scoreboard sent to Discord."))
        except Exception as exc:
            # Only show error type/message after removing anything resembling the webhook URL.
            safe = str(exc).replace(webhook, "[webhook redacted]")[:180]
            if not manual:
                # Leave it eligible for the next stable observation after a transient failure.
                self.events.put(("retry-eligible", fingerprint))
            self.events.put(("status", f"Discord post failed: {safe}"))

    def save_capture(self, frame, fingerprint):
        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = CAPTURE_DIR / f"{stamp}-{fingerprint[:10]}.jpg"
        Image.fromarray(frame, mode="RGB").save(path, format="JPEG", quality=92, optimize=True)
        cutoff = time.time() - (7 * 86400)
        for old in CAPTURE_DIR.glob("*.jpg"):
            try:
                if old.stat().st_mtime < cutoff:
                    old.unlink()
            except OSError:
                pass

    def toggle_monitor(self):
        if self.auto_var.get():
            if not self.roi:
                self.auto_var.set(False)
                messagebox.showinfo("Select the board first", "Capture a sample and select the full scoreboard region before enabling auto-post.")
                return
            if not self.webhook_var.get().strip():
                self.auto_var.set(False)
                messagebox.showinfo("Webhook needed", "Paste and save the Discord webhook before enabling auto-post.")
                return
            if self.monitor_thread and self.monitor_thread.is_alive():
                return
            try:
                self.ensure_camera()
                self.init_ocr()
            except Exception:
                self.auto_var.set(False)
                messagebox.showerror("Capture/OCR unavailable", "Could not start the screen capture or local OCR engine. Check display access and try again.")
                return
            self.stop_event.clear()
            self._armed = False
            self._empty_count = 0
            self.monitor_game = self.game_var.get()
            self.monitor_roi = tuple(self.roi)
            self.monitor_thread = threading.Thread(target=self._watch_loop, daemon=True)
            self.monitor_thread.start()
            self.status_var.set("Auto-post is on. Existing scoreboard is ignored until it disappears; new stable appearances can post.")
        else:
            self.stop_event.set()
            self.status_var.set("Auto-post is off.")

    def _watch_loop(self):
        try:
            cam = self.camera
            ocr = self.ocr
            roi = self.monitor_roi
            game = self.monitor_game
            if cam is None or ocr is None:
                raise RuntimeError("Capture is not initialized")
            stable_key = ""
            stable_count = 0
            empty_count = 0
            armed = False
            while not self.stop_event.wait(POLL_SECONDS):
                with self.camera_lock:
                    frame = cam.grab(region=roi, new_frame_only=False)
                if frame is None:
                    continue
                frame = frame.copy()
                result, _elapsed = ocr(frame)
                text, lines, _boxes = group_ocr_lines(result)
                signal = looks_like_scoreboard(lines)
                if not signal:
                    empty_count += 1
                    stable_key = ""
                    stable_count = 0
                    if empty_count >= 3:
                        armed = True
                    self.events.put(("preview", frame, text, False))
                    continue
                empty_count = 0
                key = self._fingerprint(game, text)
                stable_count = stable_count + 1 if key == stable_key else 1
                stable_key = key
                self.events.put(("preview", frame, text, True))
                if stable_count >= STABLE_FRAMES and armed:
                    armed = False
                    self.events.put(("stable", frame, text, game))
                    stable_count = 0
        except Exception as exc:
            self.events.put(("status", f"Auto capture stopped: {type(exc).__name__}. Check display access and region settings."))
            self.events.put(("disable-auto",))

    def _drain_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "status":
                    self.status_var.set(event[1])
                elif kind == "disable-auto":
                    self.auto_var.set(False)
                    self.stop_event.set()
                elif kind == "retry-eligible":
                    self._seen.discard(event[1])
                elif kind == "preview":
                    _, frame, text, signal = event
                    self.last_frame, self.last_text = frame, text
                    self.last_lines = text.splitlines()
                    self.show_preview(frame)
                    self.set_text(text)
                    if signal:
                        self.status_var.set("Scoreboard-like region detected; waiting for a stable frame.")
                elif kind == "stable":
                    _, frame, text, game = event
                    threading.Thread(
                        target=self._send,
                        args=(frame, text, self.webhook_var.get().strip(), game,
                              self.save_image_var.get()),
                        daemon=True,
                    ).start()
        except queue.Empty:
            pass
        self.after(120, self._drain_events)

    def close(self):
        self.stop_event.set()
        if self.camera is not None:
            try:
                with self.camera_lock:
                    self.camera.release()
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    APP_DATA.mkdir(parents=True, exist_ok=True)
    ScoreboardApp().mainloop()
