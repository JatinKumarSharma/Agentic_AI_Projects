FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/models/huggingface \
    XDG_CACHE_HOME=/models/cache \
    CVI_LOCAL_STT_MODEL=small \
    CVI_LOCAL_STT_DEVICE=cpu \
    CVI_LOCAL_STT_COMPUTE_TYPE=int8 \
    CVI_LOCAL_TTS_MODEL=/models/tts/en_US-lessac-medium.onnx

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY __init__.py app.py stt.py vad.py segmenter.py stream.py ./speech/

RUN useradd --create-home --uid 10002 --shell /usr/sbin/nologin speech \
    && mkdir -p /models/tts /models/huggingface /models/cache \
    && chown -R speech:speech /app /models

USER speech

EXPOSE 9000

CMD ["uvicorn", "speech.app:app", "--host", "0.0.0.0", "--port", "9000"]
