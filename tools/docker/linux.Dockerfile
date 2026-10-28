# The CI runners' C toolchain (ubuntu-latest = Ubuntu 24.04: Linux amd64, gcc 13 + the -m32 multilib,
# Python 3.12) for `tools/ci.sh linux`, so the C tests' gcc -Werror builds fail on the Mac too, not only
# in CI (Apple clang lets -Wformat-truncation / -Wstringop-truncation through). amd64 on purpose:
# gcc-multilib has no arm64 package, and the point is CI's toolchain. Built from a context of this file
# + requirements-dev.txt.
FROM --platform=linux/amd64 ubuntu:24.04
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      gcc-multilib g++-multilib git make python3 python3-pip \
 && rm -rf /var/lib/apt/lists/*
COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install --no-cache-dir --break-system-packages -r /tmp/requirements-dev.txt
ENV PYTHONDONTWRITEBYTECODE=1
