# Backend Dockerfile - Node.js API only
# Python hardware daemon now runs in its own container (see hardware/Dockerfile)
FROM node:20-bookworm-slim

WORKDIR /app

# Copy package files first for layer caching
COPY package.json package-lock.json* ./

# Install Node.js dependencies
RUN npm install --production

# Copy all source code
COPY . .

# Expose port (5000=Node API)
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD node -e "require('http').get('http://localhost:5000/health', (r) => { process.exit(r.statusCode === 200 ? 0 : 1) })"

CMD ["node", "server.js"]
