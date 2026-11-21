FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
ADD https://github.com/tobymao/sqlglot.git#eb11aec4be8cb2122afe713a4816662c3f0fb2fa /opt/sqlglot
ENV PYTHONDONTWRITEBYTECODE=1 HOME=/tmp
WORKDIR /app
COPY src ./src
COPY artifacts ./artifacts
USER 65534:65534
CMD ["python", "-m", "src.main"]
