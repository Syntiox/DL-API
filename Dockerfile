FROM python:3.11-slim

# Install ffmpeg, git, curl, and Node.js 20 LTS (needed for yt-dlp JS runtime)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg git curl ca-certificates && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Tell yt-dlp to use node as JS runtime and allow EJS remote components
RUN mkdir -p /etc/yt-dlp && \
    echo '--no-js-runtimes' > /etc/yt-dlp/yt-dlp.conf && \
    echo '--js-runtimes node' >> /etc/yt-dlp/yt-dlp.conf && \
    echo '--remote-components ejs:github' >> /etc/yt-dlp/yt-dlp.conf

ENV YT_DLP_CONFIG=/etc/yt-dlp/yt-dlp.conf

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
