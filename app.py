
from flask import Flask, render_template, request, jsonify, Response, stream_with_context, send_from_directory
import requests
import re
import logging
import os
from urllib.parse import quote_plus, urlparse
import ipaddress
from dotenv import load_dotenv


app = Flask(__name__)
load_dotenv()
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Third-party provider configuration.  Cobalt is the primary provider because it
# can return both a muxed video and an MP3 audio download.  An Invidious API is
# used only if Cobalt is unavailable.  Override these values in the deployment
# environment; never put provider credentials in the browser.
COBALT_API_URL = os.getenv("COBALT_API_URL", "https://api.cobalt.tools").rstrip("/")
COBALT_API_TOKEN = os.getenv("COBALT_API_TOKEN")
INVIDIOUS_API_URL = os.getenv(
    "INVIDIOUS_API_URL", "https://inv.nadeko.net"
).rstrip("/")
INVIDIOUS_FALLBACK_URL = os.getenv(
    "INVIDIOUS_FALLBACK_URL", "https://yewtu.be"
).rstrip("/")
REQUEST_TIMEOUT = (5, 30)

# --- PWA Static Files Serving ---
@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory('.', 'manifest.json', mimetype='application/manifest+json')

@app.route('/sw.js')
def serve_sw():
    return send_from_directory('static', 'sw.js', mimetype='application/javascript')
# --------------------------------

def get_file_size(url):
    """Get file size from URL."""
    try:
        resp = requests.head(url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
        size = int(resp.headers.get('content-length', 0))
        if size:
            return f"{size / (1024 * 1024):.2f} MB"
    except:
        pass
    return '0 MB'

def validate_youtube_url(url):
    """Validate YouTube URL."""
    try:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower()
        if host not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
            return False
        return extract_youtube_id(url) is not None
    except (TypeError, ValueError):
        return False

def extract_youtube_id(url):
    """Extract video ID from YouTube URL."""
    try:
        u = url.strip()
        patterns = [
            r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/|youtube\.com/embed/|youtube\.com/v/|youtube\.com/watch\?.*v=)([a-zA-Z0-9_-]{11})'
        ]
        for pattern in patterns:
            m = re.search(pattern, u)
            if m:
                return m.group(1)
    except:
        pass
    return None

def get_youtube_title(url, default):
    """Fetch a display title without making extraction depend on oEmbed."""
    try:
        response = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": url, "format": "json"},
            timeout=(5, 8),
        )
        if response.ok:
            return response.json().get("title", default)
    except requests.RequestException as error:
        logger.info("YouTube oEmbed title lookup failed: %s", error)
    return default


