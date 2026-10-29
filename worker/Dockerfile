FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY migrations ./migrations
COPY seeds ./seeds
ENV MIGRATIONS_DIR=/app/migrations SEEDS_FILE=/app/seeds/companies.csv

CMD ["python", "-m", "vacancy_finder"]
