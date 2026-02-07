FROM golang:1.25-alpine AS builder

WORKDIR /app

# Copy go.mod first (always exists)
COPY go.mod ./

# Copy go.sum only if it exists
# (Docker doesn't support conditionals, so we copy everything later)
RUN go mod download || true

# Copy the rest of the service
COPY . .

# Build the binary
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -ldflags="-s -w" -o app ./cmd/server

# ---- Runtime image ----
FROM gcr.io/distroless/static:nonroot

WORKDIR /app
COPY --from=builder /app/app /app

EXPOSE 8080
ENTRYPOINT ["/app"]
