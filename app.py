from flask import Flask, request, send_file, after_this_request, jsonify
import subprocess, os, tempfile, traceback, multiprocessing, json, textwrap, re
import requests as http_requests
from datetime import datetime

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
CPU_CORES    = str(multiprocessing.cpu_count())
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR    = os.path.join(BASE_DIR, "fonts")
os.makedirs(FONTS_DIR, exist_ok=True)

_FONT_CANDIDATES = [
    (os.path.join(FONTS_DIR, "Autumn_Regular.ttf"), "Autumn"),
    (os.path.join(BASE_DIR,  "Autumn_Regular.ttf"), "Autumn"),
    (os.path.join(FONTS_DIR, "Anton-Regular.ttf"),  "Anton"),
    (os.path.join(BASE_DIR,  "Anton-Regular.ttf"),  "Anton"),
]
FONT_PATH, FONT_NAME = next(
    ((p, n) for p, n in _FONT_CANDIDATES if os.path.exists(p)),
    ("", "Impact")
)

RESOLUTIONS = {
    "720x1280":  ("720",  "1280"),
    "1080x1080": ("1080", "1080"),
    "1280x720":  ("1280", "720"),
}

_MIME_MAP = {
    ".mp3": "audio/mpeg", ".mp4": "audio/mp4", ".m4a": "audio/mp4",
    ".wav": "audio/wav",  ".webm": "audio/webm", ".ogg": "audio/ogg",
    ".opus": "audio/ogg", ".oga": "audio/ogg",  ".flac": "audio/flac",
}
_EXT_AUD = set(_MIME_MAP.keys())

# ── helpers ──────────────────────────────────────────────────────────────────

def _esc_path(p):
    return p.replace("\\", "/").replace(":", "\\:")

def _esc(txt):
    return (txt.replace("\\","\\\\").replace("'","\\'")
               .replace(":","\\:").replace("[","\\[")
               .replace("]","\\]").replace(",","\\,"))

