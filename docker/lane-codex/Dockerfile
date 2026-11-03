# syntax=docker/dockerfile:1
# Build-only recipe; the application deliberately has no live execution path.
FROM --platform=linux/amd64 gcr.io/distroless/cc-debian12:debug-nonroot@sha256:923320b891f20d5f4bd43ed3a72eeee2f3323d481d6f4bd8d0b2c96d1c0758bc AS unpack
ADD --checksum=sha256:1d44b28bc4ac9937d7d3532264baa68a507ce364956a0d4ebb856f331cdee254 https://github.com/openai/codex/releases/download/rust-v0.166.0/codex-package-x86_64-unknown-linux-musl.tar.gz /tmp/codex.tar.gz
RUN ["/busybox/tar", "-xzf", "/tmp/codex.tar.gz", "-C", "/tmp", "bin/codex"]

FROM --platform=linux/amd64 gcr.io/distroless/cc-debian12:nonroot@sha256:9dac0a79194e45a7da0158a9c6da57b217585af0786db3845d1f0ec1a0dd182f
COPY --from=unpack --chmod=0555 /tmp/bin/codex /usr/local/bin/codex
COPY --chown=10001:10001 docker/lane-codex/profile/ /profile/
ENV PATH=/usr/local/bin
USER 10001:10001
WORKDIR /work
CMD ["/usr/local/bin/codex", "--version"]
