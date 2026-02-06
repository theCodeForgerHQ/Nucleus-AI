# Frontend — Next.js (Warp-style UI)
FROM node:20-alpine AS base

WORKDIR /app

# Install dependencies
COPY package.json package-lock.json* ./
RUN npm install

# Copy app source
COPY . .

# Expose Next.js dev port
EXPOSE 3000

# Dev: run Next.js dev server (bind to 0.0.0.0 for Docker)
CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0"]
