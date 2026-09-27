FROM golang:1.25-alpine AS build

WORKDIR /src
COPY main.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/dashboard main.go

FROM scratch
WORKDIR /app
COPY --from=build /out/dashboard /app/dashboard
COPY static /app/static
USER 65532:65532
EXPOSE 3000
ENTRYPOINT ["/app/dashboard"]

