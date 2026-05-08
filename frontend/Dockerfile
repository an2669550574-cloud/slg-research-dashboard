FROM node:20-alpine AS builder
WORKDIR /app

# Vite 在构建时把 VITE_* 环境变量内联到产物里。生产部署需要把 API key 编译进去。
ARG VITE_API_KEY=""
ENV VITE_API_KEY=${VITE_API_KEY}

COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
