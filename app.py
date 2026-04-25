from flask import Flask, request, send_file, after_this_request, jsonify
import subprocess, os, tempfile, traceback, multiprocessing, json, textwrap
import requests as http_requests

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

# --- FUNÇÕES DE AUXÍLIO PARA FFmpeg ---

def _esc_path(p):
    """Escapa caminhos para filtros do FFmpeg (importante para Windows/Render)"""
    return p.replace("\\", "/").replace(":", "\\:")

def _esc(txt):
    return txt.replace("\\","\\\\").replace("'","\\'").replace(":","\\:").replace("[","\\[").replace("]","\\]").replace(",","\\,")

def _ts_ass(s):
    h=int(s//3600); m=int((s%3600)//60); sc=int(s%60); cs=int(round((s-int(s))*100))
    return f"{h}:{m:02d}:{sc:02d}.{cs:02d}"


# ---------------------------------------------------------------------------
# Ken Burns effect (zoom lento + leve pan diagonal)
# ---------------------------------------------------------------------------
# Estratégia:
#   • FPS de saída = 10  — suave o suficiente para movimento, muito mais leve
#     que 25fps para uma imagem estática.
#   • zoompan nativo do FFmpeg: sem dependências externas.
#   • Zoom de 1.0 → 1.06 (6 %) ao longo de toda a duração.
#   • Pan diagonal suave (canto superior-esquerdo → centro), dá sensação de
#     movimento sem cortar rosto/texto importante.
#   • Fallback seguro: se a duração do áudio não for conhecida, usa 180 s.
# ---------------------------------------------------------------------------
OUTPUT_FPS = 5    # 5fps — ideal para imagem estática: suave visualmente, ultra-leve

def build_motion_vf(w: int, h: int, audio_duration: float | None) -> str:
    """
    Ken Burns ultra-rapido a 5fps.
    5fps x 216s = 1080 frames apenas. Zoom 15% claramente visivel.
    scale+eval=frame: paralelo, sem overhead do zoompan.
    """
    dur = max(1, float(audio_duration) if audio_duration else 180.0)
    zoom_max = 1.15   # 15% zoom - bem visivel

    zoom_expr_w = f"iw*({zoom_max}-(({zoom_max}-1)*({dur}-t)/{dur}))"
    zoom_expr_h = f"ih*({zoom_max}-(({zoom_max}-1)*({dur}-t)/{dur}))"

    return (
        f"scale={w*2}:{h*2}:force_original_aspect_ratio=increase,"
        f"crop={w*2}:{h*2},"
        f"scale={zoom_expr_w}:{zoom_expr_h}:eval=frame,"
        f"crop={w}:{h},"
        f"setsar=1"
    )


# --- ROTAS E LÓGICA ---

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
    resp = send_file(os.path.join(BASE_DIR, "service-worker.js"), mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp

@app.route("/static/<path:filename>")
def static_files(filename):
    return send_file(os.path.join(BASE_DIR, "static", filename))

_MIME_MAP = {
    ".mp3": "audio/mpeg", ".mp4": "audio/mp4", ".m4a": "audio/mp4",
    ".wav": "audio/wav",  ".webm": "audio/webm", ".ogg": "audio/ogg",
    ".opus": "audio/ogg", ".oga": "audio/ogg",  ".flac": "audio/flac",
}

def _mime_for(filename):
    ext = os.path.splitext(filename)[1].lower()
    return _MIME_MAP.get(ext, "audio/mpeg")

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
    data     = resp.json()
    texto    = (data.get("text") or "").strip()
    segs     = [{"start": float(s.get("start",0)), "end": float(s.get("end",0)), "text": (s.get("text") or "").strip()} for s in (data.get("segments") or [])]
    palavras = [{"word": (w.get("word") or "").strip(), "start": float(w.get("start",0)), "end": float(w.get("end",0))} for w in (data.get("words") or []) if (w.get("word") or "").strip()]
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

def gerar_ass(dados, w, h, modo_dados="segmentos"):
    font_size = int(w * 0.074)
    margin_v  = int(h * 0.08)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Tron,{FONT_NAME},{font_size},&H00FFFFFF,&H88AAAAAA,&H00000000,&HAA000000,-1,0,0,0,100,100,0,0,3,6,2,2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if modo_dados == "palavras" and dados:
        for g in [dados[i:i+4] for i in range(0, len(dados), 4)]:
            if not g: continue
            start=g[0]["start"]; end=g[-1]["end"]
            if end<=start: end=start+0.5
            partes=[f"{{\\k{max(1,int(round((pw['end']-pw['start'])*100)))}}}{pw['word'].strip().replace('{','').replace('}','').replace(chr(92),'')}" for pw in g]
            lines.append(f"Dialogue: 0,{_ts_ass(start)},{_ts_ass(end)},Tron,,0,0,0,,{' '.join(partes)}")
    else:
        for seg in dados:
            txt=seg.get("text","").strip()
            if not txt: continue
            if len(txt)>35: txt=textwrap.fill(txt,width=35,max_lines=2,placeholder="...").replace("\n","\\N")
            txt=txt.replace("{","").replace("}","")
            lines.append(f"Dialogue: 0,{_ts_ass(seg['start'])},{_ts_ass(seg['end'])},Tron,,0,0,0,,{txt}")
    return header + "\n".join(lines)

def build_vf_estatico(w, h, legenda, audio_duration=None):
    """Versão com Ken Burns + legenda estática via drawtext."""
    motion = build_motion_vf(w, h, audio_duration)

    if not legenda.strip():
        return motion

    txt = _esc(legenda.strip())
    fpath_esc = _esc_path(FONT_PATH)
    font = f"fontfile='{fpath_esc}'" if os.path.exists(FONT_PATH) else "font=Impact"
    fs   = int(w * 0.072)
    mb   = int(h * 0.06)

    return (
        f"{motion},"
        f"drawtext={font}:text='{txt}':fontcolor=white:fontsize={fs}"
        f":bordercolor=black:borderw=5:shadowcolor=black@0.65:shadowx=2:shadowy=3"
        f":box=1:boxcolor=black@0.38:boxborderw=14"
        f":x=(w-text_w)/2:y=h-text_h-{mb}"
    )

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

    _EXT_AUD = {".mp3",".mp4",".m4a",".wav",".webm",".ogg",".opus",".oga",".flac"}

    try:
        with tempfile.TemporaryDirectory() as tmp:
            img_ext = os.path.splitext(img_file.filename or "img.jpg")[1].lower() or ".jpg"
            aud_ext = os.path.splitext(aud_file.filename or "aud.mp3")[1].lower()
            if aud_ext not in _EXT_AUD: aud_ext = ".mp3"

            img_path = os.path.join(tmp, "img" + img_ext)
            aud_path = os.path.join(tmp, "aud" + aud_ext)

            with open(img_path, "wb") as f:
                f.write(img_file.read())
            with open(aud_path, "wb") as f:
                f.write(aud_file.read())

            fd, out_path = tempfile.mkstemp(suffix=".mp4")
            os.close(fd)

            # ---------------------------------------------------------------
            # Duração real do áudio (necessária para calcular frames do zoom)
            # ---------------------------------------------------------------
            audio_duration = None
            try:
                probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", aud_path],
                    capture_output=True, text=True, timeout=30
                )
                audio_duration = float(probe.stdout.strip())
                print(f"DEBUG: [4] Duração do áudio: {audio_duration:.1f}s")
            except Exception as e:
                print(f"DEBUG: [4] ffprobe falhou ({e}), usando fallback 180s para o zoom")

            # ---------------------------------------------------------------
            # Pré-redimensiona imagem para evitar OOM com PNGs 4K+
            # Usa 2× a resolução alvo pois o zoompan precisa de espaço extra
            # ---------------------------------------------------------------
            max_dim = max(w, h) * 2
            img_resized = os.path.join(tmp, "img_resized.jpg")
            try:
                resize_cmd = [
                    "ffmpeg", "-y", "-i", img_path,
                    "-vf", (
                        f"scale='if(gt(iw,{max_dim}),{max_dim},iw)':"
                        f"'if(gt(ih,{max_dim}),{max_dim},ih)'"
                        f":force_original_aspect_ratio=decrease"
                    ),
                    "-q:v", "2",
                    img_resized
                ]
                r = subprocess.run(resize_cmd, capture_output=True, timeout=60)
                if r.returncode == 0 and os.path.exists(img_resized) and os.path.getsize(img_resized) > 0:
                    img_path = img_resized
                    print(f"DEBUG: [5a] imagem pré-redimensionada OK ({os.path.getsize(img_resized)//1024}KB)")
                else:
                    print(f"DEBUG: [5a] resize falhou (rc={r.returncode}), usando original")
            except Exception as e:
                print(f"DEBUG: [5a] resize erro ({e}), usando original")

            # ---------------------------------------------------------------
            # Monta filtro de vídeo:
            #   • Sempre começa com Ken Burns (motion)
            #   • Adiciona legenda .ass por cima se modo_leg == "auto"
            #   • Ou drawtext se modo_leg == "estatica"
            # ---------------------------------------------------------------
            motion_vf = build_motion_vf(w, h, audio_duration)
            vf        = motion_vf   # base: sempre tem movimento
            ass_path  = None

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
                        ass_path_esc = _esc_path(ass_path)
                        # Aplica Ken Burns primeiro, depois sobrepõe a legenda .ass
                        vf = f"{motion_vf},ass='{ass_path_esc}'"
                        print("DEBUG: [ass] Ken Burns + legenda .ass OK")
                    except Exception as e:
                        print(f"DEBUG: [ass] falhou ({e}), Ken Burns sem legenda")
                        ass_path = None

            elif modo_leg == "estatica" and legenda_txt:
                vf = build_vf_estatico(w, h, legenda_txt, audio_duration)

            print(f"DEBUG: [5] vf={vf[:160]}")

            # ---------------------------------------------------------------
            # Comando FFmpeg
            # Nota: -framerate do input pode ser baixo (a imagem é estática),
            # mas o OUTPUT_FPS definido no zoompan dita o FPS real de saída.
            # ---------------------------------------------------------------
            cmd = [
                "ffmpeg", "-y",
                "-f", "image2",
                "-loop", "1",
                "-framerate", str(OUTPUT_FPS),
                "-i", img_path,
                "-i", aud_path,
                "-vf", vf,
                "-map", "0:v", "-map", "1:a",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "stillimage",
                "-crf", "35",
                "-r", str(OUTPUT_FPS),
                "-g", str(OUTPUT_FPS * 5),
                "-pix_fmt", "yuv420p",
                "-threads", CPU_CORES,
                "-x264-params", "rc-lookahead=0:ref=1:bframes=0:weightp=0:vbv-maxrate=1000:vbv-bufsize=1000",
                "-c:a", "aac", "-b:a", "96k", "-ar", "44100",
                "-movflags", "+faststart",
                "-async", "1",
            ]

            if audio_duration:
                cmd += ["-t", str(audio_duration + 0.5)]
            else:
                cmd += ["-shortest"]

            cmd.append(out_path)

            timeout_s = min(600, max(120, int((audio_duration or 300) * 2)))
            print(f"DEBUG: [6] Rodando ffmpeg, timeout={timeout_s}s")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
            print(f"DEBUG: [7] ffmpeg retornou código {result.returncode}")

        if result.returncode != 0:
            lines = result.stderr.splitlines()
            err = [l for l in lines if any(k in l for k in ("Error","error","Invalid","failed","Cannot","No such"))]
            return f"Erro FFmpeg:\n{chr(10).join(err[-15:]) or result.stderr[-1000:]}", 500

        @after_this_request
        def _cleanup(response):
            try: os.unlink(out_path)
            except Exception: pass
            return response

        import re
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if legenda_txt:
            base = re.sub(r'[^\w\s-]', '', legenda_txt)[:40].strip().replace(' ', '_')
        elif aud_file and aud_file.filename:
            base = re.sub(r'[^\w\s-]', '', os.path.splitext(aud_file.filename)[0])[:40].strip().replace(' ', '_')
        else:
            base = "tron"
        download_name = f"{base}_{ts}.mp4" if base else f"tron_{ts}.mp4"

        return send_file(out_path, mimetype="video/mp4", as_attachment=True, download_name=download_name)

    except subprocess.TimeoutExpired:
        return "Tempo limite excedido. Tente com um áudio mais curto ou resolução menor.", 504
    except Exception:
        return f"Erro interno:\n{traceback.format_exc()}", 500

# --- ROTAS DE SAÚDE E ERRO ---

@app.route("/healthz")
def healthz():
    return "OK", 200

@app.errorhandler(Exception)
def handle_exception(e):
    return f"<pre>{traceback.format_exc()}</pre>", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
