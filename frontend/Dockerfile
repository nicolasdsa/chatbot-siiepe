# ---- build ----
FROM node:20-alpine AS build
WORKDIR /app
ARG VITE_RAG_TOKEN
ENV VITE_RAG_TOKEN=$VITE_RAG_TOKEN
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build


# ---- run (nginx) ----
FROM nginx:alpine
WORKDIR /usr/share/nginx/html
# artefatos do Vite
COPY --from=build /app/dist ./
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
