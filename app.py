
from flask import Flask, render_template, request, jsonify, Response, stream_with_context, send_from_directory
import requests
import re
import logging
import time
import os
from urllib.parse import quote_plus


app = Flask(__name__)
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

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
        u = url.strip().lower()
        patterns = [
            r'youtube\.com/watch\?v=[a-zA-Z0-9_-]+',
            r'youtu\.be/[a-zA-Z0-9_-]+',
            r'youtube\.com/shorts/[a-zA-Z0-9_-]+',
            r'youtube\.com/embed/[a-zA-Z0-9_-]+',
            r'youtube\.com/v/[a-zA-Z0-9_-]+',
        ]
        return any(re.search(p, u) for p in patterns)
    except:
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

def extract_youtube_with_cobalt(url):
    """Try to extract YouTube video using Cobalt API instances."""
    instances = [
        "https://api.cobalt.tools",
        "https://cobalt.api.rylor.org",
        "https://cobalt.k6.cz",
        "https://cobalt-api.lunes.host",
        "https://co.wuk.sh"
    ]
    
    video_id = extract_youtube_id(url)
    if not video_id:
        return None, "Invalid YouTube URL"
        
    title = f"YouTube Video {video_id}"
    try:
        oembed_url = f"https://www.youtube.com/oembed?url={quote_plus(url)}&format=json"
        oresp = requests.get(oembed_url, timeout=5)
        if oresp.ok:
            title = oresp.json().get('title', title)
    except Exception as e:
        logger.warning(f"Failed to fetch YouTube oembed title: {e}")
        
    media_list = []
    
    # Try video download
    video_url = None
    for instance in instances:
        try:
            logger.info(f"Trying Cobalt video download on instance: {instance}")
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            payload = {
                "url": url,
                "videoQuality": "1080",
                "filenameStyle": "basic"
            }
            resp = requests.post(f"{instance}/api/json", json=payload, headers=headers, timeout=8)
            if resp.status_code == 404:
                resp = requests.post(instance, json=payload, headers=headers, timeout=8)
            
            if resp.ok:
                data = resp.json()
                if data.get("status") in ["redirect", "tunnel"]:
                    video_url = data.get("url")
                    logger.info(f"Cobalt video success on {instance}")
                    break
        except Exception as e:
            logger.warning(f"Cobalt video instance {instance} failed: {e}")
            continue
            
    # Try audio download
    audio_url = None
    for instance in instances:
        try:
            logger.info(f"Trying Cobalt audio download on instance: {instance}")
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            payload = {
                "url": url,
                "downloadMode": "audio",
                "audioFormat": "mp3",
                "filenameStyle": "basic"
            }
            resp = requests.post(f"{instance}/api/json", json=payload, headers=headers, timeout=8)
            if resp.status_code == 404:
                resp = requests.post(instance, json=payload, headers=headers, timeout=8)
                
            if resp.ok:
                data = resp.json()
                if data.get("status") in ["redirect", "tunnel"]:
                    audio_url = data.get("url")
                    logger.info(f"Cobalt audio success on {instance}")
                    break
        except Exception as e:
            logger.warning(f"Cobalt audio instance {instance} failed: {e}")
            continue
            
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

def extract_youtube_with_invidious(url):
    """Fallback to public Invidious API instances."""
    video_id = extract_youtube_id(url)
    if not video_id:
        return None, "Invalid YouTube URL"
        
    invidious_instances = [
        "https://yewtu.be",
        "https://invidious.nerdvpn.de",
        "https://invidious.flokinet.to",
        "https://invidious.projectsegfau.lt",
        "https://inv.tux.im",
        "https://invidious.io"
    ]
    
    for instance in invidious_instances:
        try:
            logger.info(f"Trying Invidious instance: {instance} for video: {video_id}")
            api_url = f"{instance}/api/v1/videos/{video_id}"
            resp = requests.get(api_url, timeout=8)
            if resp.ok:
                data = resp.json()
                title = data.get("title", f"YouTube Video {video_id}")
                media_list = []
                
                # Extract format streams (combined video + audio)
                streams = data.get("formatStreams", [])
                for idx, stream in enumerate(streams):
                    quality = stream.get("quality", "medium")
                    stream_url = stream.get("url")
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
                    
                # Extract adaptive formats for audio (audio only)
                adaptive = data.get("adaptiveFormats", [])
                audio_added = False
                for stream in adaptive:
                    if stream.get("type", "").startswith("audio/") and not audio_added:
                        stream_url = stream.get("url")
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
                        audio_added = True
                        
                if media_list:
                    logger.info(f"Invidious success on {instance}")
                    return {'media': media_list, 'title': title, 'source': 'youtube'}, None
        except Exception as e:
            logger.warning(f"Invidious instance {instance} failed: {e}")
            continue
            
    return None, "Invidious extraction failed"

def extract_youtube_with_loader_to(url):
    """Fallback using Loader.to AJAX API."""
    try:
        video_id = extract_youtube_id(url)
        if not video_id:
            return None, "Invalid YouTube URL"
        
        api_url = "https://api.loader.to/api/ajax/download.php"
        params = {
            "url": url,
            "format": "720",
            "button": "1"
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://loader.to/"
        }
        
        logger.info(f"Trying Loader.to for video: {video_id}")
        resp = requests.get(api_url, params=params, headers=headers, timeout=8)
        if resp.ok:
            data = resp.json()
            job_id = data.get("id")
            if not job_id:
                return None, "No job ID returned from Loader.to"
            
            # Poll progress up to 5 times (10 seconds total)
            progress_url = "https://api.loader.to/api/ajax/progress.php"
            for _ in range(5):
                time.sleep(2)
                prog_resp = requests.get(progress_url, params={"id": job_id}, headers=headers, timeout=5)
                if prog_resp.ok:
                    prog_data = prog_resp.json()
                    if prog_data.get("success") == 1 or prog_data.get("progress") >= 1000:
                        download_url = prog_data.get("download_url")
                        if download_url:
                            logger.info("Loader.to success")
                            title = prog_data.get("title", f"YouTube Video {video_id}")
                            filename = f"{title}.mp4"
                            return {
                                'media': [{
                                    'filename': filename,
                                    'size': '720p HD',
                                    'thumbnail': f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg",
                                    'dlink': download_url,
                                    'stream_url': f"/api/stream?url={quote_plus(download_url)}",
                                    'proxy_download': f"/api/download?url={quote_plus(download_url)}&filename={quote_plus(filename)}",
                                    'type': 'video',
                                    'quality': 'Video (720p)'
                                }]
                            }, None
                    elif prog_data.get("success") == 0 and prog_data.get("progress") == 0:
                        break
    except Exception as e:
        logger.warning(f"Loader.to failed: {e}")
        
    return None, "Loader.to extraction failed"

def extract_youtube_data(url):
    """Extract YouTube video with fallback strategy."""
    methods = [
        extract_youtube_with_cobalt,
        extract_youtube_with_invidious,
        extract_youtube_with_loader_to
    ]
    for method in methods:
        try:
            result, error = method(url)
            if result:
                return result, None
        except Exception as e:
            logger.warning(f"{method.__name__} error: {e}")
            continue
            
    return None, "Unable to download video from YouTube. Try another video link or check back later."

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
