# CLIP PREMIUM — MVP privado

Aplicación web privada para subir vídeos autorizados, transcribirlos, detectar momentos destacados con IA y exportar clips MP4.

## Requisitos
- Python 3.11+
- FFmpeg y ffprobe instalados
- Una clave de API de OpenAI con facturación habilitada
- Vídeos propios o para los que tengas permiso de uso

## Instalación local
1. Instala FFmpeg. En Ubuntu: `sudo apt-get install ffmpeg`.
2. Crea entorno: `python -m venv .venv`
3. Actívalo y ejecuta: `pip install -r requirements.txt`
4. Copia `.env.example` a `.env`.
5. Genera una clave secreta fuerte y configura `SECRET_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD` y `OPENAI_API_KEY`.
6. Ejecuta: `python app.py`
7. Abre `http://localhost:5000`.

**Nunca compartas el archivo .env ni publiques tu clave API.** Cambia `COOKIE_SECURE=true` cuando uses HTTPS en producción. Usa una contraseña larga y única.

## Qué incluye
- Acceso privado con usuario y contraseña almacenados mediante hash.
- Sesión protegida, cookies HttpOnly/SameSite y protección CSRF.
- Subida de vídeos MP4, MOV, MKV y WEBM.
- Extracción de audio con FFmpeg y transcripción con IA.
- Selección de momentos destacados mediante modelo de texto.
- Recorte y codificación MP4 con FFmpeg.
- Límite global de 10 clips por día.
- Tarea programada a las 03:00, zona `America/Tegucigalpa`.

## Límites importantes
- Esta versión procesa archivos que subas. No descarga automáticamente vídeos desde enlaces de YouTube ni captura streams. Para integrar enlaces/directos, conecta una fuente oficial o un proveedor que permita acceder al contenido y verifica que tienes permiso. No intentes saltarte DRM, restricciones de acceso o controles de la plataforma.
- El límite de 10 es un máximo diario, no una garantía: si no hay vídeos en cola o la IA falla, se crearán menos.
- La tarea programada corre dentro del proceso de la app. En producción usa un worker/scheduler dedicado y almacenamiento persistente; no uses el servidor de desarrollo de Flask.
- Para uso público, añade límites de subida, antivirus, cuotas por usuario, copias de seguridad, HTTPS y monitorización.
- Los costes de transcripción y análisis dependen de la duración y del uso de la API.
