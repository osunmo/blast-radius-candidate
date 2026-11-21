FROM alpine/git@sha256:2306e2b05ee0ef99f5d25b0779f645b24ae9f91ba187107503592fcdfe78da3a AS upstream
ARG SQLGLOT_REV=eb11aec4be8cb2122afe713a4816662c3f0fb2fa
RUN git init -q /repo \
    && git -C /repo fetch --no-recurse-submodules --no-tags --depth=1 https://github.com/tobymao/sqlglot.git "$SQLGLOT_REV" \
    && test "$(git -C /repo rev-parse FETCH_HEAD)" = "$SQLGLOT_REV" \
    && git -C /repo archive --format=tar -o /source.tar FETCH_HEAD \
    && mkdir /snapshot && tar -xf /source.tar -C /snapshot

FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
COPY --from=upstream /snapshot /opt/sqlglot
ENV PYTHONDONTWRITEBYTECODE=1 HOME=/tmp
WORKDIR /app
COPY src ./src
COPY artifacts ./artifacts
USER 65534:65534
CMD ["python", "-m", "src.main"]
