FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# From docker-compose.yml's additional build context "shared" (item 20b) -
# provider abstractions this service and apps/voice both depend on.
COPY --from=shared . /packages/shared

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Bake the embedding model's tokenizer into the image, so the chunker never
# reaches the network mid-request. Without this the first document processed
# after a container starts pays a Hub download - measured at ~15s - and a
# deployment without egress to huggingface.co would silently fall back to the
# character estimate instead of the real token count.
#
# Only the tokenizer: a vocabulary file, not model weights. Build-arg driven
# so an image built for a different EMBEDDING_MODEL bakes that one instead.
ARG EMBEDDING_TOKENIZER=BAAI/bge-base-en-v1.5
RUN python -c "from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('${EMBEDDING_TOKENIZER}')"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]