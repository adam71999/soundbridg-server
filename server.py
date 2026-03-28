"""
SoundBridg Cloud Server
Simple file storage with persistent local uploads.
"""

import os
import json
import time
from pathlib import Path
from flask import Flask, request, jsonify, send_file, abort, Response
from flask_cors import CORS
from werkzeug.utils import secure_filename
import mimetypes

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

API_KEY      = os.environ.get("API_KEY", "changeme")
MAX_FILE_MB  = int(os.environ.get("MAX_FILE_MB", "500"))
ALLOWED_EXT  = {".mp3", ".wav", ".flac", ".m4a", ".aac"}

UPLOAD_FOLDER = Path(os.environ.get("UPLOAD_FOLDER", "/tmp/uploads"))
METADATA_FILE = UPLOAD_FOLDER / "_metadata.json"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

def load_meta():
    if METADATA_FILE.exists():
        with open(METADATA_FILE) as f:
            return json.load(f)
    return {}

def save_meta(meta):
    with open(METADATA_FILE, "w") as f:
        json.dump(meta, f, indent=2)

def require_key(f):
    from functools import wraps
    @wraps(f)
    def wrap(*args, **kwargs):
        key = request.headers.get("X-API-Key") or request.args.get("key")
        if key != API_KEY:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return wrap

@app.route("/")
def index():
    return app.send_static_file("index.html")

@app.route("/api/health")
def health():
    meta = load_meta()
    return jsonify({"status": "ok", "tracks": len(meta)})

@app.route("/api/upload", methods=["POST"])
@require_key
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    file = request.files["file"]
    ext  = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        return jsonify({"error": f"{ext} not allowed"}), 400
    file.seek(0, 2); size = file.tell(); file.seek(0)
    if size > MAX_FILE_MB * 1024 * 1024:
        return jsonify({"error": "File too large"}), 413
    filename = secure_filename(file.filename)
    meta = load_meta()
    stem, n = Path(filename).stem, 1
    while filename in meta:
        filename = f"{stem}_{n}{ext}"; n += 1
    file.save(UPLOAD_FOLDER / filename)
    meta[filename] = {
        "filename":     filename,
        "project_name": request.form.get("project_name", Path(filename).stem),
        "format":       request.form.get("format", ext.lstrip(".").upper()),
        "size":         size,
        "uploaded_at":  time.time(),
    }
    save_meta(meta)
    return jsonify({"success": True, "filename": filename})

@app.route("/api/tracks")
@require_key
def tracks():
    meta  = load_meta()
    key   = request.args.get("key")
    items = []
    for filename, info in meta.items():
        if (UPLOAD_FOLDER / filename).exists():
            items.append({
                **info,
                "url":          f"/api/files/{filename}?key={key}",
                "download_url": f"/api/files/{filename}?key={key}&dl=1",
            })
    items.sort(key=lambda x: x.get("uploaded_at", 0), reverse=True)
    return jsonify(items)

@app.route("/api/files/<filename>")
@require_key
def serve_file(filename):
    fpath = UPLOAD_FOLDER / secure_filename(filename)
    if not fpath.exists():
        abort(404)
    mime, _ = mimetypes.guess_type(str(fpath))
    mime = mime or "application/octet-stream"
    size = fpath.stat().st_size
    rng  = request.headers.get("Range")
    if rng:
        start = int(rng.replace("bytes=", "").split("-")[0])
        end   = size - 1
        length = end - start + 1
        def stream():
            with open(fpath, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk: break
                    remaining -= len(chunk)
                    yield chunk
        resp = Response(stream(), 206, mimetype=mime)
        resp.headers["Content-Range"]  = f"bytes {start}-{end}/{size}"
        resp.headers["Accept-Ranges"]  = "bytes"
        resp.headers["Content-Length"] = length
        return resp
    if request.args.get("dl") == "1":
        return send_file(fpath, mimetype=mime, as_attachment=True, download_name=filename)
    return send_file(fpath, mimetype=mime)

@app.route("/api/delete/<filename>", methods=["DELETE"])
@require_key
def delete(filename):
    fpath = UPLOAD_FOLDER / secure_filename(filename)
    if fpath.exists(): fpath.unlink()
    meta = load_meta()
    meta.pop(filename, None)
    save_meta(meta)
    return jsonify({"success": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
