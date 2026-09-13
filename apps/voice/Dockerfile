FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# From docker-compose.yml's additional build context "shared" (item 20b) -
# provider abstractions this service and apps/api both depend on.
COPY --from=shared . /packages/shared

# The lock, not requirements.txt. requirements.txt is what this service asks
# for; requirements.lock is every package that ends up installed, transitive
# dependencies included, at the versions a resolve actually produced - so two
# builds from one commit are the same image. Regenerate it with
# scripts/lock-python-deps.sh after changing requirements.txt; both are copied
# so the image carries the intent alongside the resolution.
COPY requirements.txt requirements.lock ./

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

COPY . .

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
