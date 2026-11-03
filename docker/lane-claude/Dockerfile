# syntax=docker/dockerfile:1
# Build-only recipe; live execution remains disabled in the application.
FROM --platform=linux/amd64 gcr.io/distroless/cc-debian12:debug-nonroot@sha256:923320b891f20d5f4bd43ed3a72eeee2f3323d481d6f4bd8d0b2c96d1c0758bc AS unpack
ADD --checksum=sha256:153511cf79cd27c55c9f9c919cc532eb90647f7893cb3f4e78dac776991714e0 https://github.com/anthropics/claude-code/releases/download/v2.1.317/claude-linux-x64.tar.gz /tmp/claude.tar.gz
RUN ["/busybox/tar", "-xzf", "/tmp/claude.tar.gz", "-C", "/tmp", "claude"]

FROM --platform=linux/amd64 gcr.io/distroless/cc-debian12:nonroot@sha256:9dac0a79194e45a7da0158a9c6da57b217585af0786db3845d1f0ec1a0dd182f
COPY --from=unpack --chmod=0555 /tmp/claude /usr/local/bin/claude
COPY --chown=10001:10001 docker/lane-claude/profile/ /profile/
ENV PATH=/usr/local/bin
USER 10001:10001
WORKDIR /work
CMD ["/usr/local/bin/claude", "--version"]
