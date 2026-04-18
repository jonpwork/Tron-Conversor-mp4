import os
import uuid
from flask import Flask, request, jsonify, send_from_directory
from groq import Groq
from moviepy.editor import ImageClip, AudioFileClip, TextClip, CompositeVideoClip

app = Flask(__name__)

# Inicializa o cliente Groq pegando a Key do Render automaticamente
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

TEMP_FOLDER = "/tmp/tron_files"
os.makedirs(TEMP_FOLDER, exist_ok=True)

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/process', methods=['POST'])
def handle_media():
    try:
        request_id = str(uuid.uuid4())
        audio_file = request.files.get('audio')
        image_file = request.files.get('image')
        action = request.form.get('action')

        if not audio_file:
            return jsonify({"error": "Áudio não enviado!"}), 400

        audio_path = os.path.join(TEMP_FOLDER, f"{request_id}_audio.mp3")
        audio_file.save(audio_path)

        # MOTOR DE TRANSCRIÇÃO (Aqui a mágica do Groq acontece!)
        print("Enviando para o Groq... transcrição relâmpago!")
        with open(audio_path, "rb") as file:
            transcription = client.audio.transcriptions.create(
                file=(audio_path, file.read()),
                model="whisper-large-v3", # O modelo mais potente do mundo, agora de graça pra você
                response_format="text",
            )
        
        text = transcription

        if action == 'transcribe':
            return jsonify({"transcription": text})

        # MOTOR DE VÍDEO
        if image_file and action == 'convert':
            print("Gerando vídeo legendado...")
            img_path = os.path.join(TEMP_FOLDER, f"{request_id}_bg.jpg")
            image_file.save(img_path)
            
            audio_clip = AudioFileClip(audio_path)
            bg_clip = ImageClip(img_path).set_duration(audio_clip.duration).set_fps(1)
            
            txt_clip = TextClip(text, fontsize=40, color='white', font='Arial', 
                                method='caption', size=(bg_clip.w*0.8, None))
            txt_clip = txt_clip.set_duration(audio_clip.duration).set_pos('center')
            
            video = CompositeVideoClip([bg_clip, txt_clip])
            video.audio = audio_clip
            
            output_name = f"{request_id}_tron.mp4"
            output_path = os.path.join(TEMP_FOLDER, output_name)
            
            video.write_videofile(output_path, codec="libx264", audio_codec="aac", fps=1, threads=4, preset="ultrafast")
            
            return jsonify({
                "video_url": f"/download/{output_name}",
                "transcription": text
            })

    except Exception as e:
        print(f"Erro nos motores: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/download/<filename>')
def download(filename):
    return send_from_directory(TEMP_FOLDER, filename)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
    
