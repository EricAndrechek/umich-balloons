FROM node:20-alpine AS base

WORKDIR /usr/src/app

# Copy package.json and package-lock.json (if available)
COPY package*.json ./

# ---- Dependencies Stage ----
FROM base AS dependencies
# Install dependencies including devDependencies needed for build, if any
# Using npm ci is generally recommended for reproducible builds in CI/CD
# RUN npm ci
# Using npm install if package-lock.json might not be present/up-to-date
RUN npm install

# ---- Production Stage ----
FROM base AS production

# Set NODE_ENV to production (important for Express performance)
ENV NODE_ENV=production

# Copy only necessary files from previous stages
COPY --from=dependencies /usr/src/app/node_modules ./node_modules
COPY package*.json ./
COPY server.js .

EXPOSE 3000

CMD ["node", "server.js"]