def _ts_ass(s):
    h=int(s//3600); m=int((s%3600)//60); sc=int(s%60); cs=int(round((s-int(s))*100))
    return f"{h}:{m:02d}:{sc:02d}.{cs:02d}"

def _mime_for(filename):
    return _MIME_MAP.get(os.path.splitext(filename)[1].lower(), "audio/mpeg")

# ── rotas estáticas ──────────────────────────────────────────────────────────

@app.route("/")
def index():
    return open(os.path.join(BASE_DIR, "index.html"), encoding="utf-8").read()

@app.route("/status")
def status():
    return jsonify({"groq": bool(GROQ_API_KEY), "font_name": FONT_NAME, "font_ok": bool(FONT_PATH)})

@app.route("/manifest.json")
def manifest():
    return send_file(os.path.join(BASE_DIR, "manifest.json"), mimetype="application/manifest+json")

@app.route("/service-worker.js")
def service_worker():
    resp = send_file(os.path.join(BASE_DIR, "sw.js"), mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp

@app.route("/static/<path:filename>")
def static_files(filename):
    return send_file(os.path.join(BASE_DIR, "static", filename))

@app.route("/healthz")
def healthz():
    return "OK", 200

# ── transcrição ──────────────────────────────────────────────────────────────

def _groq_transcrever(audio_bytes, filename):
    mime = _mime_for(filename)
    resp = http_requests.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        files={"file": (filename or "audio.mp3", audio_bytes, mime)},
        data={
            "model":                     "whisper-large-v3-turbo",
            "language":                  "pt",
            "response_format":           "verbose_json",
            "timestamp_granularities[]": "word",
        },
        timeout=120
    )
    if resp.status_code != 200:
        raise Exception(f"Groq {resp.status_code}: {resp.text}")
    data  = resp.json()
    texto = (data.get("text") or "").strip()
    segs  = [
        {"start": float(s.get("start",0)), "end": float(s.get("end",0)),
         "text": (s.get("text") or "").strip()}
        for s in (data.get("segments") or [])
    ]
    palavras = [
        {"word": (w.get("word") or "").strip(),
         "start": float(w.get("start",0)), "end": float(w.get("end",0))}
        for w in (data.get("words") or []) if (w.get("word") or "").strip()
    ]
    return texto, segs, palavras

@app.route("/transcrever", methods=["POST"])
def transcrever():
    if not GROQ_API_KEY:
        return jsonify({"erro": "GROQ_API_KEY não configurada."}), 400
    aud = request.files.get("audio")
    if not aud:
        return jsonify({"erro": "Nenhum áudio enviado."}), 400
    try:
        texto, segs, palavras = _groq_transcrever(aud.read(), aud.filename or "audio.mp3")
        return jsonify({"texto": texto, "segmentos": segs, "palavras": palavras})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

# ── geração de legenda ASS ───────────────────────────────────────────────────

def gerar_ass(dados, w, h, modo_dados="segmentos"):
    font_size = int(w * 0.074)
    margin_v  = int(h * 0.08)
    header = (
        f"[Script Info]\nScriptType: v4.00+\nPlayResX: {w}\nPlayResY: {h}\nWrapStyle: 0\n\n"
        f"[V4+ Styles]\n"
        f"Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        f"BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        f"BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Tron,{FONT_NAME},{font_size},&H00FFFFFF,&H88AAAAAA,&H00000000,"
        f"&HAA000000,-1,0,0,0,100,100,0,0,3,6,2,2,40,40,{margin_v},1\n\n"
        f"[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    if modo_dados == "palavras" and dados:
        for g in [dados[i:i+4] for i in range(0, len(dados), 4)]:
            if not g: continue
            start = g[0]["start"]; end = g[-1]["end"]
            if end <= start: end = start + 0.5
            partes = [
                f"{{\\k{max(1,int(round((pw['end']-pw['start'])*100)))}}}"
                f"{pw['word'].strip().replace('{','').replace('}','').replace(chr(92),'')}"
                for pw in g
            ]
            lines.append(f"Dialogue: 0,{_ts_ass(start)},{_ts_ass(end)},Tron,,0,0,0,,{' '.join(partes)}")
    else:
        for seg in dados:
            txt = seg.get("text","").strip()
            if not txt: continue
            if len(txt) > 35:
                txt = textwrap.fill(txt, width=35, max_lines=2, placeholder="...").replace("\n","\\N")
            txt = txt.replace("{","").replace("}","")
            lines.append(f"Dialogue: 0,{_ts_ass(seg['start'])},{_ts_ass(seg['end'])},Tron,,0,0,0,,{txt}")
    return header + "\n".join(lines)

# ── filtro de vídeo ──────────────────────────────────────────────────────────
# Imagem ESTÁTICA — sem Ken Burns, sem zoompan.
# Apenas escala para a resolução alvo + pad + (opcional) legenda drawtext.
# Resultado: conversão rápida, baixo uso de CPU/RAM.

OUTPUT_FPS = 1   # 1 fps é suficiente para foto estática; arquivo menor e bem mais rápido

def build_scale_vf(w: int, h: int) -> str:
    """Escala simples para resolução alvo com pad preto. Sem nenhum efeito."""
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1"
    )

def build_vf_com_legenda(w: int, h: int, legenda: str) -> str:
    """Escala + legenda via drawtext."""
    base = build_scale_vf(w, h)
    if not legenda.strip():
        return base
    txt      = _esc(legenda.strip())
    font_arg = f"fontfile='{_esc_path(FONT_PATH)}'" if os.path.exists(FONT_PATH) else "font=Impact"
    fs       = int(w * 0.072)
    mb       = int(h * 0.06)
    return (
        f"{base},"
        f"drawtext={font_arg}:text='{txt}':fontcolor=white:fontsize={fs}"
        f":bordercolor=black:borderw=5:shadowcolor=black@0.65:shadowx=2:shadowy=3"
        f":box=1:boxcolor=black@0.38:boxborderw=14"
        f":x=(w-text_w)/2:y=h-text_h-{mb}"
    )

# ── conversão principal ──────────────────────────────────────────────────────

@app.route("/converter", methods=["POST"])
def converter():
    img_file      = request.files.get("imagem")
    aud_file      = request.files.get("audio")
    resolucao     = request.form.get("resolucao",    "1080x1080")
    legenda_txt   = request.form.get("legenda",      "").strip()
    modo_leg      = request.form.get("modo_legenda", "nenhuma")
    palavras_json = request.form.get("palavras",     "")
    segs_json     = request.form.get("segmentos",    "")

    if not img_file or not aud_file:
        return "Imagem e áudio são obrigatórios.", 400

    w_str, h_str = RESOLUTIONS.get(resolucao, ("1080", "1080"))
    w, h = int(w_str), int(h_str)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            # salva arquivos recebidos
            img_ext = os.path.splitext(img_file.filename or "img.jpg")[1].lower() or ".jpg"
            aud_ext = os.path.splitext(aud_file.filename or "aud.mp3")[1].lower()
            if aud_ext not in _EXT_AUD: aud_ext = ".mp3"

            img_path = os.path.join(tmp, "img" + img_ext)
            aud_path = os.path.join(tmp, "aud" + aud_ext)
            with open(img_path, "wb") as f: f.write(img_file.read())
            with open(aud_path, "wb") as f: f.write(aud_file.read())

            fd, out_path = tempfile.mkstemp(suffix=".mp4")
            os.close(fd)

            # ── duração do áudio ──────────────────────────────────────────
            audio_duration = None
            try:
                probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", aud_path],
                    capture_output=True, text=True, timeout=30
                )
                audio_duration = float(probe.stdout.strip())
                print(f"[conv] duração: {audio_duration:.1f}s")
            except Exception as e:
                print(f"[conv] ffprobe falhou: {e}")

            # ── pré-redimensiona imagem (evita OOM com PNG 4K+) ───────────
            max_dim = max(w, h)
            img_resized = os.path.join(tmp, "img_r.jpg")
            try:
                r = subprocess.run([
                    "ffmpeg", "-y", "-i", img_path,
                    "-vf", (f"scale='if(gt(iw,{max_dim}),{max_dim},iw)':"
                            f"'if(gt(ih,{max_dim}),{max_dim},ih)'"
                            f":force_original_aspect_ratio=decrease"),
                    "-q:v", "2", img_resized
                ], capture_output=True, timeout=60)
                if r.returncode == 0 and os.path.getsize(img_resized) > 0:
                    img_path = img_resized
                    print(f"[conv] resize OK ({os.path.getsize(img_resized)//1024}KB)")
            except Exception as e:
                print(f"[conv] resize erro: {e}")

            # ── monta filtro de vídeo ─────────────────────────────────────
            vf       = build_scale_vf(w, h)   # base: estático
            ass_path = None

            if modo_leg == "auto":
                dados_ass  = None
                modo_dados = "segmentos"
                if palavras_json:
                    try:
                        p = json.loads(palavras_json)
                        if p: dados_ass = p; modo_dados = "palavras"
                    except Exception: pass
                if dados_ass is None and segs_json:
                    try: dados_ass = json.loads(segs_json)
                    except Exception: pass
                if dados_ass:
                    try:
                        ass_path = os.path.join(tmp, "leg.ass")
                        with open(ass_path, "w", encoding="utf-8") as f:
                            f.write(gerar_ass(dados_ass, w, h, modo_dados))
                        vf = f"{build_scale_vf(w,h)},ass='{_esc_path(ass_path)}'"
                        print("[conv] legenda .ass OK")
                    except Exception as e:
                        print(f"[conv] ass falhou: {e}")

            elif modo_leg == "estatica" and legenda_txt:
                vf = build_vf_com_legenda(w, h, legenda_txt)

            print(f"[conv] vf={vf[:120]}")

            # ── comando ffmpeg ────────────────────────────────────────────
            # -framerate 1 + -r 1: 1 fps → mínimo trabalho para foto estática
            # -tune stillimage: otimização x264 para quadros idênticos
            # -crf 28: qualidade ligeiramente melhor que 35, arquivo ainda pequeno
            # -preset ultrafast: encoding mais rápido possível
            cmd = [
                "ffmpeg", "-y",
                "-f", "image2", "-loop", "1", "-framerate", "1",
                "-i", img_path,
                "-i", aud_path,
                "-vf", vf,
                "-map", "0:v", "-map", "1:a",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "stillimage",
                "-crf", "28",
                "-r", "1",
                "-g", "2",
                "-pix_fmt", "yuv420p",
                "-threads", CPU_CORES,
                "-x264-params", "rc-lookahead=0:ref=1:bframes=0:weightp=0:vbv-maxrate=2000:vbv-bufsize=2000",
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                "-movflags", "+faststart",
                "-async", "1",
            ]
            if audio_duration:
                cmd += ["-t", str(audio_duration + 0.3)]
            else:
                cmd += ["-shortest"]
            cmd.append(out_path)

            timeout_s = min(300, max(60, int((audio_duration or 180) * 1.5)))
            print(f"[conv] ffmpeg timeout={timeout_s}s")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
            print(f"[conv] ffmpeg rc={result.returncode}")

        if result.returncode != 0:
            lines = result.stderr.splitlines()
            err   = [l for l in lines if any(k in l for k in
                      ("Error","error","Invalid","failed","Cannot","No such"))]
            return f"Erro FFmpeg:\n{chr(10).join(err[-15:]) or result.stderr[-1000:]}", 500

        @after_this_request
        def _cleanup(response):
            try: os.unlink(out_path)
            except Exception: pass
            return response

        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = (re.sub(r'[^\w\s-]', '', legenda_txt)[:40].strip().replace(' ','_')
                if legenda_txt else
                re.sub(r'[^\w\s-]', '', os.path.splitext(aud_file.filename or "tron")[0])[:40]
                   .strip().replace(' ','_'))
        download_name = f"{base or 'tron'}_{ts}.mp4"

        return send_file(out_path, mimetype="video/mp4",
                         as_attachment=True, download_name=download_name)

    except subprocess.TimeoutExpired:
        return "Tempo limite excedido. Tente com áudio mais curto ou resolução menor.", 504
    except Exception:
        return f"Erro interno:\n{traceback.format_exc()}", 500

# ── erro global ──────────────────────────────────────────────────────────────

@app.errorhandler(Exception)
def handle_exception(e):
    return f"<pre>{traceback.format_exc()}</pre>", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
