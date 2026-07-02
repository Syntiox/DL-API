from __future__ import annotations

import os
import time
import hashlib
from typing import Optional

import httpx
from cachetools import TTLCache
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from jose import jwt, JWTError
from pydantic import BaseModel

import engine  # engine.py

# ─────────────────────────────────────────────
#  Config
# ─────────────────────────────────────────────
JWT_SECRET       = os.environ.get("JWT_SECRET",  "change-me-in-production")
API_SECRET       = os.environ.get("API_SECRET",  "change-me-in-production")
JWT_ALGORITHM    = "HS256"
TOKEN_TTL_SECONDS = 1200  # 20 minutes

# TTLCache: max 10,000 tokens kept, each auto-deleted after 20 min
# This prevents the memory leak from a plain set() that never cleans itself
used_tokens: TTLCache = TTLCache(maxsize=10_000, ttl=TOKEN_TTL_SECONDS)

# ─────────────────────────────────────────────
#  App setup
# ─────────────────────────────────────────────
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

# ─────────────────────────────────────────────
#  Middleware — X-API-KEY restriction on /info
#  Origin/Host headers can be spoofed by anyone,
#  so we use a shared secret between Koyeb & Render.
# ─────────────────────────────────────────────
@app.middleware("http")
async def restrict_info_with_api_key(request: Request, call_next):
    if request.url.path == "/info":
        api_key = request.headers.get("x-api-key")
        if api_key != API_SECRET:
            return JSONResponse(
                {"error": "Forbidden: Invalid API Key"},
                status_code=403,
            )
    return await call_next(request)

# ─────────────────────────────────────────────
#  Request Models
# ─────────────────────────────────────────────
class InfoRequest(BaseModel):
    url: str

# ─────────────────────────────────────────────
#  JWT helpers
# ─────────────────────────────────────────────
def _make_stream_url(yt_url: str, base_url: str, ext: str = "mp4") -> str:
    """
    Wrap a raw YT URL inside a signed JWT and return a proxy URL.
    The jti (JWT ID) is a short hash used for single-use enforcement.
    """
    jti = hashlib.sha256(f"{yt_url}{time.time()}".encode()).hexdigest()[:20]
    payload = {
        "url": yt_url,
        "ext": ext,
        "exp": time.time() + TOKEN_TTL_SECONDS,
        "jti": jti,
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return f"{base_url}stream?token={token}"

def _guess_ext(fmt: dict) -> str:
    """Guess file extension from a yt-dlp format dict."""
    return fmt.get("ext") or "mp4"

# ─────────────────────────────────────────────
#  Routes
# ─────────────────────────────────────────────

@app.get("/", tags=["Health"])
def root():
    """API health check."""
    return {"status": "ok", "service": "Syntiox DL API", "version": "3.0.0"}


@app.get("/ffmpeg", tags=["Health"])
def ffmpeg_check():
    """Check whether ffmpeg is available on the server."""
    ok = engine.check_ffmpeg()
    return {"ffmpeg_available": ok}


@app.post("/info", tags=["Info"])
async def get_info(body: InfoRequest, request: Request):
    """
    Provide a YouTube URL to get video details + secure temporary stream URLs.
    Requires X-API-KEY header. Raw YouTube URLs are never exposed to the caller.
    Each URL in the response is a signed JWT proxy link valid for 20 minutes.
    """
    result = engine.get_info(body.url)
    if result.get("type") == "error":
        raise HTTPException(status_code=400, detail=result["message"])

    base = str(request.base_url)  # e.g. https://xxx.onrender.com/

    # Wrap best_video URL
    best_video = result.pop("best_video", "")
    if best_video:
        result["best_video_download_url"] = _make_stream_url(
            best_video, base, ext="mp4"
        )

    # Wrap best_audio URL
    best_audio = result.pop("best_audio", "")
    if best_audio:
        result["audio_download_url"] = _make_stream_url(
            best_audio, base, ext="m4a"
        )

    # Wrap per-format URLs — never expose raw YT URL
    if "formats" in result:
        for fmt in result["formats"]:
            raw_url = fmt.get("url", "")
            if raw_url:
                ext = _guess_ext(fmt)
                fmt["download_url"] = _make_stream_url(raw_url, base, ext=ext)
            # Always remove the raw YT URL from the response
            fmt.pop("url", None)

    return result


@app.get("/stream", tags=["Stream"])
async def stream_video(token: str = Query(...)):
    """
    Single-use JWT streaming endpoint.
    - Validates the token signature and expiry
    - Enforces single-use via TTLCache (auto-expires after 20 min → no memory leak)
    - Proxies the video/audio in 64 KB chunks
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
    AUDIO_EXTS = {"mp3", "m4a", "webm", "ogg", "opus", "aac"}
    media_type = f"audio/{ext}" if ext in AUDIO_EXTS else f"video/{ext}"
    filename   = f"download.{ext}"

    # ── 5. Async chunk-by-chunk proxy ─────────────
    async def _streamer():
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 13; SM-S918B) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Mobile Safari/537.36"
            ),
            "Referer": "https://www.youtube.com/",
            "Origin":  "https://www.youtube.com",
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=300, write=None, pool=None),
            follow_redirects=True,
        ) as client:
            async with client.stream("GET", yt_url, headers=headers) as resp:
                async for chunk in resp.aiter_bytes(chunk_size=65_536):  # 64 KB
                    yield chunk

    return StreamingResponse(
        _streamer(),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
