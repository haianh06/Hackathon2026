# Stage 1: Build
FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .

# Build args for API URL (passed at build time)
ARG REACT_APP_API_URL=http://localhost:5000/api
ARG REACT_APP_SOCKET_URL=http://localhost:5000
ENV REACT_APP_API_URL=$REACT_APP_API_URL
ENV REACT_APP_SOCKET_URL=$REACT_APP_SOCKET_URL
ENV CI=false
ENV DISABLE_ESLINT_PLUGIN=true

RUN npm run build

# Stage 2: Serve with nginx
FROM nginx:alpine

# Copy custom nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy built files
COPY --from=build /app/build /usr/share/nginx/html

EXPOSE 3000

CMD ["nginx", "-g", "daemon off;"]
