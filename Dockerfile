FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    FINTRACE_DB=/data/banking.db \
    OLLAMA_BASE_URL=http://host.docker.internal:11434

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 app && mkdir /data && chown app /data
USER app

EXPOSE 8000

# Create the demo database on first start. The ground-truth file is evaluation-only and is not kept.
CMD ["sh", "-c", "[ -f \"$FINTRACE_DB\" ] || { python -m fintrace.synthetic_data --db \"$FINTRACE_DB\" --truth /tmp/ground_truth.json && rm /tmp/ground_truth.json; } && exec uvicorn fintrace.api:app --host 0.0.0.0 --port 8000"]
