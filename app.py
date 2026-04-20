from flask import Flask, request, send_file, after_this_request, jsonify
import subprocess, os, tempfile, traceback, multiprocessing, json, textwrap
import requests as http_requests

app = Flask(__name__)

# --- CÓDIGO DETETIVE (Com logs para o Google Cloud) ---

@app.route("/converter", methods=["POST"])
def converter():
    print("DEBUG: [1] Iniciando rota /converter", flush=True)
    
    img_file = request.files.get("imagem")
    aud_file = request.files.get("audio")
    
    if not img_file or not aud_file:
        print("DEBUG: Erro - Arquivos ausentes", flush=True)
        return "Imagem e áudio são obrigatórios.", 400

    try:
        with tempfile.TemporaryDirectory() as tmp:
            print(f"DEBUG: [2] Pasta temporária criada: {tmp}", flush=True)
            
            img_path = os.path.join(tmp, "img.png")
            aud_path = os.path.join(tmp, "aud.mp3")
            
            img_file.save(img_path)
            aud_file.save(aud_path)
            print("DEBUG: [3] Arquivos salvos no disco temporário", flush=True)

            fd, out_path = tempfile.mkstemp(suffix=".mp4")
            os.close(fd)

            # Comando de teste simplificado para ver se o FFmpeg responde
            cmd = [
                "ffmpeg", "-y",
                "-f", "image2", "-loop", "1", "-i", img_path,
                "-i", aud_path,
                "-vf", "scale=1080:1080:force_original_aspect_ratio=decrease,pad=1080:1080:(ow-iw)/2:(oh-ih)/2:black",
                "-c:v", "libx264", "-preset", "ultrafast", "-t", "5", # Limitado a 5 segundos para teste
                "-pix_fmt", "yuv420p", "-c:a", "aac", out_path
            ]
            
            print(f"DEBUG: [4] Executando comando: {' '.join(cmd)}", flush=True)
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                print(f"DEBUG: [ERRO FFmpeg] {result.stderr}", flush=True)
                return f"Erro FFmpeg: {result.stderr}", 500
            
            print("DEBUG: [5] Sucesso! Vídeo gerado.", flush=True)
            return send_file(out_path, mimetype="video/mp4")

    except Exception as e:
        err = traceback.format_exc()
        print(f"DEBUG: [ERRO INTERNO] {err}", flush=True)
        return f"Erro interno: {err}", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
                
