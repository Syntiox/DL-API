import yt_dlp
import os
import shutil

#  Silent logger (suppress yt-dlp console noise)
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
    'format': 'all',
    'js_runtimes': {'node': {}},
    'impersonate': yt_dlp.networking.impersonate.ImpersonateTarget(client='chrome'),
    # ── Network stability ────────────────────────────────────────────────────
    'socket_timeout': 30,      # idle socket timeout (seconds)
    'retries': 10,             # retry on transient network errors
    'fragment_retries': 10,    # retry on fragment errors (HLS/DASH)
    # ── Playlist safety cap ──────────────────────────────────────────────────
    'playlistend': 100,        # max 100 entries per playlist fetch
}

import urllib.request
import json

#  Helper: Dynamic YouTube Extractor Args
def _get_youtube_args() -> dict:
    """
    Build yt-dlp extractor args for YouTube.
    
    Strategy (best → fallback):
      1. tv_embedded client  → no PO token required, bypasses bot detection
      2. bgutil POT server   → auto-generates PO tokens (needs bgutil running)
      3. POT_PROVIDER_URL    → external PO token REST API
      4. YT_PO_TOKEN env var → manual static PO token
    
    tv_embedded is the most reliable for server/datacenter IPs.
    """
    # ── Player clients: let yt-dlp use its own default selection ──────────────
    # DO NOT set player_client here — yt-dlp's default picks the best clients
    # automatically and returns the most formats (including 1080p, 4K, etc.)
    # Forcing a specific client (e.g. tv_embedded) limits available formats.
    yt_args: dict = {}

    # ── PO Token from remote provider URL ──
    provider_url = os.environ.get("POT_PROVIDER_URL")
    if provider_url:
        try:
            req = urllib.request.Request(provider_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
                po_token    = data.get("po_token")    or data.get("poToken")
                visitor_data = data.get("visitor_data") or data.get("visitorData")
                if po_token:
                    yt_args['po_token'] = po_token
                    print(f"[ENGINE POT] Token fetched from provider: {provider_url}")
                if visitor_data:
                    yt_args['visitor_data'] = visitor_data
        except Exception as e:
            print(f"[ENGINE POT ERROR] Failed to fetch from {provider_url}: {e}")

    # ── Fallback: direct env vars ──
    if 'po_token' not in yt_args and os.environ.get("YT_PO_TOKEN"):
        yt_args['po_token'] = os.environ.get("YT_PO_TOKEN")
        print("[ENGINE POT] Using YT_PO_TOKEN from environment")
    if 'visitor_data' not in yt_args and os.environ.get("YT_VISITOR_DATA"):
        yt_args['visitor_data'] = os.environ.get("YT_VISITOR_DATA")

    return yt_args

#  Helper: extract & rank video formats
def _extract_formats(info: dict) -> list[dict]:
    formats_dict: dict[str, dict] = {}
    
    for f in info.get('formats') or []:
        vcodec    = f.get('vcodec', 'none')
        acodec    = f.get('acodec', 'none')
        ext       = f.get('ext', '')
        url       = f.get('url') or f.get('manifest_url')
        format_id = f.get('format_id')
        
        if not url:
            continue

        # 1. Video Formats
        if vcodec != 'none' and f.get('height'):
            res = f"{f.get('height')}p"
            score = 0
            if 'avc' in vcodec: score += 10
            if ext == 'mp4':    score += 5

            if res not in formats_dict or score > formats_dict.get(res, {}).get('score', -1):
                formats_dict[res] = {
                    'id': format_id, 'res': res, 'ext': ext, 'url': url, 'score': score,
                    'cookies': f.get('cookies') or info.get('cookies'),
                    'http_headers': f.get('http_headers') or info.get('http_headers')
                }
                
        # 2. Audio Formats
        elif vcodec == 'none' and acodec != 'none' and f.get('abr'):
            res = f"{int(f.get('abr'))}kbps"
            score = 0
            if ext == 'm4a': score += 5
            
            if res not in formats_dict or score > formats_dict.get(res, {}).get('score', -1):
                formats_dict[res] = {
                    'id': format_id, 'res': res, 'ext': ext, 'url': url, 'score': score,
                    'cookies': f.get('cookies') or info.get('cookies'),
                    'http_headers': f.get('http_headers') or info.get('http_headers')
                }

    def sort_key(item):
        res_str = item['res']
        if res_str.endswith('p'):

            return (1, int(res_str.replace('p', '')))
        elif res_str.endswith('kbps'):

            return (0, int(res_str.replace('kbps', '')))
        return (-1, 0)

    sorted_formats = sorted(formats_dict.values(), key=sort_key, reverse=True)
    
    return [{'id': f['id'], 'res': f['res'], 'ext': f['ext'], 'url': f['url'], 'cookies': f.get('cookies'), 'http_headers': f.get('http_headers')} for f in sorted_formats]

def _get_best_video_with_audio(info: dict) -> dict:
    best_video = None
    for f in info.get('formats') or []:
        vcodec = f.get('vcodec', 'none')
        acodec = f.get('acodec', 'none')
        url = f.get('url') or f.get('manifest_url')
        if vcodec != 'none' and acodec != 'none' and url:
            if not best_video or (f.get('height') or 0) > (best_video.get('height') or 0):
                best_video = f
    if best_video:
        raw = best_video.get('url') or best_video.get('manifest_url', '')
        return {
            'url':          raw,
            'direct_url':   raw,   # raw CDN URL — expose to trusted callers
            'cookies':      best_video.get('cookies') or info.get('cookies'),
            'http_headers': best_video.get('http_headers') or info.get('http_headers'),
            'ext':          best_video.get('ext'),
            'height':       best_video.get('height'),
            'width':        best_video.get('width'),
            'fps':          best_video.get('fps'),
            'vcodec':       best_video.get('vcodec'),
            'acodec':       best_video.get('acodec'),
            'filesize':     best_video.get('filesize') or best_video.get('filesize_approx'),
        }
    return {}

def _get_best_audio(info: dict) -> dict:
    best_audio = None
    for f in info.get('formats') or []:
        vcodec = f.get('vcodec', 'none')
        acodec = f.get('acodec', 'none')
        url = f.get('url') or f.get('manifest_url')
        if vcodec == 'none' and acodec != 'none' and url:
            if not best_audio or (f.get('abr') or 0) > (best_audio.get('abr') or 0):
                best_audio = f
    if best_audio:
        raw = best_audio.get('url') or best_audio.get('manifest_url', '')
        return {
            'url':          raw,
            'direct_url':   raw,   # raw CDN URL — expose to trusted callers
            'cookies':      best_audio.get('cookies') or info.get('cookies'),
            'http_headers': best_audio.get('http_headers') or info.get('http_headers'),
            'ext':          best_audio.get('ext'),
            'abr':          best_audio.get('abr'),
            'acodec':       best_audio.get('acodec'),
            'filesize':     best_audio.get('filesize') or best_audio.get('filesize_approx'),
        }
    return {}

#  Public API functions
def check_ffmpeg() -> bool:
    """Return True if ffmpeg is available on this machine."""
    return shutil.which('ffmpeg') is not None or os.path.exists('ffmpeg.exe')

def get_info(url: str) -> dict:
    """Fetch metadata for a single video, search query, or a playlist."""
    is_search = url.startswith('ytsearch')
    
    yt_args = _get_youtube_args()
    print(f"[ENGINE YT] player_client={yt_args.get('player_client')}, po_token={'YES' if 'po_token' in yt_args else 'NO'}")

    extractor_args = {
        'youtube': yt_args,
    }

    # ── bgutil PO Token server (yt-dlp-get-pot plugin) ───────────────────────
    # If bgutil is running (Docker / startup.sh), the plugin auto-injects tokens.
    # tv_embedded client doesn't need this, but it helps web client fallback.
    bgu_url = os.environ.get('BGU_BASE_URL', 'http://localhost:4416')
    extractor_args['youtubepot-bgutilhttp'] = {'base_url': bgu_url}

    opts = {
        **_COMMON_OPTS,
        'logger': _SilentLogger(),
        'extractor_args': extractor_args,
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
                        'type':            'video',
                        'title':           video_info.get('title'),
                        'thumb':           video_info.get('thumbnail'),
                        'duration':        video_info.get('duration'),
                        'duration_string': video_info.get('duration_string'),
                        'uploader':        video_info.get('uploader'),
                        'uploader_id':     video_info.get('uploader_id'),
                        'channel':         video_info.get('channel'),
                        'channel_id':      video_info.get('channel_id'),
                        'channel_url':     video_info.get('channel_url'),
                        'webpage_url':     video_info.get('webpage_url'),
                        'extractor':       video_info.get('extractor'),
                        'upload_date':     video_info.get('upload_date'),
                        'timestamp':       video_info.get('timestamp'),
                        'view_count':      video_info.get('view_count'),
                        'like_count':      video_info.get('like_count'),
                        'dislike_count':   video_info.get('dislike_count'),
                        'comment_count':   video_info.get('comment_count'),
                        'average_rating':  video_info.get('average_rating'),
                        'age_limit':       video_info.get('age_limit'),
                        'tags':            video_info.get('tags'),
                        'categories':      video_info.get('categories'),
                        'description':     video_info.get('description'),
                        'language':        video_info.get('language'),
                        'is_live':         video_info.get('is_live'),
                        'was_live':        video_info.get('was_live'),
                        'best_video':      _get_best_video_with_audio(video_info),
                        'best_audio':      _get_best_audio(video_info),
                        'formats':         _extract_formats(video_info),
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
                    'type':            'video',
                    'title':           info.get('title'),
                    'thumb':           info.get('thumbnail'),
                    'duration':        info.get('duration'),
                    'duration_string': info.get('duration_string'),
                    'uploader':        info.get('uploader'),
                    'uploader_id':     info.get('uploader_id'),
                    'channel':         info.get('channel'),
                    'channel_id':      info.get('channel_id'),
                    'channel_url':     info.get('channel_url'),
                    'webpage_url':     info.get('webpage_url'),
                    'extractor':       info.get('extractor'),
                    'upload_date':     info.get('upload_date'),
                    'timestamp':       info.get('timestamp'),
                    'view_count':      info.get('view_count'),
                    'like_count':      info.get('like_count'),
                    'dislike_count':   info.get('dislike_count'),
                    'comment_count':   info.get('comment_count'),
                    'average_rating':  info.get('average_rating'),
                    'age_limit':       info.get('age_limit'),
                    'tags':            info.get('tags'),
                    'categories':      info.get('categories'),
                    'description':     info.get('description'),
                    'language':        info.get('language'),
                    'is_live':         info.get('is_live'),
                    'was_live':        info.get('was_live'),
                    'best_video':      _get_best_video_with_audio(info),
                    'best_audio':      _get_best_audio(info),
                    'formats':         _extract_formats(info),
                }
    except Exception as exc:
        error_msg = str(exc)
        print(f"[ENGINE ERROR] get_info failed for {url!r}: {error_msg}")
        return {'type': 'error', 'message': error_msg}
    finally:
        if cookie_path and os.path.exists(cookie_path):
            try:
                os.remove(cookie_path)
            except Exception:
                pass
