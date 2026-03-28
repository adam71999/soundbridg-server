"""
SoundBridg Cloud Server
Uses Railway S3 bucket for persistent storage with graceful fallback.
"""

import os
import json
import time
import logging
from pathlib import Path
from flask import Flask, request, jsonify, send_file, abort, Response
from flask_cors import CORS
from werkzeug.utils import secure_filename
import mimetypes

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

API_KEY     = os.environ.get("API_KEY", "changeme")
MAX_FILE_MB = int(os.environ.get("MAX_FILE_MB", "500"))
ALLOWED_EXT = {".mp3", ".wav", ".flac", ".m4a", ".aac"}

# S3 config from Railway bucket variables
ENDPOINT    = os.environ.get("AWS_ENDPOINT_URL")
ACCESS_KEY  = os.environ.get("AWS_ACCESS_KEY_ID")
SECRET_KEY  = os.environ.get("AWS_SECRET_ACCESS_KEY")
REGION      = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
BUCKET      = os.environ.get("AWS_S3_BUCKET_NAME")

USE_S3 = all([ENDPOINT, ACCESS_KEY, SECRET_KEY, BUCKET])
log.info(f"Storage mode: {'S3 bucket' if USE_S3 else 'local /tmp'}")

# Local fallback
UPLOAD_FOLDER = Path("/tmp/uploads")
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
METADATA_FILE = UPLOAD_FOLDER / "_metadata.json"

def get_s3():
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        region_name=REGION,
    )

def load_meta():
    if USE_S3:
        try:
            s3  = get_s3()
            obj = s3.get_object(Bucket=BUCKET, Key="_metadata.json")
            return json.loads(obj["Body"].read())
        except Exception as e:
            log.warning(f"S3 meta load failed: {e}")
            return {}
    if METADATA_FILE.exists():
        with open(METADATA_FILE) as f:
            return json.load(f)
    return {}

def save_meta(meta):
    if USE_S3:
        try:
            get_s3().put_object(
                Bucket=BUCKET, Key="_metadata.json",
                Body=json.dumps(meta), ContentType="application/json"
            )
            return
        except Exception as e:
            log.warning(f"S3 meta save failed: {e}")
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
    return jsonify({"status": "ok", "tracks": len(meta), "storage": "s3" if USE_S3 else "local"})

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

    if USE_S3:
        try:
            mime, _ = mimetypes.guess_type(filename)
            get_s3().upload_fileobj(
                file, BUCKET, filename,
                ExtraArgs={"ContentType": mime or "application/octet-stream"}
            )
        except Exception as e:
            log.error(f"S3 upload failed: {e}")
            return jsonify({"error": "Upload failed"}), 500
    else:
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
        if not USE_S3 and not (UPLOAD_FOLDER / filename).exists():
            continue
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
        try:
            s3  = get_s3()
            rng = request.headers.get("Range")
            kw  = {"Range": rng} if rng else {}
            obj = s3.get_object(Bucket=BUCKET, Key=filename, **kw)
            status = 206 if rng else 200
            body   = obj["Body"]
            length = obj["ContentLength"]

            def stream():
                while True:
                    chunk = body.read(65536)
                    if not chunk: break
                    yield chunk

            resp = Response(stream(), status=status, mimetype=mime)
            resp.headers["Accept-Ranges"]  = "bytes"
            resp.headers["Content-Length"] = length
            if rng and "ContentRange" in obj:
                resp.headers["Content-Range"] = obj["ContentRange"]
            if request.args.get("dl") == "1":
                resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
            return resp
        except Exception as e:
            log.error(f"S3 serve failed: {e}")
            abort(404)
    else:
        fpath = UPLOAD_FOLDER / filename
        if not fpath.exists():
            abort(404)
        size = fpath.stat().st_size
        rng  = request.headers.get("Range")
        if rng:
            start  = int(rng.replace("bytes=", "").split("-")[0])
            end    = size - 1
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
    filename = secure_filename(filename)
    if USE_S3:
        try:
            get_s3().delete_object(Bucket=BUCKET, Key=filename)
        except Exception as e:
            log.warning(f"S3 delete failed: {e}")
    else:
        fpath = UPLOAD_FOLDER / filename
        if fpath.exists(): fpath.unlink()
    meta = load_meta()
    meta.pop(filename, None)
    save_meta(meta)
    return jsonify({"success": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
