FROM python:3.12-slim

RUN useradd -m -u 1000 app
USER app
ENV PATH="/home/app/.local/bin:$PATH"

WORKDIR /app

COPY --chown=app requirements.txt .
RUN pip install --no-cache-dir --upgrade -r requirements.txt

COPY --chown=app . .

ENV PORT=8080
CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT}
