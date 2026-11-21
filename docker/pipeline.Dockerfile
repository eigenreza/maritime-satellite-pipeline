# Pipeline worker: Python 3.11 + GDAL tools + ESA SNAP 14 (official installer, Sentinel toolboxes).
FROM python:3.11-slim-bookworm

ARG SNAP_VERSION=14.0
ARG SNAP_INSTALLER=esa-snap_sentinel_linux-14.0.0.sh

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    SNAP_HOME=/opt/snap \
    PATH=/opt/snap/bin:$PATH \
    MSP_SNAP_MODE=local

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates gdal-bin fontconfig libgfortran5 git \
 && rm -rf /var/lib/apt/lists/*

# ESA SNAP, unattended install from the official download server
RUN curl -fsSL -o /tmp/snap.sh "https://download.esa.int/step/snap/${SNAP_VERSION}/installers/${SNAP_INSTALLER}" \
 && sh /tmp/snap.sh -q -dir /opt/snap \
 && rm /tmp/snap.sh \
 && echo "-Xmx6G" > /opt/snap/bin/gpt.vmoptions \
 && gpt -h > /dev/null

WORKDIR /opt/msp
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install -e ".[dev,demo,ml]"

WORKDIR /work
CMD ["msp", "--help"]
