FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Provider abstractions this service and apps/voice both depend on (item
# 20b).
#
# The build context is the repository root, not apps/api, which is why this
# and the COPYs below are repo-relative. It used to be a named build context
# ("shared", declared in docker-compose.yml's additional_contexts) with a
# context of apps/api - tidier, and undeployable: named contexts are a
# buildx feature, and Render's dockerContext is a single path with no
# equivalent. A Dockerfile that only builds under docker-compose is not a
# deployment artefact.
COPY packages/shared /packages/shared

# The lock, not requirements.txt. requirements.txt is what this service asks
# for; requirements.lock is every package that ends up installed, transitive
# dependencies included, at the versions a resolve actually produced - so two
# builds from one commit are the same image. Regenerate it with
# scripts/lock-python-deps.sh after changing requirements.txt; both are copied
# so the image carries the intent alongside the resolution.
COPY apps/api/requirements.txt apps/api/requirements.lock ./

# --require-hashes: pip verifies every downloaded artefact against the lock
# and refuses the install if any byte differs, so a version number cannot
# quietly resolve to different content than the one this was built and
# tested against.
#
# The shared package is installed separately because --require-hashes
# rejects any unhashed requirement, and a local source tree copied in from
# the build context has no meaningful artefact hash. --no-deps because
# everything it depends on is already in the hashed set above.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --require-hashes -r requirements.lock \
    && pip install --no-cache-dir --no-deps -e /packages/shared

# Bake the embedding model's tokenizer into the image, so the chunker never
# reaches the network mid-request. Without this the first document processed
# after a container starts pays a Hub download - measured at ~15s - and a
# deployment without egress to huggingface.co would silently fall back to the
# character estimate instead of the real token count.
#
# Build-arg driven so an image built for a different EMBEDDING_MODEL bakes
# that one instead.
ARG EMBEDDING_TOKENIZER=BAAI/bge-base-en-v1.5
RUN python -c "from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('${EMBEDDING_TOKENIZER}')"

# The model's weights, for EMBEDDING_PROVIDER=local. Same reasoning as the
# tokenizer above and more of it: this is ~440MB, so a container that had to
# fetch it at startup would take minutes to become useful, and the first
# caller to arrive during that would time out - which is the exact failure
# CLAUDE.md section 6.4 records from the previous in-process attempt.
#
# The ONNX export only - not the PyTorch weights sitting beside it in the
# same repository, which nothing here can run and which would double the
# image for no purpose. providers/local_embedding.py resolves the model
# through this same cache at runtime and finds it already present.
#
# Set EMBEDDING_WEIGHTS to an empty string to skip this and keep the image
# small, for a deployment that uses the hosted provider.
ARG EMBEDDING_WEIGHTS=BAAI/bge-base-en-v1.5
RUN if [ -n "${EMBEDDING_WEIGHTS}" ]; then \
        python -c "from huggingface_hub import snapshot_download; snapshot_download('${EMBEDDING_WEIGHTS}', allow_patterns=['onnx/model.onnx', '*.json', 'vocab.txt', '*.txt'])"; \
    fi

# The service's own source. Repo-relative, and deliberately last: everything
# above changes rarely, so a code edit rebuilds only this layer.
COPY apps/api/ .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]