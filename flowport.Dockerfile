FROM alpine:3.23

ARG TARGETARCH
RUN apk add --no-cache ca-certificates curl \
    && case "$TARGETARCH" in \
         amd64) checksum=a7d66486cccc43cc169c6d56876ffc9268ed7f91a23bfdf8239595905f4075cf ;; \
         arm64) checksum=8ae9ea54c010b6eb956904656960438013c2d3b8eb1d52f03565450788ad1e2b ;; \
         *) echo "Unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
       esac \
    && curl --fail --location --retry 3 \
         "https://flowport.pages.dev/releases/0.5.1/flowport_0.5.1_linux_${TARGETARCH}.tar.gz?build=04afe5d87607cd875203857031b955011ba23a0e" \
         --output /tmp/flowport.tar.gz \
    && echo "$checksum  /tmp/flowport.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/flowport.tar.gz -C /tmp \
    && cp "/tmp/flowport_0.5.1_linux_${TARGETARCH}/collectway" /usr/local/bin/collectway \
    && rm -r /tmp/flowport.tar.gz "/tmp/flowport_0.5.1_linux_${TARGETARCH}"

ENTRYPOINT ["collectway"]
CMD ["-admin-listen", "0.0.0.0:8080", "-ingest-listen", "0.0.0.0:8081", "-data-dir", "/data", "-queue-bytes", "67108864"]
