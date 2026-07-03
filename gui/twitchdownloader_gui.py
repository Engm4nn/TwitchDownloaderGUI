#!/usr/bin/env python3
"""
TwitchDownloader GUI.

A zero-dependency local web GUI wrapping TwitchDownloaderCLI.
Run:  python3 twitchdownloader_gui.py   (or double-click "TwitchDownloader GUI.command")

Serves on http://127.0.0.1:<port> (localhost only) and opens your browser.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
CLI_NAME = "TwitchDownloaderCLI"


def find_cli():
    """Locate TwitchDownloaderCLI next to the script, one level up, or on PATH."""
    for candidate in (os.path.join(TOOL_DIR, CLI_NAME),
                      os.path.join(os.path.dirname(TOOL_DIR), CLI_NAME),
                      shutil.which(CLI_NAME)):
        if candidate and os.path.exists(candidate):
            return candidate
    return os.path.join(TOOL_DIR, CLI_NAME)  # default location for error message


CLI_PATH = find_cli()
DEFAULT_OUTPUT_DIR = os.path.expanduser("~/Downloads")
PORT_RANGE = range(5959, 5970)

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PERCENT_RE = re.compile(r"(\d{1,3})%")


def find_ffmpeg():
    for candidate in (shutil.which("ffmpeg"),
                      "/opt/homebrew/bin/ffmpeg",
                      "/usr/local/bin/ffmpeg"):
        if candidate and os.path.exists(candidate):
            return candidate
    return None


FFMPEG_PATH = find_ffmpeg()

# The CLI's built-in bundled font, and the default for chat renders.
EMBEDDED_FONT = "Inter Embedded"


def list_fonts():
    """Return a sorted list of installed font family names, with the CLI's
    bundled 'Inter Embedded' always first as the default choice."""
    families = set()
    try:
        # `atsutil fonts -list` is slow; parsing font filenames is fast and works.
        for root in ("/System/Library/Fonts", "/Library/Fonts",
                     os.path.expanduser("~/Library/Fonts")):
            if not os.path.isdir(root):
                continue
            for entry in os.listdir(root):
                stem, ext = os.path.splitext(entry)
                if ext.lower() in (".ttf", ".ttc", ".otf"):
                    # "Arial Bold.ttf" -> "Arial"; strip common style suffixes.
                    name = re.sub(
                        r"[ _-]*(Bold|Italic|Oblique|Regular|Light|Medium|"
                        r"SemiBold|Thin|Black|Condensed|Heavy)+$",
                        "", stem, flags=re.IGNORECASE).strip()
                    if name:
                        families.add(name)
    except OSError:
        pass
    return [EMBEDDED_FONT] + sorted(families, key=str.lower)


FONTS = list_fonts()


class Job:
    """Runs one CLI invocation and captures its output for polling."""

    def __init__(self, argv, label):
        self.argv = argv
        self.label = label
        self.proc = None
        self.log = deque(maxlen=2000)
        self.cur_line = ""
        self.status_line = ""
        self.percent = None
        self.exit_code = None
        self.lock = threading.Lock()

    def start(self):
        # Force an English locale: under e.g. da_DK the .NET CLI expects commas
        # as decimal separators and rejects values like "29.97".
        env = os.environ.copy()
        env["LC_ALL"] = "en_US.UTF-8"
        env["LANG"] = "en_US.UTF-8"
        self.proc = subprocess.Popen(
            self.argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=TOOL_DIR,
            env=env,
            start_new_session=True,
        )
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        stream = self.proc.stdout
        while True:
            chunk = stream.read1(4096)
            if not chunk:
                break
            text = ANSI_RE.sub("", chunk.decode("utf-8", errors="replace"))
            with self.lock:
                for ch in text:
                    if ch == "\n":
                        if self.cur_line.strip():
                            self.log.append(self.cur_line)
                            self.status_line = self.cur_line
                        self.cur_line = ""
                    elif ch == "\r":
                        if self.cur_line.strip():
                            self.status_line = self.cur_line
                            self._scan_percent(self.cur_line)
                        self.cur_line = ""
                    else:
                        self.cur_line += ch
                if self.cur_line.strip():
                    self.status_line = self.cur_line
                    self._scan_percent(self.cur_line)
        with self.lock:
            if self.cur_line.strip():
                self.log.append(self.cur_line)
                self.status_line = self.cur_line
                self.cur_line = ""
        self.exit_code = self.proc.wait()

    def _scan_percent(self, line):
        m = None
        for m in PERCENT_RE.finditer(line):
            pass
        if m:
            value = int(m.group(1))
            if 0 <= value <= 100:
                self.percent = value

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def cancel(self):
        if not self.running:
            return
        pgid = os.getpgid(self.proc.pid)
        os.killpg(pgid, signal.SIGINT)

        def hard_kill():
            time.sleep(5)
            if self.running:
                os.killpg(pgid, signal.SIGTERM)

        threading.Thread(target=hard_kill, daemon=True).start()

    def snapshot(self):
        with self.lock:
            return {
                "running": self.running,
                "label": self.label,
                "cmd": " ".join(self.argv),
                "log": "\n".join(self.log),
                "status_line": self.status_line,
                "percent": self.percent,
                "exit_code": self.exit_code,
            }


current_job = None
job_lock = threading.Lock()
last_output_path = None


def sanitize_filename(name):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip()
    return name or "output"


def resolve_output(fields, allowed_exts, default_ext):
    out_dir = os.path.expanduser(fields.get("output_dir", "").strip() or DEFAULT_OUTPUT_DIR)
    filename = sanitize_filename(fields.get("filename", "").strip())
    ext = os.path.splitext(filename)[1].lower()
    if ext not in allowed_exts:
        filename += default_ext
    os.makedirs(out_dir, exist_ok=True)
    return os.path.join(out_dir, filename)


def build_command(mode, f):
    """Returns (argv, label, output_path_or_None). Raises ValueError on bad input."""
    argv = [CLI_PATH]
    output = None

    def require(key, msg):
        value = f.get(key, "").strip()
        if not value:
            raise ValueError(msg)
        return value

    def opt(flag, key):
        value = f.get(key, "").strip()
        if value:
            argv.extend([flag, value])

    def num(key, default, label, integer=False):
        """Normalize a numeric field: accept "29.97", "29,97", "60 fps" etc."""
        value = f.get(key, "").strip().lower().replace("fps", "").replace(",", ".").strip()
        if not value:
            return default
        pattern = r"\d+" if integer else r"\d+(\.\d+)?"
        if not re.fullmatch(pattern, value):
            kind = "a whole number" if integer else "a number"
            raise ValueError(f"{label} must be {kind}, got: {f.get(key)!r}")
        return value

    if mode == "videodownload":
        argv.append("videodownload")
        argv.extend(["-u", require("id", "VOD URL or ID is required")])
        output = resolve_output(f, {".mp4", ".m4a"}, ".mp4")
        argv.extend(["-o", output])
        opt("-q", "quality")
        opt("-b", "beginning")
        opt("-e", "ending")
        argv.extend(["-t", num("threads", "4", "Threads", integer=True)])
        opt("--oauth", "oauth")
        if FFMPEG_PATH:
            argv.extend(["--ffmpeg-path", FFMPEG_PATH])
        label = "VOD download"

    elif mode == "clipdownload":
        argv.append("clipdownload")
        argv.extend(["-u", require("id", "Clip URL or ID is required")])
        output = resolve_output(f, {".mp4"}, ".mp4")
        argv.extend(["-o", output])
        opt("-q", "quality")
        if FFMPEG_PATH:
            argv.extend(["--ffmpeg-path", FFMPEG_PATH])
        label = "Clip download"

    elif mode == "chatdownload":
        argv.append("chatdownload")
        argv.extend(["-u", require("id", "VOD/clip URL or ID is required")])
        fmt = f.get("format", "json")
        if fmt not in ("json", "html", "txt"):
            raise ValueError("Invalid chat format")
        output = resolve_output(f, {"." + fmt}, "." + fmt)
        argv.extend(["-o", output])
        if f.get("embed") and fmt == "json":
            argv.append("-E")
            # BTTV/FFZ/7TV embedding defaults ON in the CLI; disable when unchecked.
            for key, flag in (("bttv", "--bttv"), ("ffz", "--ffz"), ("stv", "--stv")):
                if f.get(key) is False:
                    argv.append(f"{flag}=false")
        opt("-b", "beginning")
        opt("-e", "ending")
        if fmt == "txt":
            argv.extend(["--timestamp-format", f.get("timestamp_format", "Relative")])
        label = "Chat download"

    elif mode == "chatrender":
        argv.append("chatrender")
        input_path = os.path.expanduser(require("input", "Input chat JSON path is required"))
        if not os.path.exists(input_path):
            raise ValueError(f"Input file not found: {input_path}")
        argv.extend(["-i", input_path])
        output = resolve_output(f, {".mp4", ".mov", ".webm", ".mkv"}, ".mp4")
        argv.extend(["-o", output])
        argv.extend(["-w", num("width", "350", "Width", integer=True)])
        argv.extend(["-h", num("height", "600", "Height", integer=True)])
        argv.extend(["--framerate", num("framerate", "30", "Framerate", integer=True)])
        argv.extend(["--font-size", num("font_size", "24", "Font size", integer=True)])
        font = f.get("font", "").strip() or EMBEDDED_FONT
        argv.extend(["--font", font])
        bg = f.get("background_color", "").strip()
        if bg:
            if not re.fullmatch(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{8})", bg):
                raise ValueError("Background color must be #RRGGBB or #AARRGGBB")
            argv.extend(["--background-color", bg])
        if f.get("outline"):
            argv.append("--outline")
        if f.get("timestamp"):
            argv.append("--timestamp")
        # Third-party emotes default ON in the CLI; only disable when unchecked.
        for key, flag in (("bttv", "--bttv"), ("ffz", "--ffz"), ("stv", "--stv")):
            if f.get(key) is False:
                argv.append(f"{flag}=false")
        if FFMPEG_PATH:
            argv.extend(["--ffmpeg-path", FFMPEG_PATH])
        label = "Chat render"

    elif mode == "info":
        argv.append("info")
        argv.extend(["-u", require("id", "VOD/clip URL or ID is required")])
        argv.extend(["-f", "Table"])
        opt("--oauth", "oauth")
        label = "Info"

    else:
        raise ValueError(f"Unknown mode: {mode}")

    argv.append("--banner=false")
    if mode != "info":
        argv.extend(["--collision", "Rename"])
    return argv, label, output


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep the terminal quiet

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        global current_job
        if self.path == "/":
            body = PAGE_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/status":
            with job_lock:
                job = current_job
            snap = job.snapshot() if job else {"running": False, "log": "", "status_line": "",
                                              "percent": None, "exit_code": None, "label": None}
            snap["last_output"] = last_output_path
            self._send_json(snap)
        elif self.path == "/api/defaults":
            self._send_json({
                "output_dir": DEFAULT_OUTPUT_DIR,
                "ffmpeg": FFMPEG_PATH,
                "cli": CLI_PATH,
                "fonts": FONTS,
            })
        else:
            self.send_error(404)

    def do_POST(self):
        global current_job, last_output_path
        if self.path == "/api/start":
            try:
                data = self._read_body()
                with job_lock:
                    if current_job and current_job.running:
                        self._send_json({"error": "A job is already running"}, 409)
                        return
                    argv, label, output = build_command(data.get("mode", ""), data.get("fields", {}))
                    job = Job(argv, label)
                    job.start()
                    current_job = job
                    last_output_path = output
                self._send_json({"ok": True})
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
            except Exception as e:
                self._send_json({"error": f"Failed to start: {e}"}, 500)
        elif self.path == "/api/cancel":
            with job_lock:
                if current_job:
                    current_job.cancel()
            self._send_json({"ok": True})
        elif self.path == "/api/reveal":
            path = last_output_path
            if path and os.path.exists(path):
                subprocess.Popen(["open", "-R", path])
            elif path:
                subprocess.Popen(["open", os.path.dirname(path)])
            self._send_json({"ok": True})
        else:
            self.send_error(404)


PAGE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>TwitchDownloader</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {
    --bg: #0e0e10; --panel: #18181b; --panel2: #1f1f23; --border: #2e2e35;
    --text: #efeff1; --muted: #9d9da8; --accent: #a970ff; --accent-dark: #772ce8;
    --ok: #3fb26b; --err: #eb4d4b;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }
  .wrap { max-width: 880px; margin: 0 auto; padding: 24px 20px 60px; }
  header { display: flex; align-items: baseline; gap: 12px; margin-bottom: 18px; }
  header h1 { font-size: 20px; margin: 0; }
  header .sub { color: var(--muted); font-size: 12px; }
  .tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); margin-bottom: 16px; flex-wrap: wrap; }
  .tabs button {
    background: none; border: none; color: var(--muted); padding: 10px 14px;
    font: inherit; cursor: pointer; border-bottom: 2px solid transparent;
  }
  .tabs button.active { color: var(--text); border-bottom-color: var(--accent); }
  .tabs button:hover { color: var(--text); }
  .pane { display: none; }
  .pane.active { display: block; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 18px; }
  .row { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
  .field { flex: 1; min-width: 140px; }
  .field.grow2 { flex: 2; min-width: 260px; }
  label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
  input[type=text], input[type=number], select {
    width: 100%; padding: 8px 10px; border-radius: 6px; border: 1px solid var(--border);
    background: var(--panel2); color: var(--text); font: inherit;
  }
  input[type=text]:focus, input[type=number]:focus, select:focus { outline: none; border-color: var(--accent); }
  .check { display: flex; align-items: center; gap: 7px; color: var(--text); font-size: 13px; padding-top: 20px; }
  .check input { accent-color: var(--accent); width: 15px; height: 15px; }
  .hint { font-size: 11px; color: var(--muted); margin-top: 3px; }
  .actions { display: flex; gap: 10px; align-items: center; margin-top: 6px; }
  .btn {
    padding: 9px 22px; border: none; border-radius: 6px; font: inherit; font-weight: 600;
    cursor: pointer; background: var(--panel2); color: var(--text); border: 1px solid var(--border);
  }
  .btn.primary { background: var(--accent-dark); border-color: var(--accent-dark); color: #fff; }
  .btn.primary:hover { background: var(--accent); }
  .btn:disabled { opacity: .45; cursor: default; }
  .btn.danger { background: none; border-color: var(--err); color: var(--err); }
  #status-card { margin-top: 20px; }
  #status-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
  #status-badge {
    font-size: 11px; font-weight: 700; letter-spacing: .5px; padding: 3px 9px; border-radius: 20px;
    background: var(--panel2); color: var(--muted); text-transform: uppercase;
  }
  #status-badge.running { background: var(--accent-dark); color: #fff; }
  #status-badge.done { background: var(--ok); color: #fff; }
  #status-badge.error { background: var(--err); color: #fff; }
  #status-line { color: var(--muted); font-size: 13px; flex: 1; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap; font-family: ui-monospace, Menlo, monospace; }
  #bar-track { height: 6px; background: var(--panel2); border-radius: 3px; overflow: hidden; margin-bottom: 12px; display: none; }
  #bar-fill { height: 100%; width: 0; background: var(--accent); transition: width .3s; }
  #log {
    background: #0a0a0c; border: 1px solid var(--border); border-radius: 6px;
    padding: 12px; height: 260px; overflow-y: auto; white-space: pre-wrap;
    font: 12px/1.6 ui-monospace, Menlo, monospace; color: #c8c8d0;
  }
  #log:empty::before { content: "Output will appear here."; color: #55555f; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>TwitchDownloader</h1>
    <span class="sub">a simple GUI for TwitchDownloaderCLI</span>
  </header>

  <div class="tabs">
    <button data-tab="videodownload" class="active">VOD</button>
    <button data-tab="clipdownload">Clip</button>
    <button data-tab="chatdownload">Chat</button>
    <button data-tab="chatrender">Chat Render</button>
    <button data-tab="info">Info</button>
  </div>

  <!-- VOD -->
  <div class="pane active card" id="pane-videodownload">
    <div class="row">
      <div class="field grow2">
        <label>VOD URL or ID</label>
        <input type="text" id="vod-id" placeholder="https://www.twitch.tv/videos/612942303">
      </div>
      <div class="field">
        <label>Quality</label>
        <input type="text" id="vod-quality" placeholder="best (e.g. 1080p60, 720p)">
      </div>
    </div>
    <div class="row">
      <div class="field">
        <label>Trim start (optional)</label>
        <input type="text" id="vod-beginning" placeholder="hh:mm:ss">
      </div>
      <div class="field">
        <label>Trim end (optional)</label>
        <input type="text" id="vod-ending" placeholder="hh:mm:ss">
      </div>
      <div class="field">
        <label>OAuth token (sub-only VODs)</label>
        <input type="text" id="vod-oauth" placeholder="optional">
      </div>
    </div>
    <div class="row">
      <div class="field grow2">
        <label>Output folder</label>
        <input type="text" id="vod-output_dir" class="outdir">
      </div>
      <div class="field">
        <label>Filename</label>
        <input type="text" id="vod-filename" placeholder="vod.mp4">
        <div class="hint">.mp4 = video, .m4a = audio only</div>
      </div>
    </div>
    <div class="actions">
      <button class="btn primary" onclick="start('videodownload')">Download VOD</button>
    </div>
  </div>

  <!-- Clip -->
  <div class="pane card" id="pane-clipdownload">
    <div class="row">
      <div class="field grow2">
        <label>Clip URL or ID</label>
        <input type="text" id="clip-id" placeholder="https://www.twitch.tv/streamer/clip/…">
      </div>
      <div class="field">
        <label>Quality</label>
        <input type="text" id="clip-quality" placeholder="best">
      </div>
    </div>
    <div class="row">
      <div class="field grow2">
        <label>Output folder</label>
        <input type="text" id="clip-output_dir" class="outdir">
      </div>
      <div class="field">
        <label>Filename</label>
        <input type="text" id="clip-filename" placeholder="clip.mp4">
      </div>
    </div>
    <div class="actions">
      <button class="btn primary" onclick="start('clipdownload')">Download Clip</button>
    </div>
  </div>

  <!-- Chat -->
  <div class="pane card" id="pane-chatdownload">
    <div class="row">
      <div class="field grow2">
        <label>VOD or Clip URL / ID</label>
        <input type="text" id="chat-id" placeholder="https://www.twitch.tv/videos/612942303">
      </div>
      <div class="field">
        <label>Format</label>
        <select id="chat-format">
          <option value="json">JSON (for rendering)</option>
          <option value="html">HTML</option>
          <option value="txt">Plain text</option>
        </select>
      </div>
      <div class="check">
        <input type="checkbox" id="chat-embed" checked>
        <label for="chat-embed" style="margin:0;color:var(--text)">Embed emotes/badges (JSON)</label>
      </div>
    </div>
    <div class="row" id="chat-embed-row">
      <div class="field" style="min-width:100%">
        <label>Emotes to embed (needs "Embed emotes/badges")</label>
        <div style="display:flex;gap:20px;flex-wrap:wrap">
          <div class="check" style="padding-top:0">
            <input type="checkbox" id="chat-bttv" checked>
            <label for="chat-bttv" style="margin:0;color:var(--text)">BTTV</label>
          </div>
          <div class="check" style="padding-top:0">
            <input type="checkbox" id="chat-ffz" checked>
            <label for="chat-ffz" style="margin:0;color:var(--text)">FFZ</label>
          </div>
          <div class="check" style="padding-top:0">
            <input type="checkbox" id="chat-stv" checked>
            <label for="chat-stv" style="margin:0;color:var(--text)">7TV</label>
          </div>
        </div>
        <div class="hint">Embedding bakes emotes into the JSON so they always render, even offline</div>
      </div>
    </div>
    <div class="row">
      <div class="field">
        <label>Trim start (optional)</label>
        <input type="text" id="chat-beginning" placeholder="hh:mm:ss">
      </div>
      <div class="field">
        <label>Trim end (optional)</label>
        <input type="text" id="chat-ending" placeholder="hh:mm:ss">
      </div>
    </div>
    <div class="row">
      <div class="field grow2">
        <label>Output folder</label>
        <input type="text" id="chat-output_dir" class="outdir">
      </div>
      <div class="field">
        <label>Filename</label>
        <input type="text" id="chat-filename" placeholder="chat.json">
      </div>
    </div>
    <div class="actions">
      <button class="btn primary" onclick="start('chatdownload')">Download Chat</button>
    </div>
  </div>

  <!-- Chat render -->
  <div class="pane card" id="pane-chatrender">
    <div class="row">
      <div class="field grow2">
        <label>Input chat JSON (from Chat tab)</label>
        <input type="text" id="render-input" placeholder="/Users/you/Downloads/chat.json">
      </div>
    </div>
    <div class="row">
      <div class="field"><label>Width</label><input type="number" min="1" step="1" id="render-width" value="350"></div>
      <div class="field"><label>Height</label><input type="number" min="1" step="1" id="render-height" value="600"></div>
      <div class="field"><label>Framerate</label><input type="number" min="1" step="1" id="render-framerate" value="30"><div class="hint">whole numbers only</div></div>
      <div class="field"><label>Font size</label><input type="number" min="1" step="1" id="render-font_size" value="24"></div>
    </div>
    <div class="row">
      <div class="field grow2">
        <label>Font</label>
        <select id="render-font"><option value="Inter Embedded">Inter Embedded (default)</option></select>
        <div class="hint">"Inter Embedded" is bundled; other fonts must be installed on this Mac</div>
      </div>
      <div class="field"><label>Background</label><input type="text" id="render-background_color" value="#111111"></div>
    </div>
    <div class="row">
      <div class="check" style="padding-top:0">
        <input type="checkbox" id="render-outline">
        <label for="render-outline" style="margin:0;color:var(--text)">Message outlines</label>
      </div>
      <div class="check" style="padding-top:0">
        <input type="checkbox" id="render-timestamp">
        <label for="render-timestamp" style="margin:0;color:var(--text)">Timestamps</label>
      </div>
    </div>
    <div class="row">
      <div class="field" style="min-width:100%">
        <label>Third-party emotes</label>
        <div style="display:flex;gap:20px;flex-wrap:wrap">
          <div class="check" style="padding-top:0">
            <input type="checkbox" id="render-bttv" checked>
            <label for="render-bttv" style="margin:0;color:var(--text)">BTTV</label>
          </div>
          <div class="check" style="padding-top:0">
            <input type="checkbox" id="render-ffz" checked>
            <label for="render-ffz" style="margin:0;color:var(--text)">FFZ</label>
          </div>
          <div class="check" style="padding-top:0">
            <input type="checkbox" id="render-stv" checked>
            <label for="render-stv" style="margin:0;color:var(--text)">7TV</label>
          </div>
        </div>
        <div class="hint">Emotes must be embedded in the chat JSON (or fetched online at render time)</div>
      </div>
    </div>
    <div class="row">
      <div class="field grow2">
        <label>Output folder</label>
        <input type="text" id="render-output_dir" class="outdir">
      </div>
      <div class="field">
        <label>Filename</label>
        <input type="text" id="render-filename" placeholder="chat_render.mp4">
      </div>
    </div>
    <div class="actions">
      <button class="btn primary" onclick="start('chatrender')">Render Chat</button>
    </div>
  </div>

  <!-- Info -->
  <div class="pane card" id="pane-info">
    <div class="row">
      <div class="field grow2">
        <label>VOD or Clip URL / ID</label>
        <input type="text" id="info-id" placeholder="https://www.twitch.tv/videos/612942303">
      </div>
      <div class="field">
        <label>OAuth token</label>
        <input type="text" id="info-oauth" placeholder="optional">
      </div>
    </div>
    <div class="actions">
      <button class="btn primary" onclick="start('info')">Get Info</button>
    </div>
  </div>

  <!-- Status -->
  <div class="card" id="status-card">
    <div id="status-head">
      <span id="status-badge">Idle</span>
      <span id="status-line"></span>
      <button class="btn danger" id="cancel-btn" onclick="cancel()" style="display:none">Cancel</button>
      <button class="btn" id="reveal-btn" onclick="reveal()" style="display:none">Show in Finder</button>
    </div>
    <div id="bar-track"><div id="bar-fill"></div></div>
    <div id="log"></div>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);

// ---- tabs ----
document.querySelectorAll('.tabs button').forEach(btn => {
  btn.onclick = () => {
    document.querySelectorAll('.tabs button').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.pane').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    $('pane-' + btn.dataset.tab).classList.add('active');
  };
});

// ---- persisted output dirs + oauth ----
fetch('/api/defaults').then(r => r.json()).then(d => {
  document.querySelectorAll('.outdir').forEach(el => {
    el.value = localStorage.getItem('outdir') || d.output_dir;
    el.addEventListener('change', () => {
      localStorage.setItem('outdir', el.value);
      document.querySelectorAll('.outdir').forEach(o => o.value = el.value);
    });
  });
  ['vod-oauth', 'info-oauth'].forEach(id => {
    $(id).value = localStorage.getItem('oauth') || '';
    $(id).addEventListener('change', () => {
      localStorage.setItem('oauth', $(id).value);
      ['vod-oauth', 'info-oauth'].forEach(o => $(o).value = $(id).value);
    });
  });
  // Populate the font dropdown (Inter Embedded stays the default first option).
  const fontSel = $('render-font');
  (d.fonts || []).forEach(name => {
    if (name === 'Inter Embedded') return; // already present as default
    const opt = document.createElement('option');
    opt.value = name; opt.textContent = name;
    fontSel.appendChild(opt);
  });
  fontSel.value = localStorage.getItem('renderFont') || 'Inter Embedded';
  fontSel.addEventListener('change', () => localStorage.setItem('renderFont', fontSel.value));
});

// ---- auto filename suggestions ----
function extractId(url) {
  url = url.trim();
  let m = url.match(/videos\/(\d+)/) || url.match(/clip\/([A-Za-z0-9_-]+)/);
  if (m) return m[1];
  if (/^[A-Za-z0-9_-]+$/.test(url)) return url;
  return null;
}
function suggest(srcId, destId, prefix, ext) {
  $(srcId).addEventListener('input', () => {
    const id = extractId($(srcId).value);
    const dest = $(destId);
    if (id && (!dest.value || dest.dataset.auto === '1')) {
      dest.value = prefix + '_' + id + ext;
      dest.dataset.auto = '1';
    }
  });
  $(destId).addEventListener('input', () => { $(destId).dataset.auto = '0'; });
}
suggest('vod-id', 'vod-filename', 'vod', '.mp4');
suggest('clip-id', 'clip-filename', 'clip', '.mp4');
suggest('chat-id', 'chat-filename', 'chat', '.json');
$('chat-format').addEventListener('change', () => {
  const f = $('chat-filename');
  const ext = '.' + $('chat-format').value;
  if (f.value) f.value = f.value.replace(/\.(json|html|txt)$/, '') + ext;
  syncEmbedRow();
});
// The embed-emote sub-row only applies to embedded JSON downloads.
function syncEmbedRow() {
  const on = $('chat-embed').checked && $('chat-format').value === 'json';
  const row = $('chat-embed-row');
  row.style.opacity = on ? '1' : '0.4';
  ['chat-bttv', 'chat-ffz', 'chat-stv'].forEach(id => $(id).disabled = !on);
}
$('chat-embed').addEventListener('change', syncEmbedRow);
syncEmbedRow();
$('render-input').addEventListener('input', () => {
  const f = $('render-filename');
  if (!f.value || f.dataset.auto === '1') {
    const base = $('render-input').value.split('/').pop().replace(/\.json(\.gz)?$/, '');
    if (base) { f.value = base + '_render.mp4'; f.dataset.auto = '1'; }
  }
});

// ---- field collection per mode ----
const FIELD_MAP = {
  videodownload: { id:'vod-id', quality:'vod-quality', beginning:'vod-beginning', ending:'vod-ending',
                   oauth:'vod-oauth', output_dir:'vod-output_dir', filename:'vod-filename' },
  clipdownload:  { id:'clip-id', quality:'clip-quality', output_dir:'clip-output_dir', filename:'clip-filename' },
  chatdownload:  { id:'chat-id', format:'chat-format', beginning:'chat-beginning', ending:'chat-ending',
                   output_dir:'chat-output_dir', filename:'chat-filename' },
  chatrender:    { input:'render-input', width:'render-width', height:'render-height',
                   framerate:'render-framerate', font_size:'render-font_size', font:'render-font',
                   background_color:'render-background_color',
                   output_dir:'render-output_dir', filename:'render-filename' },
  info:          { id:'info-id', oauth:'info-oauth' },
};
const CHECKBOX_MAP = {
  chatdownload: { embed: 'chat-embed', bttv: 'chat-bttv', ffz: 'chat-ffz', stv: 'chat-stv' },
  chatrender:   { outline: 'render-outline', timestamp: 'render-timestamp',
                  bttv: 'render-bttv', ffz: 'render-ffz', stv: 'render-stv' },
};

async function start(mode) {
  const fields = {};
  for (const [key, id] of Object.entries(FIELD_MAP[mode])) fields[key] = $(id).value;
  for (const [key, id] of Object.entries(CHECKBOX_MAP[mode] || {})) fields[key] = $(id).checked;
  const res = await fetch('/api/start', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({mode, fields}),
  });
  const data = await res.json();
  if (data.error) { alert(data.error); return; }
  $('log').textContent = '';
  $('bar-fill').style.width = '0';
  poll();
}

function cancel() { fetch('/api/cancel', {method: 'POST'}); }
function reveal() { fetch('/api/reveal', {method: 'POST'}); }

let polling = false;
async function poll() {
  if (polling) return;
  polling = true;
  const badge = $('status-badge'), line = $('status-line'), log = $('log');
  while (true) {
    let s;
    try { s = await (await fetch('/api/status')).json(); }
    catch { break; }
    line.textContent = s.status_line || '';
    const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
    log.textContent = s.log || '';
    if (atBottom) log.scrollTop = log.scrollHeight;
    if (s.percent != null && s.running) {
      $('bar-track').style.display = 'block';
      $('bar-fill').style.width = s.percent + '%';
    }
    if (s.running) {
      badge.textContent = s.label || 'Running';
      badge.className = 'running';
      $('cancel-btn').style.display = '';
      $('reveal-btn').style.display = 'none';
    } else {
      $('cancel-btn').style.display = 'none';
      $('bar-track').style.display = 'none';
      if (s.exit_code === 0) {
        badge.textContent = 'Done'; badge.className = 'done';
        if (s.last_output) $('reveal-btn').style.display = '';
      } else if (s.exit_code != null) {
        badge.textContent = 'Failed (' + s.exit_code + ')'; badge.className = 'error';
      } else {
        badge.textContent = 'Idle'; badge.className = '';
      }
      break;
    }
    await new Promise(r => setTimeout(r, 500));
  }
  polling = false;
}
poll(); // pick up any job state on page load
</script>
</body>
</html>
"""


def main():
    if not os.path.exists(CLI_PATH):
        sys.exit(f"TwitchDownloaderCLI not found at {CLI_PATH}")
    if not os.access(CLI_PATH, os.X_OK):
        os.chmod(CLI_PATH, 0o755)

    server = None
    for port in PORT_RANGE:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if server is None:
        sys.exit("Could not find a free port between 5959 and 5969")

    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"TwitchDownloader GUI running at {url}")
    print("Keep this window open. Press Ctrl+C to quit.")
    if FFMPEG_PATH is None:
        print("WARNING: ffmpeg not found — VOD downloads and chat renders will fail.")
        print("Install it with:  brew install ffmpeg")
    threading.Thread(target=lambda: (time.sleep(0.4), webbrowser.open(url)), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        with job_lock:
            if current_job and current_job.running:
                current_job.cancel()
        print("\nBye.")


if __name__ == "__main__":
    main()
