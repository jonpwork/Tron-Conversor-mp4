import os
import whisper
import uuid
from flask import Flask, request, jsonify, send_from_directory
from moviepy.editor import ImageClip, AudioFileClip, TextClip, CompositeVideoClip

app = Flask(__name__)

# Aqui está o motor! Para o Render Free não travar, o 'tiny' é o ideal e super rápido.
print("Iniciando motores de IA...")
model = whisper.load_model("tiny")

# O Render exige que arquivos temporários fiquem na pasta /tmp
TEMP_FOLDER = "/tmp/tron_files"
os.makedirs(TEMP_FOLDER, exist_ok=True)

@app.route('/')
def index():
    # Isso faz o Python servir aquela interface HTML linda que criamos
    return send_from_directory('.', 'index.html')

@app.route('/process', methods=['POST'])
def handle_media():
    try:
        # Gera um ID único para cada conversão para não misturar arquivos de usuários diferentes
        request_id = str(uuid.uuid4())
        audio_file = request.files.get('audio')
        image_file = request.files.get('image')
        action = request.form.get('action')

        if not audio_file:
            return jsonify({"error": "Áudio não enviado!"}), 400

        # Salvando o áudio na pasta temporária
        audio_path = os.path.join(TEMP_FOLDER, f"{request_id}_audio.mp3")
        audio_file.save(audio_path)

        # 1. MOTOR DE TRANSCRIÇÃO (Whisper)
        print("Transcrevendo áudio...")
        result = model.transcribe(audio_path)
        text = result['text']

        # Se o usuário clicou só para transcrever, devolve o texto e para por aqui
        if action == 'transcribe':
            return jsonify({"transcription": text})

        # 2. MOTOR DE VÍDEO (MoviePy)
        if image_file and action == 'convert':
            print("Gerando vídeo legendado...")
            img_path = os.path.join(TEMP_FOLDER, f"{request_id}_bg.jpg")
            image_file.save(img_path)
            
            # Carrega o áudio e a imagem
            audio_clip = AudioFileClip(audio_path)
            
            # OTIMIZAÇÃO EXTREMA: 1 FPS economiza 90% do processamento
            bg_clip = ImageClip(img_path).set_duration(audio_clip.duration).set_fps(1)
            
            # Criando a legenda que vai no meio do vídeo
            txt_clip = TextClip(text, fontsize=40, color='white', font='Arial', 
                                method='caption', size=(bg_clip.w*0.8, None))
            txt_clip = txt_clip.set_duration(audio_clip.duration).set_pos('center')
            
            # Junta tudo: Fundo + Legenda + Áudio
            video = CompositeVideoClip([bg_clip, txt_clip])
            video.audio = audio_clip
            
            output_name = f"{request_id}_tron.mp4"
            output_path = os.path.join(TEMP_FOLDER, output_name)
            
            # Renderizando o MP4 com todos os processadores (threads=4) e velocidade máxima (ultrafast)
            video.write_videofile(output_path, codec="libx264", audio_codec="aac", fps=1, threads=4, preset="ultrafast")
            
            # Devolve a URL para o botão de download automático funcionar lá no HTML
            return jsonify({
                "video_url": f"/download/{output_name}",
                "transcription": text
            })

    except Exception as e:
        print(f"Erro nos motores: {e}")
        return jsonify({"error": str(e)}), 500

# Rota especial para liberar o download do vídeo gerado
@app.route('/download/<filename>')
def download(filename):
    return send_from_directory(TEMP_FOLDER, filename)

if __name__ == '__main__':
    # Configuração de porta obrigatória para o Render conseguir ler a aplicação
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
