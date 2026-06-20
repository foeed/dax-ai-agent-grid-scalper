FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY scripts ./scripts
COPY tests ./tests
COPY mql5 ./mql5
COPY README.md .

RUN mkdir -p data logs

CMD ["python", "-m", "trend_scalper.signal_service"]
