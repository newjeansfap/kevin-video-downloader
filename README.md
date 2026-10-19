# Kevin Lim Fung Yen Video Downloader — Web

A responsive online video downloader built for **PC, tablet and mobile**,
designed by Kevin Lim Fung Yen. Paste an X / Twitter (or any yt-dlp-supported)
video link and download it from any device.

## What's inside
- `server.py` — Python web server that runs the download engine (yt-dlp + ffmpeg).
- `web/` — the responsive macOS-style frontend (HTML / CSS / JS), adaptive layouts
  for phone, tablet and desktop.
- `Dockerfile` + `requirements.txt` — container image, ready for Render / Railway / Fly.
- `render.yaml` — Render Blueprint for one-click deployment.

## Features
- Download **video + audio (MP4)**, **video only**, or **audio only (m4a)**.
- Optional **X login** for restricted videos: Chrome / Edge / Firefox server cookies,
  or upload a `cookies.txt` file (most reliable).
- **Pause / Resume / Cancel** with progress bar.
- **Completed downloads** list — tap to save the file to your current device.

## Deploy
Render (one click):
```
https://render.com/deploy?repo=<your-public-git-repo-url>
```
Or: Render → New + → Blueprint → connect your repo → it uses `render.yaml`.

Railway / Fly: deploy the Dockerfile.

## Run locally (optional, for testing)
```
pip install -r requirements.txt
python server.py
# open http://127.0.0.1:8787
```

## Privacy note
The download engine runs on the hosting server. For restricted X videos, you
should upload a `cookies.txt` file; note that this shares those cookies with the
server that hosts the app.
