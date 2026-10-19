# -*- coding: utf-8 -*-
"""
Kevin Lim Fung Yen Video Downloader — Web Server
Serves a responsive web app and runs yt-dlp downloads on this machine,
reachable from any device on the same network (phone / tablet / PC).
"""
import os
import sys
import json
import re
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_LIBS = os.path.join(os.path.dirname(BASE_DIR), "_libs")   # sibling folder
WEB_DIR = BASE_DIR   # index.html / style.css / app.js live at the app root
DOWNLOADS = os.path.join(BASE_DIR, "downloads")
COOKIE_DIR = os.path.join(BASE_DIR, "cookies")
os.makedirs(DOWNLOADS, exist_ok=True)
os.makedirs(COOKIE_DIR, exist_ok=True)

if os.path.isdir(_LIBS):
    sys.path.insert(0, _LIBS)

import yt_dlp
from yt_dlp.utils import DownloadCancelled
import imageio_ffmpeg

FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
# Cloud platforms (Railway/Render) inject the port via the PORT env var.
PORT = int(os.environ.get("PORT", "8787") or "8787")


def safe_filename(title):
    if not title:
        return "x_video"
    cleaned = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", title)
    return cleaned.strip() or "x_video"


# ---------------------------------------------------------------------------
# Job management
# ---------------------------------------------------------------------------
class Job:
    def __init__(self, url, format_choice, login_browser, cookie_path):
        self.id = uuid.uuid4().hex
        self.url = url
        self.format_choice = format_choice
        self.login_browser = login_browser
        self.cookie_path = cookie_path
        self.state = "idle"          # idle|downloading|paused|done|cancelled|error
        self.percent = 0.0
        self.size_mb = 0.0
        self.message = "Queued"
        self.stop_event = threading.Event()
        self.stop_kind = None
        self.part_files = set()
        self.final_path = None
        self.filename = None
        self.error = None
        self.started = time.time()

JOBS = {}
FINISHED = {}   # job_id -> {filename, path, done_time}


def _build_opts(job):
    opts = {
        "outtmpl": os.path.join(DOWNLOADS, "%(title)s.%(ext)s"),
        "ffmpeg_location": FFMPEG_EXE,
        "progress_hooks": [_make_hook(job)],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": False,
    }
    if job.login_browser == "file" and job.cookie_path:
        opts["cookiefile"] = job.cookie_path
    elif job.login_browser and job.login_browser != "none":
        opts["cookiesfrombrowser"] = (job.login_browser,)

    if job.format_choice == "video_audio":
        opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        opts["merge_output_format"] = "mp4"
    elif job.format_choice == "video_only":
        opts["format"] = "bestvideo[ext=mp4]/bestvideo/best"
        opts["no_audio"] = True
        opts["merge_output_format"] = "mp4"
    else:
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "m4a",
            "preferredquality": "192",
        }]
    return opts


def _make_hook(job):
    def hook(d):
        if job.stop_event.is_set():
            raise DownloadCancelled()
        if d.get("status") == "downloading":
            fn = d.get("filename")
            if fn:
                job.part_files.add(fn)
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            job.percent = (downloaded / total * 100) if total else 0.0
            job.size_mb = downloaded / (1024 * 1024)
            job.message = f"Downloading {job.percent:.0f}% ({job.size_mb:.1f} MB)"
        elif d.get("status") == "finished":
            job.message = "Merging audio & video..."
    return hook


def run_download(job):
    try:
        job.state = "downloading"
        job.stop_event.clear()
        job.message = "Fetching video info..."
        opts = _build_opts(job)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(job.url, download=True)
        ext = info.get("ext", "mp4")
        if job.format_choice == "audio_only":
            ext = "m4a"
        final = os.path.join(DOWNLOADS, safe_filename(info.get("title")) + "." + ext)
        if not os.path.exists(final):
            try:
                files = [os.path.join(DOWNLOADS, f) for f in os.listdir(DOWNLOADS)
                         if os.path.isfile(os.path.join(DOWNLOADS, f))]
                final = max(files, key=os.path.getmtime)
            except Exception:
                pass
        job.final_path = final
        job.filename = os.path.basename(final)
        job.state = "done"
        job.message = "Done"
        job.percent = 100.0
        FINISHED[job.id] = {"filename": job.filename, "path": final,
                            "done_time": time.strftime("%H:%M")}
    except DownloadCancelled:
        if job.stop_kind == "cancel":
            for p in list(job.part_files):
                try:
                    os.remove(p)
                except Exception:
                    pass
            job.state = "cancelled"
            job.message = "Cancelled"
        else:
            job.state = "paused"
            job.message = "Paused — click Resume to continue"
    except Exception as e:
        job.state = "error"
        job.message = str(e)
        job.error = str(e)


