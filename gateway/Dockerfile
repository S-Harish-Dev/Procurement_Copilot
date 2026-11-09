# Gateway + mock-llm images (multi-arch: amd64 and arm64, e.g. an Apple Silicon laptop).
# Build context = repository root:  docker build -f gateway/Dockerfile --target gateway .

FROM rust:1.99.1-slim-trixie AS build
# protoc: the gateway generates its brain gRPC client from proto/brain.proto at build time.
RUN apt-get update && apt-get install -y --no-install-recommends protobuf-compiler && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY gateway/ gateway/
COPY prompts/ prompts/
COPY proto/ proto/
WORKDIR /src/gateway
# Cache the dependency registry and target dir across builds.
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/src/gateway/target \
    cargo build --release -p api -p mock-llm \
    && mkdir -p /out && cp target/release/gateway target/release/mock-llm /out/

FROM debian:trixie-20261027-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home /app app
WORKDIR /app

FROM runtime AS gateway
# ONNX Runtime is loaded dynamically (fastembed `ort-load-dynamic`): one official library per arch.
ARG ORT_VERSION=1.31.1
ARG TARGETARCH
RUN set -eux; \
    case "${TARGETARCH:-amd64}" in amd64) a=x64 ;; arm64) a=aarch64 ;; *) echo "unsupported arch"; exit 1 ;; esac; \
    curl -fsSL "https://github.com/microsoft/onnxruntime/releases/download/v${ORT_VERSION}/onnxruntime-linux-${a}-${ORT_VERSION}.tgz" \
      | tar -xz -C /opt; \
    ln -s "/opt/onnxruntime-linux-${a}-${ORT_VERSION}/lib/libonnxruntime.so.${ORT_VERSION}" /usr/local/lib/libonnxruntime.so
ENV ORT_DYLIB_PATH=/usr/local/lib/libonnxruntime.so \
    REPO_ROOT=/app \
    FASTEMBED_CACHE_DIR=/models/fastembed \
    PROMPTS_DIR=/app/prompts \
    ASSETS_DIR=/app/gateway/assets
COPY --from=build /out/gateway /usr/local/bin/gateway
COPY prompts/ /app/prompts/
COPY gateway/assets/ /app/gateway/assets/
COPY gateway/providers.toml gateway/providers.mock.toml gateway/providers.mock.docker.toml /app/gateway/
RUN mkdir -p /models && chown app /models
USER app
EXPOSE 8080 8081 9464
HEALTHCHECK --interval=10s --timeout=3s --start-period=60s --retries=6 \
    CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1
ENTRYPOINT ["/usr/local/bin/gateway"]

FROM runtime AS mock-llm
COPY --from=build /out/mock-llm /usr/local/bin/mock-llm
USER app
ENV MOCK_HOST=0.0.0.0
EXPOSE 8090
ENTRYPOINT ["/usr/local/bin/mock-llm"]
