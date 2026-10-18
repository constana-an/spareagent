# SpareAgent execution sandbox image (Week 3). Contains only interpreters for the fixture
# languages: Python 3 (stdlib unittest) and Node 22 (built-in test runner + type stripping).
# No git, no package managers' credentials, no network tooling beyond the base image.
FROM node:22-alpine
RUN apk add --no-cache python3 \
 && rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
 && deluser --remove-home node && rm -rf /home /root/.* 2>/dev/null; true
ENV PYTHONDONTWRITEBYTECODE=1 HOME=/tmp NODE_OPTIONS=--disable-warning=ExperimentalWarning
USER 65534:65534
WORKDIR /workspace
CMD ["sleep", "60"]
