# Local docs gate (the Mac has no sphinx):
#   docker build -t oc-docs -f docs/Dockerfile .
#   docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD":/w -w /w oc-docs sh docs/build_site.sh docs/_build/site
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends doxygen \
    && rm -rf /var/lib/apt/lists/*
COPY docs/requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt
