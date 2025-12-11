from flask import Flask, request, jsonify, send_file, render_template
from yt_dlp import YoutubeDL
import tempfile
import os
import re
import threading
import time
import shutil
import uuid
from typing import Dict

app = Flask(__name__, static_folder='static', template_folder='templates')

# simple in-memory job store for background fetches
jobs: Dict[str, Dict] = {}


def is_youtube_url(url: str) -> bool:
    pattern = r'^(https?://)?(www\.)?(youtube\.com|youtu\.be)/'
    return bool(re.search(pattern, url))


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/info', methods=['POST'])
def info():
    data = request.get_json() or {}
    url = data.get('url')
    if not url:
        return jsonify({'error': 'Missing url'}), 400
    if not is_youtube_url(url):
        return jsonify({'error': 'Only YouTube URLs are supported'}), 400

    # quick synchronous info extraction (may be slow). Prefer using /api/submit for background fetch.
    ydl_opts = {'quiet': True, 'skip_download': True, 'noplaylist': True, 'socket_timeout': 10}
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    formats = []
    for f in info.get('formats', []):
        ext = f.get('ext')
        if ext not in ('mp4', 'webm', 'm4a', 'mp3'):
            continue
        formats.append({
            'format_id': f.get('format_id'),
            'ext': ext,
            'format_note': f.get('format_note'),
            'height': f.get('height'),
            'width': f.get('width'),
            'filesize': f.get('filesize') or f.get('filesize_approx'),
            'tbr': f.get('tbr'),
            'acodec': f.get('acodec'),
            'vcodec': f.get('vcodec'),
        })

    formats_sorted = sorted(formats, key=lambda x: (x.get('height') or 0, x.get('tbr') or 0), reverse=True)
    # include thumbnail and duration for better UI
    return jsonify({'title': info.get('title'), 'thumbnail': info.get('thumbnail'), 'duration': info.get('duration'), 'formats': formats_sorted})


@app.route('/api/submit', methods=['POST'])
def submit_job():
    data = request.get_json() or {}
    url = data.get('url')
    if not url:
        return jsonify({'error': 'Missing url'}), 400
    if not is_youtube_url(url):
        return jsonify({'error': 'Only YouTube URLs are supported'}), 400

    # allow cookies upload via multipart form: check files for 'cookies'
    job_id = str(uuid.uuid4())
    jobs[job_id] = {'status': 'pending', 'created': time.time(), 'result': None, 'error': None, 'cookiefile': None}

    # save uploaded cookies file if present
    if 'cookies' in request.files:
        cf = request.files['cookies']
        if cf and cf.filename:
            tmp = tempfile.mkdtemp(prefix='ytdl_cookies_')
            cookie_path = os.path.join(tmp, cf.filename)
            cf.save(cookie_path)
            jobs[job_id]['cookiefile'] = cookie_path

    def work(jobid, video_url):
        jobs[jobid]['status'] = 'running'
        try:
            ydl_opts = {'quiet': True, 'skip_download': True, 'noplaylist': True, 'socket_timeout': 10}
            # if cookies were uploaded, pass cookiefile to yt-dlp
            cookiefile = jobs[jobid].get('cookiefile')
            if cookiefile:
                ydl_opts['cookiefile'] = cookiefile

            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)

            formats = []
            for f in info.get('formats', []):
                ext = f.get('ext')
                if ext not in ('mp4', 'webm', 'm4a', 'mp3'):
                    continue
                formats.append({
                    'format_id': f.get('format_id'),
                    'ext': ext,
                    'format_note': f.get('format_note'),
                    'height': f.get('height'),
                    'width': f.get('width'),
                    'filesize': f.get('filesize') or f.get('filesize_approx'),
                    'tbr': f.get('tbr'),
                    'acodec': f.get('acodec'),
                    'vcodec': f.get('vcodec'),
                })

            formats_sorted = sorted(formats, key=lambda x: (x.get('height') or 0, x.get('tbr') or 0), reverse=True)
            jobs[jobid]['result'] = {'title': info.get('title'), 'thumbnail': info.get('thumbnail'), 'duration': info.get('duration'), 'formats': formats_sorted}
            jobs[jobid]['status'] = 'done'
        except Exception as e:
            jobs[jobid]['error'] = str(e)
            jobs[jobid]['status'] = 'error'

    t = threading.Thread(target=work, args=(job_id, url), daemon=True)
    t.start()

    return jsonify({'job_id': job_id}), 202


@app.route('/api/job/<job_id>', methods=['GET'])
def job_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify({'status': job['status'], 'result': job.get('result'), 'error': job.get('error')})


def _schedule_cleanup(path: str, delay: int = 30):
    def _cleanup():
        time.sleep(delay)
        try:
            if os.path.exists(path):
                os.remove(path)
            parent = os.path.dirname(path)
            # remove parent dir if empty
            if os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)
        except Exception:
            pass

    t = threading.Thread(target=_cleanup, daemon=True)
    t.start()


@app.route('/api/download', methods=['POST'])
def download():
    data = request.get_json() or {}
    url = data.get('url')
    format_id = data.get('format_id')
    # allow passing job_id so we can reuse uploaded cookies
    job_id = data.get('job_id')
    if not url or not format_id:
        return jsonify({'error': 'Missing parameters'}), 400
    if not is_youtube_url(url):
        return jsonify({'error': 'Only YouTube URLs are supported'}), 400

    tmpdir = tempfile.mkdtemp(prefix='ytdl_')
    outtmpl = os.path.join(tmpdir, '%(title)s.%(ext)s')
    ydl_opts = {'outtmpl': outtmpl, 'format': str(format_id), 'quiet': True}
    # reuse cookiefile from job if provided
    if job_id and job_id in jobs and jobs[job_id].get('cookiefile'):
        ydl_opts['cookiefile'] = jobs[job_id]['cookiefile']

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info)
            # send file as attachment
            resp = send_file(filepath, as_attachment=True)
            # schedule cleanup of the downloaded file
            _schedule_cleanup(filepath, delay=30)
            return resp
    except Exception as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    # Respect PORT env var when running on platforms like Render
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '').lower() in ('1', 'true', 'yes')
    app.run(host='0.0.0.0', port=port, debug=debug)
