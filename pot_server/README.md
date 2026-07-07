# pot_server — PO Token Server for Syntiox DL API

This folder contains the **PO Token HTTP server** based on [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider).

It runs as a background service inside the same Docker container as the main API and provides YouTube Proof-of-Origin tokens to yt-dlp via the `yt-dlp-get-pot` plugin.

## How it works

```
User → /info request
       ↓
   engine.py (yt-dlp)
       ↓  needs PO token?
   yt-dlp-get-pot plugin
       ↓  HTTP request
   bgutil server (localhost:4416)
       ↓  generates token via BotGuard
   returns { po_token, visitor_data }
       ↓
   yt-dlp uses token → YouTube accepts request ✅
```

## Files

| File | Purpose |
|------|---------|
| `package.json` | Node.js deps for bgutil server |
| The actual bgutil server is cloned from GitHub during Docker build |

## Port

The bgutil server listens on `http://localhost:4416` inside the container.
The main API connects to it via `yt-dlp-get-pot` plugin (automatic, no manual URL needed).

## Manual run (local dev)

```bash
cd pot_server
npm ci
# bgutil is cloned during docker build; for local testing:
git clone https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git bgutil
cd bgutil/server && npm ci && npx tsc
node build/main.js
```
