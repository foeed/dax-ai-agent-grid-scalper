FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

WORKDIR /app

RUN groupadd -r bot && useradd -r -g bot bot

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY mql5 ./mql5
COPY README.md .

RUN mkdir -p data logs && chown -R bot:bot /app

USER bot

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8766/health')" || exit 1

CMD ["python", "-m", "trend_scalper.signal_service"]
