# Fake Ollama HTTP service for the e2e Docker stack.
# Exposes /api/tags, /api/chat (streaming), /api/generate, /api/embed,
# /api/embeddings — the surface cortex / neuron call when the
# configured LLM provider is "ollama". Backed by deterministic
# canned responses; see server.py.
FROM python:3.14.3-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

RUN pip install "aiohttp>=3.10,<4"

COPY server.py /app/server.py

EXPOSE 11434

HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:11434/api/tags', timeout=2).status == 200 else 1)"

CMD ["python", "/app/server.py"]
