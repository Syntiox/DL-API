from __future__ import annotations

import os
import time
import hashlib
import subprocess
from typing import Optional

import httpx
from cachetools import TTLCache
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from jose import jwt, JWTError
from pydantic import BaseModel

import engine  # engine.py

#  Config
JWT_SECRET       = os.environ.get("JWT_SECRET",  "change-me-in-production")
API_SECRET       = os.environ.get("API_SECRET",  "change-me-in-production")
JWT_ALGORITHM    = "HS256"
TOKEN_TTL_SECONDS = 1200  # 20 minutes

# TTLCache: max 10,000 tokens kept, each auto-deleted after 20 min
# This prevents the memory leak from a plain set() that never cleans itself
used_tokens: TTLCache = TTLCache(maxsize=10_000, ttl=TOKEN_TTL_SECONDS)

#  App setup
app = FastAPI(
    title="Syntiox Smart DL API",
    description="Secure streaming proxy API with JWT-based temporary download links",
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

#  Middleware — X-API-KEY restriction on /info
#  Origin/Host headers can be spoofed by anyone,
#  so we use a shared secret between Koyeb & Render.
@app.middleware("http")
async def restrict_info_with_api_key(request: Request, call_next):
    if request.url.path == "/info":
        api_key = request.headers.get("x-api-key")
        if api_key != API_SECRET:
            return JSONResponse(
                {
                    "creator": "Shaluka Gimhan",
                    "web url": "syntiox.top",
                    "error": "Forbidden: Invalid API Key"
                },
                status_code=403,
            )
    return await call_next(request)

#  Request Models
class InfoRequest(BaseModel):
    url: str

#  JWT helpers
def _make_stream_url(yt_url: str, base_url: str, ext: str = "mp4", audio_only: bool = False, cookies: str = None, headers: dict = None) -> str:
    """
    Wrap a raw YT URL inside a signed JWT and return a proxy URL.
    The jti (JWT ID) is a short hash used for single-use enforcement.
    audio_only=True signals /stream to pipe through ffmpeg to extract audio.
    """
    jti = hashlib.sha256(f"{yt_url}{time.time()}".encode()).hexdigest()[:20]
    payload = {
        "url": yt_url,
        "ext": ext,
        "exp": time.time() + TOKEN_TTL_SECONDS,
        "jti": jti,
    }
    if audio_only:
        payload["audio_only"] = True
    if cookies:
        payload["cookies"] = cookies
    if headers:
        payload["headers"] = headers
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return f"{base_url}stream?token={token}"

def _guess_ext(fmt: dict) -> str:
    """Guess file extension from a yt-dlp format dict."""
    return fmt.get("ext") or "mp4"

#  Routes

@app.get("/", tags=["Health"])
def root():
    """API health check."""
    return {
        "creator": "Shaluka Gimhan",
        "web url": "syntiox.top",
        "status": "ok",
        "service": "Syntiox DL API",
        "version": "3.0.0"
    }

@app.get("/ffmpeg", tags=["Health"])
def ffmpeg_check():
    """Check whether ffmpeg is available on the server."""
    ok = engine.check_ffmpeg()
    return {
        "creator": "Shaluka Gimhan",
        "web url": "syntiox.top",
        "ffmpeg_available": ok
    }

@app.post("/info", tags=["Info"])
async def get_info(body: InfoRequest, request: Request):
    """
    Provide a YouTube URL to get video details + secure temporary stream URLs.
    Requires X-API-KEY header. Raw YouTube URLs are never exposed to the caller.
    Each URL in the response is a signed JWT proxy link valid for 20 minutes.
    """
    result = engine.get_info(body.url)
    if result.get("type") == "error":
        return JSONResponse(
            status_code=400,
            content={
                "creator": "Shaluka Gimhan",
                "web url": "syntiox.top",
                "error": result["message"]
            }
        )

    # Fix scheme for reverse-proxy hosts (Railway, Render, etc.)
    # They terminate TLS and forward requests internally as http,
    # so request.base_url gives http:// even when the client used https://.
    # X-Forwarded-Proto contains the original scheme used by the client.
    forwarded_proto = request.headers.get("x-forwarded-proto")
    base = str(request.base_url)
    if forwarded_proto == "https" and base.startswith("http://"):
        base = "https://" + base[len("http://"):]

    # Wrap best_video URL
    best_video_dict = result.pop("best_video", {})
    if best_video_dict and best_video_dict.get("url"):
        result["best_video_download_url"] = _make_stream_url(
            best_video_dict["url"], base, ext="mp4",
            cookies=best_video_dict.get("cookies"),
            headers=best_video_dict.get("http_headers")
        )

    url_lower = body.url.lower()
    audio_friendly_domains = [
        "youtube.com", "youtu.be",
        "tiktok.com", "vm.tiktok", "vt.tiktok",
        "facebook.com", "fb.watch", "fb.com",
        "soundcloud.com"
    ]
    generate_audio = any(d in url_lower for d in audio_friendly_domains)

    # Wrap best_audio URL — if no separate audio stream, extract from video via ffmpeg
    best_audio_dict = result.pop("best_audio", {})
    if generate_audio:
        if best_audio_dict and best_audio_dict.get("url"):
            result["audio_download_url"] = _make_stream_url(
                best_audio_dict["url"], base, ext="m4a",
                cookies=best_audio_dict.get("cookies"),
                headers=best_audio_dict.get("http_headers")
            )
        elif best_video_dict and best_video_dict.get("url") and engine.check_ffmpeg():
            # No separate audio stream — extract audio from the combined video stream
            result["audio_download_url"] = _make_stream_url(
                best_video_dict["url"], base, ext="mp3", audio_only=True,
                cookies=best_video_dict.get("cookies"),
                headers=best_video_dict.get("http_headers")
            )

    # Wrap per-format URLs — never expose raw YT URL
    if "formats" in result:
        for fmt in result["formats"]:
            raw_url = fmt.get("url", "")
            if raw_url:
                ext = _guess_ext(fmt)
                cookies = fmt.pop("cookies", None)
                headers = fmt.pop("http_headers", None)
                fmt["download_url"] = _make_stream_url(raw_url, base, ext=ext, cookies=cookies, headers=headers)
            # Always remove the raw YT URL from the response
            fmt.pop("url", None)

    final_result = {
        "creator": "Shaluka Gimhan",
        "web url": "syntiox.top"
    }
    final_result.update(result)

    return final_result


@app.get("/stream", tags=["Stream"])
async def stream_video(token: str = Query(...)):
    """
    Single-use JWT streaming endpoint.
    - Validates the token signature and expiry
    - Enforces single-use via TTLCache (auto-expires after 20 min → no memory leak)
    - Proxies the video/audio in 256 KB chunks
    - Sets correct Content-Type and filename based on ext in token payload
    """
    # ── 1. Decode & validate JWT ──────────────────
    try:
        payload = jwt.decode(
            token, JWT_SECRET, algorithms=[JWT_ALGORITHM],
            options={"verify_exp": False},  # we check exp manually below
        )
    except JWTError:
        raise HTTPException(status_code=403, detail="Invalid token")

    # ── 2. Manual expiry check ────────────────────
    if time.time() > payload.get("exp", 0):
        raise HTTPException(status_code=403, detail="Token expired")

    # ── 3. Single-use enforcement (TTLCache) ──────
    jti = payload.get("jti")
    if not jti:
        raise HTTPException(status_code=403, detail="Malformed token")

    if jti in used_tokens:
        raise HTTPException(status_code=403, detail="Token already used")
    used_tokens[jti] = True  # mark as used; auto-removed after TOKEN_TTL_SECONDS

    # ── 4. Determine media type from ext ─────────
    yt_url = payload.get("url", "")
    if not yt_url:
        raise HTTPException(status_code=403, detail="Malformed token payload")

    ext = payload.get("ext", "mp4").lower()
    audio_only = payload.get("audio_only", False)
    req_cookies = payload.get("cookies")
    req_headers = payload.get("headers") or {}
    is_m3u8 = ".m3u8" in yt_url.lower()

    # Build ffmpeg headers string if needed
    ffmpeg_headers = ""
    for k, v in req_headers.items():
        ffmpeg_headers += f"{k}: {v}\r\n"
    if req_cookies:
        ffmpeg_headers += f"Cookie: {req_cookies}\r\n"

    # ── 5. Stream via ffmpeg (audio extraction or M3U8 video) ───────────
    if (audio_only or is_m3u8) and engine.check_ffmpeg():
        async def _ffmpeg_streamer():
            cmd = ["ffmpeg"]
            if ffmpeg_headers:
                cmd.extend(["-headers", ffmpeg_headers])
            
            cmd.extend(["-i", yt_url])

            if audio_only:
                cmd.extend([
                    "-vn",                   # drop video
                    "-acodec", "libmp3lame", # encode to mp3
                    "-q:a", "2",             # high quality (VBR ~190 kbps)
                    "-f", "mp3",             # output format
                    "pipe:1"                 # write to stdout
                ])
            else:
                cmd.extend([
                    "-c", "copy",            # copy codecs (no re-encoding)
                    "-bsf:a", "aac_adtstoasc", # fix AAC bitstream for MP4 container
                    "-f", "mp4",             # output format mp4
                    "-movflags", "frag_keyframe+empty_moov", # required for streaming mp4 via pipe
                    "pipe:1"
                ])

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            try:
                while True:
                    chunk = proc.stdout.read(262_144)  # 256 KB
                    if not chunk:
                        break
                    yield chunk
            finally:
                proc.stdout.close()
                proc.wait()

        return StreamingResponse(
            _ffmpeg_streamer(),
            media_type="audio/mpeg" if audio_only else "video/mp4",
            headers={
                "Content-Disposition": f'attachment; filename="{"audio.mp3" if audio_only else "video.mp4"}"',
                "Cache-Control": "no-store",
            },
        )

    # ── 6. Direct async chunk-by-chunk proxy ──────────────────────────────
    AUDIO_EXTS = {"mp3", "m4a", "webm", "ogg", "opus", "aac"}
    media_type = f"audio/{ext}" if ext in AUDIO_EXTS else f"video/{ext}"
    filename   = f"download.{ext}"

    async def _streamer():
        # Detect platform from the CDN/stream URL and set the correct Referer/Origin.
        # yt-dlp usually provides these via req_headers, but if missing we fall back
        # to platform-specific defaults so the CDN doesn't 403-reject the request.
        _url_lower = yt_url.lower()

        # ── Platform detection (CDN domains + source domains) ──
        if any(d in _url_lower for d in ("tiktok.com", "tiktokcdn", "tiktokcdn-us", "musical.ly")):
            _referer = "https://www.tiktok.com/"
            _origin  = "https://www.tiktok.com"
        elif any(d in _url_lower for d in ("facebook.com", "fbcdn.net", "fb.watch", "fna.fbcdn.net")):
            _referer = "https://www.facebook.com/"
            _origin  = "https://www.facebook.com"
        elif any(d in _url_lower for d in ("instagram.com", "cdninstagram.com")):
            _referer = "https://www.instagram.com/"
            _origin  = "https://www.instagram.com"
        elif any(d in _url_lower for d in ("soundcloud.com", "sndcdn.com")):
            _referer = "https://soundcloud.com/"
            _origin  = "https://soundcloud.com"
        elif any(d in _url_lower for d in ("x.com", "twitter.com", "twimg.com", "pbs.twimg", "video.twimg")):
            _referer = "https://x.com/"
            _origin  = "https://x.com"
        elif any(d in _url_lower for d in ("pornhub.com", "phncdn.com", "cdn.pornhub")):
            _referer = "https://www.pornhub.com/"
            _origin  = "https://www.pornhub.com"
        elif any(d in _url_lower for d in ("reddit.com", "v.redd.it", "redd.it", "redditstatic.com")):
            _referer = "https://www.reddit.com/"
            _origin  = "https://www.reddit.com"
        elif any(d in _url_lower for d in ("twitch.tv", "twitchcdn.net", "twitch-clips-production")):
            _referer = "https://www.twitch.tv/"
            _origin  = "https://www.twitch.tv"
        elif any(d in _url_lower for d in ("dailymotion.com", "dmcdn.net")):
            _referer = "https://www.dailymotion.com/"
            _origin  = "https://www.dailymotion.com"
        elif any(d in _url_lower for d in ("vimeo.com", "vimeocdn.com")):
            _referer = "https://vimeo.com/"
            _origin  = "https://vimeo.com"
        elif any(d in _url_lower for d in ("bilibili.com", "bilivideo.com", "bilivideo.cn")):
            _referer = "https://www.bilibili.com/"
            _origin  = "https://www.bilibili.com"
        else:
            # Default → YouTube / googlevideo CDN
            _referer = "https://www.youtube.com/"
            _origin  = "https://www.youtube.com"

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 13; SM-S918B) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Mobile Safari/537.36"
            ),
            "Referer": _referer,
            "Origin":  _origin,
        }
        # yt-dlp provided headers always win — they are more specific than our defaults
        headers.update(req_headers)
        if req_cookies:
            headers["Cookie"] = req_cookies

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=300, write=None, pool=None),
            follow_redirects=True,
        ) as client:
            async with client.stream("GET", yt_url, headers=headers) as resp:
                async for chunk in resp.aiter_bytes(chunk_size=262_144):  # 256 KB
                    yield chunk

    return StreamingResponse(
        _streamer(),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
