FROM python:3.15-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_NO_CACHE=1
WORKDIR /app

# Dependencies first, from the lockfile, so code edits don't reinstall them.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable
ENV PATH=/app/.venv/bin:$PATH

COPY migrations ./migrations
COPY seeds ./seeds
ENV MIGRATIONS_DIR=/app/migrations SEEDS_FILE=/app/seeds/companies.csv

CMD ["python", "-m", "vacancy_finder"]
