FROM python:3.13-slim

WORKDIR /app

# Install uv via pip (like rubber-duck)
RUN pip install uv

# Copy dependency files
COPY pyproject.toml .
COPY uv.lock* .

# Install dependencies
RUN uv sync --frozen --no-dev

# Copy application code
COPY . .

# Create data directory for SQLite
RUN mkdir -p /data

# Expose port
EXPOSE 8080

# Run with gunicorn for production
CMD ["uv", "run", "python", "-m", "gunicorn", "--bind", "0.0.0.0:8080", "app:app"]
