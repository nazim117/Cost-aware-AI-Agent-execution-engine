FROM golang:1.25-alpine AS builder

WORKDIR /app

WORKDIR /app
COPY --from=builder /app/app /app

EXPOSE 8082
ENTRYPOINT [""]