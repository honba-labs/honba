FROM rust:1.75 AS builder
WORKDIR /app
COPY . .
RUN cargo build --release --workspace

FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /app/target/release /app/target/release
COPY python/ /app/python/
RUN pip install /app/python/
ENTRYPOINT ["honba"]
