import os, json, uuid, subprocess, threading
from datetime import datetime, date
from pathlib import Path
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, abort
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
ROOT = Path(__file__).resolve().parent
UPLOAD_DIR = ROOT / "uploads"
CLIP_DIR = ROOT / "clips"
UPLOAD_DIR.mkdir(exist_ok=True)
CLIP_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "CHANGE-ME-BEFORE-DEPLOYING"),
    SQLALCHEMY_DATABASE_URI="sqlite:///" + str(ROOT / "clip_premium.db"),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    MAX_CONTENT_LENGTH=2 * 1024 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "false").lower() == "true",
)
db = SQLAlchemy(app)
csrf = CSRFProtect(app)

ALLOWED = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
processing_lock = threading.Lock()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(30), default="queued")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    error = db.Column(db.Text)

class Clip(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    video_id = db.Column(db.Integer, db.ForeignKey("video.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    title = db.Column(db.String(180), nullable=False)
    reason = db.Column(db.Text)
    start_seconds = db.Column(db.Float, nullable=False)
    end_seconds = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    video = db.relationship("Video", backref="clips")

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def init_db():
    with app.app_context():
        db.create_all()
        username = os.environ.get("ADMIN_USERNAME", "owner")
        password = os.environ.get("ADMIN_PASSWORD")
        if password and not User.query.filter_by(username=username).first():
            db.session.add(User(username=username, password_hash=generate_password_hash(password)))
            db.session.commit()

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(username=request.form.get("username", "").strip()).first()
        if user and check_password_hash(user.password_hash, request.form.get("password", "")):
            session.clear()
            session["user_id"] = user.id
            session.permanent = True
            return redirect(url_for("dashboard"))
        flash("Usuario o contraseña incorrectos.", "error")
    return render_template("login.html")

@app.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.get("/")
@login_required
def dashboard():
    videos = Video.query.order_by(Video.created_at.desc()).all()
    clips = Clip.query.order_by(Clip.created_at.desc()).limit(40).all()
    today_count = Clip.query.filter(db.func.date(Clip.created_at) == date.today().isoformat()).count()
    return render_template("dashboard.html", videos=videos, clips=clips, today_count=today_count)

@app.post("/upload")
@login_required
def upload():
    f = request.files.get("video")
    if not f or not f.filename:
        flash("Selecciona un archivo de vídeo.", "error")
        return redirect(url_for("dashboard"))
    original = secure_filename(f.filename)
    ext = Path(original).suffix.lower()
    if ext not in ALLOWED:
        flash("Formato no admitido. Usa MP4, MOV, MKV o WEBM.", "error")
        return redirect(url_for("dashboard"))
    stored = f"{uuid.uuid4().hex}{ext}"
    f.save(UPLOAD_DIR / stored)
    db.session.add(Video(filename=stored, original_name=original))
    db.session.commit()
    flash("Vídeo añadido a la cola. Se analizará automáticamente.", "success")
    return redirect(url_for("dashboard"))

@app.post("/process/<int:video_id>")
@login_required
def process_one(video_id):
    threading.Thread(target=process_video_safe, args=(video_id,), daemon=True).start()
    flash("Análisis iniciado. Actualiza la página en unos minutos.", "success")
    return redirect(url_for("dashboard"))

@app.get("/clips/<path:filename>")
@login_required
def get_clip(filename):
    return send_from_directory(CLIP_DIR, filename, as_attachment=True)

def run_cmd(args, timeout=3600):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or "Error de FFmpeg")[-1800:])
    return p.stdout

def video_duration(path):
    out = run_cmd(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                   "-of", "default=noprint_wrappers=1:nokey=1", str(path)], timeout=120)
    return float(out.strip())

