"""
SoundBridg Cloud Server — S3 Bucket Edition
Uses Railway's S3-compatible bucket for persistent file storage.
"""

import os
import json
import time
import boto3
from pathlib import Path
from flask import Flask, request, jsonify, send_file, abort, Response
from flask_cors import CORS
from werkzeug.utils import secure_filename
import mimetypes
import tempfile

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

API_KEY     = os.environ.get("API_KEY", "changeme")
MAX_FILE_MB = int(os.environ.get("MAX_FILE_MB", "500"))
ALLOWED_EXT = {".mp3", ".wav", ".flac", ".m4a", ".aac"}

# S3 bucket config from Railway environment variables
AWS_ENDPOINT_URL   = os.environ.get("AWS_ENDPOINT_URL")
AWS_ACCESS_KEY_ID  = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_DEFAULT_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
S3_BUCKET_NAME     = os.environ.get("AWS_S3_BUCKET_NAME")

# Fallback to local storage if no S3 configured
USE_S3 = all([AWS_ENDPOINT_URL, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_BUCKET_NAME])

# Local fallback
UPLOAD_FOLDER  = Path(os.environ.get("UPLOAD_FOLDER", "/data/uploads"))
METADATA_FILE  = UPLOAD_FOLDER / "_metadata.json"
if not USE_S3:
    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

def get_s3():
    return boto3.client(
        "s3",
        endpoint_url=AWS_ENDPOINT_URL,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=AWS_DEFAULT_REGION,
    )

# ── Metadata ──────────────────────────────────────────────────────────────────

METADATA_S3_KEY = "_metadata.json"

def load_meta():
    if USE_S3:
        try:
            s3 = get_s3()
            obj = s3.get_object(Bucket=S3_BUCKET_NAME, Key=METADATA_S3_KEY)
            return json.loads(obj["Body"].read())
        except Exception:
            return {}
    else:
        if METADATA_FILE.exists():
            with open(METADATA_FILE) as f:
                return json.load(f)
        return {}

def save_meta(meta):
    if USE_S3:
        s3 = get_s3()
        s3.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=METADATA_S3_KEY,
            Body=json.dumps(meta, indent=2),
            ContentType="application/json",
        )
    else:
        with open(METADATA_FILE, "w") as f:
            json.dump(meta, f, indent=2)

# ── Auth ──────────────────────────────────────────────────────────────────────

def require_key(f):
    from functools import wraps
    @wraps(f)
    def wrap(*args, **kwargs):
        key = request.headers.get("X-API-Key") or request.args.get("key")
        if key != API_KEY:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return wrap

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return app.send_static_file("index.html")

@app.route("/api/health")
def health():
    meta = load_meta()
    return jsonify({
        "status": "ok",
        "tracks": len(meta),
        "storage": "s3" if USE_S3 else "local",
    })

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
    meta     = load_meta()

    # Deduplicate
    stem, n = Path(filename).stem, 1
    while filename in meta:
        filename = f"{stem}_{n}{ext}"; n += 1

    project_name = request.form.get("project_name", Path(filename).stem)
    format_type  = request.form.get("format", ext.lstrip(".").upper())

    if USE_S3:
        s3 = get_s3()
        mime, _ = mimetypes.guess_type(filename)
        mime = mime or "application/octet-stream"
        s3.upload_fileobj(
            file,
            S3_BUCKET_NAME,
            filename,
            ExtraArgs={"ContentType": mime},
        )
    else:
        file.save(UPLOAD_FOLDER / filename)

    meta[filename] = {
        "filename":     filename,
        "project_name": project_name,
        "format":       format_type,
        "size":         size,
        "uploaded_at":  time.time(),
    }
    save_meta(meta)
    return jsonify({"success": True, "filename": filename})

@app.route("/api/tracks")
@require_key
def tracks():
    meta = load_meta()
    key  = request.args.get("key")
    items = []
    for filename, info in meta.items():
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
    filename = secure_filename(filename)
    mime, _  = mimetypes.guess_type(filename)
    mime     = mime or "application/octet-stream"

    if USE_S3:
        s3 = get_s3()
        # Stream from S3
        range_header = request.headers.get("Range")
        extra = {}
        if range_header:
            extra["Range"] = range_header

        try:
            obj = s3.get_object(Bucket=S3_BUCKET_NAME, Key=filename, **extra)
        except Exception:
            abort(404)

        body   = obj["Body"]
        length = obj["ContentLength"]
        status = 206 if range_header else 200

        def generate():
            while True:
                chunk = body.read(65536)
                if not chunk:
                    break
                yield chunk

        resp = Response(generate(), status=status, mimetype=mime)
        resp.headers["Accept-Ranges"] = "bytes"
        resp.headers["Content-Length"] = length
        if range_header and "ContentRange" in obj:
            resp.headers["Content-Range"] = obj["ContentRange"]
        if request.args.get("dl") == "1":
            resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return resp
    else:
        fpath = UPLOAD_FOLDER / filename
        if not fpath.exists():
            abort(404)
        return send_file(
            fpath, mimetype=mime,
            as_attachment=request.args.get("dl") == "1",
            download_name=filename,
        )

@app.route("/api/delete/<filename>", methods=["DELETE"])
@require_key
def delete(filename):
    filename = secure_filename(filename)
    if USE_S3:
        try:
            get_s3().delete_object(Bucket=S3_BUCKET_NAME, Key=filename)
        except Exception:
            pass
    else:
        fpath = UPLOAD_FOLDER / filename
        if fpath.exists():
            fpath.unlink()
    meta = load_meta()
    meta.pop(filename, None)
    save_meta(meta)
    return jsonify({"success": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
