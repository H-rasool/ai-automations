# n8n with a pinned version, built on Alpine.
# The build tools are needed so community nodes (e.g. the Evolution API node) can be installed from the n8n UI.
FROM node:20-alpine

USER root

RUN apk add --no-cache python3 py3-pip build-base git bash

# Pin the n8n version so rebuilds are reproducible
RUN npm install -g n8n@2.6.3 --omit=dev

RUN mkdir -p /home/node/.n8n && chown -R node:node /home/node/.n8n

USER node
ENV HOME=/home/node
WORKDIR /home/node

EXPOSE 5678

ENTRYPOINT ["n8n"]
CMD ["start"]
