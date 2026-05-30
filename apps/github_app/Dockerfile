# Probot app — Node 20 base, with the Guardian Python CLI installed on
# top so the App can shell out to `guardian diff`.
FROM node:20-bookworm-slim

ENV PYTHONUNBUFFERED=1

RUN apt-get update \
  && apt-get install -y --no-install-recommends python3 python3-pip git \
  && rm -rf /var/lib/apt/lists/*

# Install the Guardian CLI into a system-wide location.
COPY pyproject.toml /tmp/guardian/pyproject.toml
COPY packages /tmp/guardian/packages
COPY apps/cli /tmp/guardian/apps/cli
RUN pip3 install --break-system-packages /tmp/guardian

WORKDIR /app
COPY apps/github_app/package.json apps/github_app/package-lock.json* ./
RUN npm install --omit=dev
COPY apps/github_app/ .

ENV NODE_ENV=production
EXPOSE 3000
CMD ["npm", "start"]
