FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend /app/backend
COPY frontend /app/frontend

ENV PYTHONUNBUFFERED=1
ENV BEAST_REPLAY_ONLY=1

EXPOSE 5000

CMD ["python", "/app/backend/app.py"]
