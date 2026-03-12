FROM golang:1.23-alpine AS builder

WORKDIR /app

COPY go.work ./
COPY services ./services

WORKDIR /app/services/${SERVICE}

RUN go mod download

RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -ldflags="-s -w" -o app ./cmd/server

FROM gcr.io/distroless/static:nonroot
WORKDIR /app
COPY --from=builder /app/services/${SERVICE}/app /app
EXPOSE 8080
ENTRYPOINT ["/app"]