def start_job(job):
    t = threading.Thread(target=run_download, args=(job,), daemon=True)
    t.start()


def resume_job(job):
    # re-run; yt-dlp continues from the saved .part file
    job.stop_kind = None
    job.stop_event.clear()
    start_job(job)


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------
MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

def _send_json(handler, data, code=200):
    body = json.dumps(data).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    server_version = "KevinDL/1.0"

    def log_message(self, *a):
        pass  # quiet

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            self._serve_file(os.path.join(WEB_DIR, "index.html"))
        elif path == "/app.js":
            self._serve_file(os.path.join(WEB_DIR, "app.js"))
        elif path == "/style.css":
            self._serve_file(os.path.join(WEB_DIR, "style.css"))
        elif path == "/api/health":
            _send_json(self, {"ok": True, "name": "Kevin Lim Fung Yen Video Downloader",
                              "address": self._lan_ip(), "port": PORT})
        elif path == "/api/files":
            _send_json(self, {"files": [
                {"id": jid, "filename": f["filename"], "done_time": f["done_time"],
                 "url": f"/api/file?id={jid}"}
                for jid, f in sorted(FINISHED.items(),
                                     key=lambda kv: kv[1]["done_time"], reverse=True)
            ]})
        elif path == "/api/status":
            jid = parse_qs(parsed.query).get("id", [""])[0]
            job = JOBS.get(jid)
            if not job:
                _send_json(self, {"error": "unknown job"}, 404)
                return
            _send_json(self, {
                "id": job.id, "state": job.state, "percent": round(job.percent, 1),
                "size_mb": round(job.size_mb, 2), "message": job.message,
                "filename": job.filename,
            })
        elif path == "/api/file":
            jid = parse_qs(parsed.query).get("id", [""])[0]
            f = FINISHED.get(jid)
            if not f or not os.path.exists(f["path"]):
                _send_json(self, {"error": "file not found"}, 404)
                return
            self._serve_download(f["path"], f["filename"])
        else:
            _send_json(self, {"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/download":
            data = self._read_json()
            url = (data.get("url") or "").strip()
            if not url or not url.lower().startswith(("http://", "https://")):
                _send_json(self, {"error": "Please enter a valid URL."}, 400)
                return
            job = Job(
                url=url,
                format_choice=data.get("format", "video_audio"),
                login_browser=data.get("login", "none"),
                cookie_path=data.get("cookie_path"),
            )
            JOBS[job.id] = job
            start_job(job)
            _send_json(self, {"id": job.id})
        elif path == "/api/control":
            data = self._read_json()
            job = JOBS.get(data.get("id"))
            if not job:
                _send_json(self, {"error": "unknown job"}, 404)
                return
            action = data.get("action")
            if action == "pause" and job.state == "downloading":
                job.stop_kind = "pause"
                job.stop_event.set()
                job.message = "Pausing..."
            elif action == "cancel":
                job.stop_kind = "cancel"
                job.stop_event.set()
                if job.state == "paused":
                    for p in list(job.part_files):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
                    job.state = "cancelled"
                    job.message = "Cancelled"
            elif action == "resume" and job.state == "paused":
                resume_job(job)
            _send_json(self, {"state": job.state})
        elif path == "/api/upload-cookie":
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length > 0 else b""
            if not raw or b"auth_token" not in raw:
                _send_json(self, {"error": "File doesn't look like a cookies.txt file."}, 400)
                return
            fname = "cookies_" + uuid.uuid4().hex + ".txt"
            path_out = os.path.join(COOKIE_DIR, fname)
            with open(path_out, "wb") as f:
                f.write(raw)
            _send_json(self, {"cookie_path": path_out})
        else:
            _send_json(self, {"error": "not found"}, 404)

    def _serve_file(self, full_path):
        if not os.path.isfile(full_path):
            _send_json(self, {"error": "not found"}, 404)
            return
        ext = os.path.splitext(full_path)[1].lower()
        ctype = MIME.get(ext, "application/octet-stream")
        with open(full_path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _serve_download(self, full_path, filename):
        with open(full_path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4" if filename.endswith(".mp4") else "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _lan_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"
        finally:
            s.close()


def main():
    ip = socket.gethostbyname(socket.gethostname())
    # best-effort LAN ip
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        lan = s.getsockname()[0]
    except Exception:
        lan = "127.0.0.1"
    finally:
        s.close()

    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    httpd.daemon_threads = True
    print("=" * 56)
    print("  Kevin Lim Fung Yen Video Downloader — Web")
    print("=" * 56)
    print(f"  Listening on port {PORT}")
    print(f"  Downloads save to: {DOWNLOADS}")
    print("  Press Ctrl+C to stop.")
    print("=" * 56)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        httpd.server_close()


if __name__ == "__main__":
    main()
