import os
import whisper
import uuid
from flask import Flask, request, jsonify, send_from_directory
from moviepy.editor import ImageClip, AudioFileClip, TextClip, CompositeVideoClip

app = Flask(__name__)

# Carregar modelo (no Render use 'base' ou 'tiny' se a RAM for pouca)
model = whisper.load_model("base")

TEMP_FOLDER = "/tmp/tron_files"
os.makedirs(TEMP_FOLDER, exist_ok=True)

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/process', methods=['POST'])
def handle_media():
    request_id = str(uuid.uuid4())
    audio_file = request.files['audio']
    image_file = request.files.get('image')
    action = request.form['action']

    audio_path = os.path.join(TEMP_FOLDER, f"{request_id}_audio.mp3")
    audio_file.save(audio_path)

    # Motor de Transcrição
    result = model.transcribe(audio_path)
    text = result['text']

    if action == 'transcribe':
        return jsonify({"transcription": text})

    # Motor de Vídeo Legendado
    if image_file:
        img_path = os.path.join(TEMP_FOLDER, f"{request_id}_bg.jpg")
        image_file.save(img_path)
        
        audio_clip = AudioFileClip(audio_path)
        # Otimização extrema: 1 FPS e Threads
        bg_clip = ImageClip(img_path).set_duration(audio_clip.duration).set_fps(1)
        
        # Legenda Caprichada
        txt_clip = TextClip(text, fontsize=40, color='white', font='Arial', 
                            method='caption', size=(bg_clip.w*0.8, None)).set_duration(audio_clip.duration).set_pos('center')
        
        video = CompositeVideoClip([bg_clip, txt_clip])
        video.audio = audio_clip
        
        output_name = f"{request_id}_tron.mp4"
        output_path = os.path.join(TEMP_FOLDER, output_name)
        
        # O segredo da velocidade: libx264 rápido + threads
        video.write_videofile(output_path, codec="libx264", audio_codec="aac", fps=1, threads=4, preset="ultrafast")
        
        return jsonify({
            "video_url": f"/download/{output_name}",
            "transcription": text
        })

@app.route('/download/<filename>')
def download(filename):
    return send_from_directory(TEMP_FOLDER, filename)

if __name__ == '__main__':
    # No Render, ele usa a variável PORT
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
