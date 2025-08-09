# frontend/Dockerfile
# Build static assets with Vite
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci || npm i
COPY . .
# You can set Vite env at build-time if you ever bypass Nginx proxying:
#   VITE_GAME_API_BASE=/api VITE_ENGINE_API_BASE=/engine VITE_ENGINE_EVENTS_BASE=/engine-events npm run build
RUN npm run build

# Serve with nginx and reverse-proxy our services
FROM nginx:1.27-alpine
# Remove default site and install our config
RUN rm -rf /etc/nginx/conf.d/*
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Static files
WORKDIR /usr/share/nginx/html
COPY --from=build /app/dist ./

EXPOSE 80

# Basic healthcheck (optional)
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD wget -qO- http://localhost/ >/dev/null 2>&1 || exit 1