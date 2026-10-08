# Runs the store website + automation in one container. Mount /app/data to keep the database across deploys.
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY spxbot ./spxbot
COPY shopbot ./shopbot
RUN pip install --no-cache-dir ".[shop-ai]"
COPY config.shop.example.toml ./
ENV SHOPBOT_CONFIG=/app/config.shop.toml PORT=8000
VOLUME /app/data
EXPOSE 8000
CMD ["shopbot", "run"]
