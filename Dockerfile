FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENTRYPOINT ["etekcity-scale-daemon"]
CMD ["--config", "/etc/etekcity-scale-daemon/config.ini"]