def cobalt_request(payload):
    """Request the configured Cobalt provider and return a ready download URL."""
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if COBALT_API_TOKEN:
        headers["Authorization"] = f"Api-Key {COBALT_API_TOKEN}"

    # Current Cobalt servers use the root endpoint.  /api/json retains
    # compatibility with older self-hosted Cobalt deployments.
    for endpoint in (COBALT_API_URL, f"{COBALT_API_URL}/api/json"):
        try:
            response = requests.post(endpoint, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
            if not response.ok:
                logger.info("Cobalt returned %s from %s", response.status_code, endpoint)
                continue
            data = response.json()
            # Cobalt-compatible servers may return a direct redirect, a
            # tunneled URL, or a local/stream URL depending on configuration.
            if data.get("status") in {"redirect", "tunnel", "local", "stream"} and data.get("url"):
                return data["url"]
            logger.info("Cobalt returned no downloadable result: %s", data.get("status"))
        except (requests.RequestException, ValueError) as error:
            logger.warning("Cobalt request failed: %s", error)
    return None


def extract_youtube_with_cobalt(url):
    """Primary third-party API: Cobalt returns video and MP3 audio downloads."""
    video_id = extract_youtube_id(url)
    if not video_id:
        return None, "Invalid YouTube URL"
    title = get_youtube_title(url, f"YouTube Video {video_id}")
    video_url = cobalt_request({"url": url, "videoQuality": "1080", "filenameStyle": "basic"})
    audio_url = cobalt_request({
        "url": url, "downloadMode": "audio", "audioFormat": "mp3", "filenameStyle": "basic"
    })
    media_list = []
    if video_url:
        filename = f"{title}.mp4"
        media_list.append({
            'filename': filename,
            'size': 'Best Quality',
            'thumbnail': f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg",
            'dlink': video_url,
            'stream_url': f"/api/stream?url={quote_plus(video_url)}",
            'proxy_download': f"/api/download?url={quote_plus(video_url)}&filename={quote_plus(filename)}",
            'type': 'video',
            'quality': 'Video'
        })
    if audio_url:
        filename = f"{title}.mp3"
        media_list.append({
            'filename': filename,
            'size': 'Audio Only',
            'thumbnail': f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg",
            'dlink': audio_url,
            'stream_url': None,
            'proxy_download': f"/api/download?url={quote_plus(audio_url)}&filename={quote_plus(filename)}",
            'type': 'audio',
            'quality': 'Audio MP3'
        })
        
    if media_list:
        return {'media': media_list, 'title': title, 'source': 'youtube'}, None
    return None, "Cobalt extraction failed"

def extract_youtube_with_invidious(url, api_base=None):
    """Fallback third-party API when the configured Cobalt API is unavailable."""
    video_id = extract_youtube_id(url)
    if not video_id:
        return None, "Invalid YouTube URL"
        
    try:
        logger.info("Trying Invidious fallback for video: %s", video_id)
        api_url = f"{(api_base or INVIDIOUS_API_URL)}/api/v1/videos/{video_id}"
        resp = requests.get(api_url, timeout=REQUEST_TIMEOUT)
        if not resp.ok:
            return None, f"Invidious returned HTTP {resp.status_code}"

        data = resp.json()
        title = data.get("title", f"YouTube Video {video_id}")
        media_list = []

        # formatStreams contain muxed video/audio files.
        for stream in data.get("formatStreams", []):
            stream_url = stream.get("url")
            if not stream_url:
                continue
            quality = stream.get("quality", "medium")
            mime_type = stream.get("type", "video/mp4")
            ext = "mp4" if "mp4" in mime_type else "webm"
            filename = f"{title}_{quality}.{ext}"
            media_list.append({
                'filename': filename,
                'size': stream.get("size", "N/A"),
                'thumbnail': f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg",
                'dlink': stream_url,
                'stream_url': f"/api/stream?url={quote_plus(stream_url)}",
                'proxy_download': f"/api/download?url={quote_plus(stream_url)}&filename={quote_plus(filename)}",
                'type': 'video',
                'quality': f"Video ({quality})"
            })

        # Return one audio-only format as an audio fallback.
        for stream in data.get("adaptiveFormats", []):
            stream_url = stream.get("url")
            if not stream_url or not stream.get("type", "").startswith("audio/"):
                continue
            container = stream.get("container", "m4a")
            filename = f"{title}_audio.{container}"
            media_list.append({
                'filename': filename,
                'size': 'Audio Only',
                'thumbnail': f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg",
                'dlink': stream_url,
                'stream_url': None,
                'proxy_download': f"/api/download?url={quote_plus(stream_url)}&filename={quote_plus(filename)}",
                'type': 'audio',
                'quality': f"Audio ({container.upper()})"
            })
            break

        if media_list:
            logger.info("Invidious fallback succeeded")
            return {'media': media_list, 'title': title, 'source': 'invidious'}, None
    except (requests.RequestException, ValueError) as error:
        logger.warning("Invidious fallback failed: %s", error)
    return None, "Invidious extraction failed"


def extract_youtube_with_piped(url):
    """Keyless fallback using the public Piped streams API."""
    video_id = extract_youtube_id(url)
    if not video_id:
        return None, "Invalid YouTube URL"
    try:
        response = requests.get(f"{PIPED_API_URL}/streams/{video_id}", timeout=REQUEST_TIMEOUT)
        if not response.ok:
            return None, f"Piped returned HTTP {response.status_code}"
        data = response.json()
        title = data.get("title", f"YouTube Video {video_id}")
        thumbnail = data.get("thumbnailUrl") or f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"
        media = []

        for stream in data.get("videoStreams", []):
            stream_url = stream.get("url")
            if not stream_url or not stream.get("videoOnly", False):
                # Prefer muxed streams so the downloaded video includes audio.
                if not stream_url or stream.get("videoOnly", False):
                    continue
            quality = stream.get("quality", "video")
            ext = (stream.get("format") or "mp4").lower()
            filename = f"{title}_{quality}.{ext}"
            media.append({"filename": filename, "size": "Video", "thumbnail": thumbnail,
                          "dlink": stream_url, "stream_url": f"/api/stream?url={quote_plus(stream_url)}",
                          "proxy_download": f"/api/download?url={quote_plus(stream_url)}&filename={quote_plus(filename)}",
                          "type": "video", "quality": f"Video ({quality})"})
            break

        for stream in data.get("audioStreams", []):
            stream_url = stream.get("url")
            if not stream_url:
                continue
            ext = (stream.get("format") or "m4a").lower()
            filename = f"{title}_audio.{ext}"
            media.append({"filename": filename, "size": "Audio Only", "thumbnail": thumbnail,
                          "dlink": stream_url, "stream_url": None,
                          "proxy_download": f"/api/download?url={quote_plus(stream_url)}&filename={quote_plus(filename)}",
                          "type": "audio", "quality": f"Audio ({ext.upper()})"})
            break

        return ({"media": media, "title": title, "source": "piped"}, None) if media else (None, "Piped returned no streams")
    except (requests.RequestException, ValueError) as error:
        logger.warning("Piped fallback failed: %s", error)
        return None, "Piped extraction failed"

def extract_youtube_data(url):
    """Extract YouTube video with fallback strategy."""
    # Ask the primary first, then use the fallback to fill any missing media
    # type. This matters because providers can succeed for video but fail for
    # audio (or vice versa) for age-restricted/region-limited videos.
    try:
        primary, primary_error = extract_youtube_with_invidious(url)
    except Exception as error:
        logger.warning("Piped provider error: %s", error)
        primary, primary_error = None, str(error)
    if primary and all(item.get("type") in {"video", "audio"} for item in primary.get("media", [])):
        types = {item.get("type") for item in primary.get("media", [])}
        if types == {"video", "audio"}:
            return primary, None

    try:
        fallback, fallback_error = extract_youtube_with_invidious(url, INVIDIOUS_FALLBACK_URL)
    except Exception as error:
        logger.warning("Invidious provider error: %s", error)
        fallback, fallback_error = None, str(error)
    if fallback:
        if not primary:
            return fallback, None
        existing_types = {item.get("type") for item in primary.get("media", [])}
        primary["media"].extend(
            item for item in fallback.get("media", []) if item.get("type") not in existing_types
        )
        if primary.get("media"):
            primary["source"] = "piped+invidious"
            return primary, None

    detail = primary_error or fallback_error
    logger.warning("All YouTube providers failed: %s", detail)
    return None, f"Unable to download video or audio from YouTube. Providers unavailable ({detail})."


def is_safe_remote_url(value):
    """Allow provider/CDN URLs while rejecting local-network SSRF targets."""
    try:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        host = parsed.hostname
        try:
            address = ipaddress.ip_address(host)
            return not (address.is_private or address.is_loopback or address.is_link_local)
        except ValueError:
            # Hostnames are resolved by the provider/CDN; reject obvious local
            # names but allow normal public domains.
            return host.lower() not in {"localhost", "localhost.localdomain"}
    except (TypeError, ValueError):
        return False

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/extract', methods=['POST'])
def handle_extract():
    try:
        data = request.get_json(force=True)
        url = (data.get('url') or '').strip()
        
        if not url:
            return jsonify({'error': 'Please provide a YouTube link.'}), 400
        
        if not validate_youtube_url(url):
            return jsonify({'error': 'Invalid YouTube URL.'}), 400
        
        result, error = extract_youtube_data(url)
        if error:
            return jsonify({'error': error}), 500
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Extract error: {e}")
        return jsonify({'error': 'Server error.'}), 500

@app.route('/api/ping', methods=['GET'])
def ping():
    return jsonify({'status': 'ok'})

@app.route('/api/stream', methods=['GET'])
def proxy_stream():
    """Stream video through proxy."""
    remote = request.args.get('url')
    if not remote:
        return jsonify({'error': 'Missing url'}), 400
    if not is_safe_remote_url(remote):
        return jsonify({'error': 'Invalid media URL'}), 400
    try:
        resp = requests.get(remote, stream=True, timeout=25, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://www.youtube.com/'
        })
        resp.raise_for_status()
        headers = {k: v for k, v in resp.headers.items() if k.lower() in ['content-type', 'content-length']}
        return Response(stream_with_context(resp.iter_content(8192)), headers=headers)
    except Exception as e:
        logger.error(f"Stream error: {e}")
        return jsonify({'error': 'Stream failed'}), 500

@app.route('/api/download', methods=['GET'])
def proxy_download():
    """Download through proxy."""
    remote = request.args.get('url')
    filename = request.args.get('filename', 'media')
    if not remote:
        return jsonify({'error': 'Missing url'}), 400
    if not is_safe_remote_url(remote):
        return jsonify({'error': 'Invalid media URL'}), 400
    try:
        resp = requests.get(remote, stream=True, timeout=25, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://www.youtube.com/'
        })
        resp.raise_for_status()
        headers = {k: v for k, v in resp.headers.items() if k.lower() in ['content-type', 'content-length']}
        safe_name = "".join(c for c in filename if c.isalnum() or c in '._- ')[:100]
        headers['Content-Disposition'] = f'attachment; filename="{safe_name}"'
        return Response(stream_with_context(resp.iter_content(8192)), headers=headers)
    except Exception as e:
        logger.error(f"Download error: {e}")
        return jsonify({'error': 'Download failed'}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5001)
