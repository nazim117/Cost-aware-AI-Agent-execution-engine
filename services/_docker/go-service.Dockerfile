FROM golang:1.25-alpine AS builder

ARG SERVICE

WORKDIR /app

# copy only the specific service
COPY services/${SERVICE} ./service

WORKDIR /app/service

RUN go mod tidy

RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -ldflags="-s -w" -o app ./cmd/server

FROM gcr.io/distroless/static:nonroot

WORKDIR /app

COPY --from=builder /app/service/app /app

EXPOSE 8080

ENTRYPOINT ["/app/app"]