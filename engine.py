import yt_dlp
import os
import shutil

# ──────────────────────────────────────────────
#  Silent logger (suppress yt-dlp console noise)
# ──────────────────────────────────────────────
class _SilentLogger:
    def debug(self, msg):   pass
    def warning(self, msg): pass
    def error(self, msg):   print(f"[ENGINE ERROR] {msg}")

_COMMON_OPTS = {
    'quiet': True,
    'no_warnings': True,
    'nocheckcertificate': True,
    'source_address': '0.0.0.0',
    'simulate': True,
}

import urllib.request
import json

# ──────────────────────────────────────────────
#  Helper: Dynamic YouTube Extractor Args
# ──────────────────────────────────────────────
def _get_youtube_args() -> dict:
    yt_args = {
        'player_client': ['android', 'web', 'ios'],
        'player_skip': ['dash', 'hls']
    }
    
    # 1. Try to fetch from Remote PO Token Server (if configured)
    provider_url = os.environ.get("POT_PROVIDER_URL")
    if provider_url:
        try:
            req = urllib.request.Request(provider_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
                
                # Different providers might have different JSON keys
                po_token = data.get("po_token") or data.get("poToken")
                visitor_data = data.get("visitor_data") or data.get("visitorData")
                
                if po_token:
                    yt_args['po_token'] = po_token
                if visitor_data:
                    yt_args['visitor_data'] = visitor_data
        except Exception as e:
            print(f"[ENGINE POT ERROR] Failed to fetch token from {provider_url}: {e}")

    # 2. Fallback to direct environment variables
    if 'po_token' not in yt_args and os.environ.get("YT_PO_TOKEN"):
        yt_args['po_token'] = os.environ.get("YT_PO_TOKEN")
    if 'visitor_data' not in yt_args and os.environ.get("YT_VISITOR_DATA"):
        yt_args['visitor_data'] = os.environ.get("YT_VISITOR_DATA")
        
    return yt_args


# ──────────────────────────────────────────────
#  Helper: extract & rank video formats
# ──────────────────────────────────────────────
def _extract_formats(info: dict) -> list[dict]:
    formats_dict: dict[str, dict] = {}
    for f in info.get('formats', []):
        vcodec    = f.get('vcodec', '')
        ext       = f.get('ext', '')
        height    = f.get('height')
        format_id = f.get('format_id')
        url       = f.get('url')

        if vcodec != 'none' and height and url:
            res = f"{height}p"
            score = 0
            if 'avc' in vcodec: score += 10
            if ext == 'mp4':    score += 5

            if res not in formats_dict or score > formats_dict[res]['score']:
                formats_dict[res] = {
                    'id':    format_id,
                    'res':   res,
                    'ext':   ext,
                    'url':   url,
                    'score': score,
                }

    sorted_formats = sorted(
        formats_dict.values(),
        key=lambda x: int(x['res'].replace('p', '')),
        reverse=True,
    )
    return [{'id': f['id'], 'res': f['res'], 'ext': f['ext'], 'url': f['url']} for f in sorted_formats]

def _get_best_video_with_audio(info: dict) -> str:
    best_video = None
    for f in info.get('formats', []):
        vcodec = f.get('vcodec', 'none')
        acodec = f.get('acodec', 'none')
        url = f.get('url')
        if vcodec != 'none' and acodec != 'none' and url:
            if not best_video or (f.get('height') or 0) > (best_video.get('height') or 0):
                best_video = f
    return best_video.get('url') if best_video else ''

def _get_best_audio(info: dict) -> str:
    best_audio = None
    for f in info.get('formats', []):
        vcodec = f.get('vcodec', 'none')
        acodec = f.get('acodec', 'none')
        url = f.get('url')
        if vcodec == 'none' and acodec != 'none' and url:
            if not best_audio or (f.get('abr') or 0) > (best_audio.get('abr') or 0):
                best_audio = f
    return best_audio.get('url') if best_audio else ''

# ──────────────────────────────────────────────
#  Public API functions
# ──────────────────────────────────────────────
def check_ffmpeg() -> bool:
    """Return True if ffmpeg is available on this machine."""
    return shutil.which('ffmpeg') is not None or os.path.exists('ffmpeg.exe')

def get_info(url: str) -> dict:
    """Fetch metadata for a single video, search query, or a playlist."""
    is_search = url.startswith('ytsearch')
    opts = {
        **_COMMON_OPTS,
        'logger': _SilentLogger(),
        'extractor_args': {
            'youtube': _get_youtube_args()
        },
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            )
        }
    }
    
    # ── Handle Cookies to Bypass Bot Detection ──
    cookie_path = None
    if os.path.exists("cookies.txt"):
        opts['cookiefile'] = "cookies.txt"
    else:
        # Check if cookies are passed via environment variable (either base64 or raw text)
        env_cookies = os.environ.get("YT_COOKIES")
        if env_cookies:
            try:
                import base64
                # Try decoding if it looks like base64, otherwise use raw text
                try:
                    decoded = base64.b64decode(env_cookies.strip(), validate=True).decode('utf-8')
                    if "Netscape" in decoded or "# HTTP Cookie File" in decoded:
                        cookie_content = decoded
                    else:
                        cookie_content = env_cookies
                except Exception:
                    cookie_content = env_cookies
                
                # Write to a temp file
                import tempfile
                temp_cookie = tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.txt', encoding='utf-8')
                temp_cookie.write(cookie_content)
                temp_cookie.close()
                cookie_path = temp_cookie.name
                opts['cookiefile'] = cookie_path
            except Exception as e:
                print(f"[ENGINE COOKIE ERROR] {e}")

    if not is_search:
        opts['extract_flat'] = 'in_playlist'

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

            if 'entries' in info:
                entries = list(info['entries'])
                if is_search and len(entries) > 0:
                    video_info = entries[0]
                    return {
                        'type':     'video',
                        'title':    video_info.get('title'),
                        'thumb':    video_info.get('thumbnail'),
                        'duration': video_info.get('duration'),
                        'uploader': video_info.get('uploader'),
                        'best_video': _get_best_video_with_audio(video_info),
                        'best_audio': _get_best_audio(video_info),
                        'formats':  _extract_formats(video_info),
                    }
                else:
                    videos = [
                        {'title': e.get('title'), 'url': e.get('url')}
                        for e in entries if e.get('url')
                    ]
                    return {
                        'type':   'playlist',
                        'title':  info.get('title'),
                        'count':  len(videos),
                        'videos': videos,
                    }
            else:
                return {
                    'type':     'video',
                    'title':    info.get('title'),
                    'thumb':    info.get('thumbnail'),
                    'duration': info.get('duration'),
                    'uploader': info.get('uploader'),
                    'best_video': _get_best_video_with_audio(info),
                    'best_audio': _get_best_audio(info),
                    'formats':  _extract_formats(info),
                }
    except Exception as exc:
        return {'type': 'error', 'message': str(exc)}
    finally:
        if cookie_path and os.path.exists(cookie_path):
            try:
                os.remove(cookie_path)
            except Exception:
                pass
