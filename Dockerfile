FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LA_CACHE_DIR=/var/cache/paragraf \
    KB_PATH=/srv/data/knowledge-base.zip

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY submission/ submission/
COPY app/ app/
COPY materials/knowledge-base.zip data/knowledge-base.zip

RUN useradd --create-home --uid 10001 paragraf \
    && mkdir -p /var/cache/paragraf \
    && chown -R paragraf /var/cache/paragraf /srv
USER paragraf

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]
