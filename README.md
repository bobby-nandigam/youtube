# YouTube Downloader (Mobile-first)

Quick local Flask app that lists available formats for a YouTube URL and lets you download selected format.

Setup (Windows PowerShell):

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:FLASK_APP = 'app.py'
flask run --host=0.0.0.0 --port=5000
```

Open http://localhost:5000 on your mobile device (or browser).

Notes:
- Uses `yt-dlp` Python API to list formats and download.
- Downloads are written to a temporary folder and served back; files are scheduled for cleanup.
- This is a development setup. For production, run behind a proper WSGI server and add rate-limiting, input validation and storage cleanup.
