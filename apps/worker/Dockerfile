FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

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

CMD ["python", "main.py"]
