# Kevin Lim Fung Yen Video Downloader — Docker image
# Works on Railway, Render, Fly.io, and any Docker host.
FROM python:3.12-slim

WORKDIR /app

# Install Python deps (yt-dlp + ffmpeg binary bundled via imageio-ffmpeg)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY server.py .
COPY index.html style.css app.js ./

# Cloud platforms inject PORT; default to 8787
ENV PORT=8787
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+__import__('os').environ.get('PORT','8787')+'/api/health',timeout=4).status==200 else 1)" || exit 1

CMD ["python", "server.py"]
