FROM golang:1.25-alpine AS builder

WORKDIR /app

COPY go.mod ./
RUN go mod download || true

COPY . .

RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -ldflags="-s -w" -o app ./cmd/server

# ---- Runtime image ----
FROM gcr.io/distroless/static:nonroot

WORKDIR /app
COPY --from=builder /app/app /app

EXPOSE 8082
ENTRYPOINT ["/app"]
