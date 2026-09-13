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

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.lock

COPY . .

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
