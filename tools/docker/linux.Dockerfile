# The CI runners' C toolchain (ubuntu-latest: Linux amd64, gcc + the -m32 multilib) for `tools/ci.sh
# linux`, so the C tests' gcc -Werror builds fail on the Mac too, not only in CI (Apple clang lets
# -Wformat-truncation / -Wstringop-truncation through). amd64 on purpose: gcc-multilib has no arm64
# package, and the point is CI's toolchain. Built from a context of this file + requirements-dev.txt.
FROM --platform=linux/amd64 python:3.12-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc-multilib g++-multilib make \
 && rm -rf /var/lib/apt/lists/*
COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install --no-cache-dir -r /tmp/requirements-dev.txt
ENV PYTHONDONTWRITEBYTECODE=1