def get_highlights(transcript, duration):
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("Falta OPENAI_API_KEY en el archivo .env.")
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    prompt = f"""
Analiza esta transcripción con marcas de tiempo y selecciona momentos especialmente interesantes,
claros o entretenidos que funcionen como clips independientes para redes sociales.
Devuelve SOLO JSON: {{"clips":[{{"start":12.5,"end":48.0,"title":"...","reason":"..."}}]}}
Reglas: máximo 10 momentos; duración de cada clip entre 20 y 60 segundos;
start/end deben estar dentro de 0 y {duration:.1f}; evita solapamientos;
prioriza momentos con contexto y remate; no inventes contenido.
Transcripción:
{transcript[:90000]}
"""
    response = client.chat.completions.create(
        model=os.environ.get("AI_TEXT_MODEL", "gpt-4o-mini"),
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "Eres un editor profesional de clips. Responde JSON válido."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
    )
    data = json.loads(response.choices[0].message.content)
    return data.get("clips", [])

def process_video_safe(video_id):
    if not processing_lock.acquire(blocking=False):
        return
    try:
        with app.app_context():
            video = db.session.get(Video, video_id)
            if not video or video.status == "done":
                return
            video.status = "processing"
            video.error = None
            db.session.commit()
            source = UPLOAD_DIR / video.filename
            audio = UPLOAD_DIR / f"{video_id}_audio.mp3"
            try:
                duration = video_duration(source)
                run_cmd(["ffmpeg", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
                         "-b:a", "64k", str(audio)], timeout=3600)
                client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
                with open(audio, "rb") as af:
                    transcription = client.audio.transcriptions.create(
                        model=os.environ.get("AI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe"),
                        file=af,
                        response_format="verbose_json",
                        timestamp_granularities=["segment"],
                    )
                segments = getattr(transcription, "segments", None) or []
                transcript = "\n".join(
                    f"[{float(s.start):.1f}-{float(s.end):.1f}] {s.text}" for s in segments
                )
                if not transcript:
                    transcript = getattr(transcription, "text", "")
                moments = get_highlights(transcript, duration)
                made = 0
                daily_remaining = max(0, 10 - Clip.query.filter(
                    db.func.date(Clip.created_at) == date.today().isoformat()).count())
                for i, m in enumerate(moments):
                    if made >= daily_remaining:
                        break
                    start = max(0.0, float(m.get("start", 0)))
                    end = min(duration, float(m.get("end", 0)))
                    if end <= start or end - start < 8:
                        continue
                    outname = f"clip_{video.id}_{uuid.uuid4().hex[:8]}.mp4"
                    outpath = CLIP_DIR / outname
                    run_cmd(["ffmpeg", "-y", "-ss", str(start), "-i", str(source),
                             "-t", str(end-start), "-c:v", "libx264", "-preset", "veryfast",
                             "-crf", "23", "-c:a", "aac", "-movflags", "+faststart", str(outpath)],
                            timeout=1800)
                    db.session.add(Clip(video_id=video.id, filename=outname,
                        title=str(m.get("title", f"Momento destacado {i+1}"))[:180],
                        reason=str(m.get("reason", ""))[:1000], start_seconds=start, end_seconds=end))
                    db.session.commit()
                    made += 1
                video.status = "done"
                db.session.commit()
            except Exception as e:
                video.status = "error"
                video.error = str(e)[:3000]
                db.session.commit()
            finally:
                if audio.exists():
                    audio.unlink(missing_ok=True)
    finally:
        processing_lock.release()

def daily_job():
    with app.app_context():
        remaining = max(0, 10 - Clip.query.filter(
            db.func.date(Clip.created_at) == date.today().isoformat()).count())
        if remaining <= 0:
            return
        queued = Video.query.filter_by(status="queued").order_by(Video.created_at.asc()).all()
        for v in queued:
            if remaining <= 0:
                break
            process_video_safe(v.id)
            remaining = max(0, 10 - Clip.query.filter(
                db.func.date(Clip.created_at) == date.today().isoformat()).count())

if __name__ == "__main__":
    init_db()
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler(timezone=os.environ.get("TIMEZONE", "America/Tegucigalpa"))
    scheduler.add_job(daily_job, "cron", hour=3, minute=0, id="daily_clips", replace_existing=True)
    scheduler.start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
