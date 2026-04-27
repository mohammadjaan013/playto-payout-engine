FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python manage.py collectstatic --noinput 2>/dev/null || true

EXPOSE 8000

# Run migrations + seed then start gunicorn.
# Using a shell so we can chain commands and expand $PORT.
CMD python manage.py migrate --noinput && python seed.py && gunicorn playto_payout.wsgi --bind 0.0.0.0:${PORT:-8000} --workers 2 --timeout 120
