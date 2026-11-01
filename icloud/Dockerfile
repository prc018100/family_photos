FROM icloudpd/icloudpd:1.32.3

USER root
RUN apk add --no-cache python3 bash
COPY icloud/sync.sh /usr/local/bin/icloud-sync.sh
COPY scripts/lib.sh /usr/local/bin/lib.sh
COPY scripts/icloud_library.py /usr/local/bin/icloud_library.py
RUN chmod 755 /usr/local/bin/icloud-sync.sh /usr/local/bin/lib.sh /usr/local/bin/icloud_library.py

# Upstream entrypoint only accepts the icloudpd binary name. This wrapper
# calls /app/icloudpd itself, then asks Immich to scan.
ENTRYPOINT ["/usr/local/bin/icloud-sync.sh"]
