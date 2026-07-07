FROM python:3.11-slim

# Install ffmpeg, git, curl, and Node.js 20 LTS (needed for yt-dlp JS runtime + bgutil POT server)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg git curl ca-certificates && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# ── Install yt-dlp-get-pot plugin (bridges yt-dlp ↔ bgutil POT server) ──────
RUN pip install --no-cache-dir yt-dlp-get-pot

# ── Clone and build bgutil PO Token HTTP server ──────────────────────────────
# bgutil runs on localhost:4416 and generates YouTube Proof-of-Origin tokens.
# yt-dlp-get-pot plugin auto-detects it and injects tokens into yt-dlp requests.
RUN mkdir -p /app/pot_server && \
    git clone --depth=1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /app/pot_server/bgutil && \
    cd /app/pot_server/bgutil/server && \
    npm ci && \
    npx tsc && \
    echo "✅ bgutil POT server built successfully"

# Tell yt-dlp to use node as JS runtime and allow EJS remote components
RUN mkdir -p /etc/yt-dlp && \
    echo '--no-js-runtimes' > /etc/yt-dlp/yt-dlp.conf && \
    echo '--js-runtimes node' >> /etc/yt-dlp/yt-dlp.conf && \
    echo '--remote-components ejs:github' >> /etc/yt-dlp/yt-dlp.conf && \
    echo '--extractor-args youtubepot-bgutilhttp:base_url=http://localhost:4416' >> /etc/yt-dlp/yt-dlp.conf

ENV YT_DLP_CONFIG=/etc/yt-dlp/yt-dlp.conf

COPY . .

# Make startup script executable
RUN chmod +x /app/startup.sh

EXPOSE 8000

# startup.sh: starts bgutil in background → waits for it → starts uvicorn
CMD ["/app/startup.sh"]
