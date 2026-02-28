FROM nginx:1.27-alpine

COPY frontend/index.html /usr/share/nginx/html/index.html
COPY frontend/main.js /usr/share/nginx/html/main.js
COPY frontend/style.css /usr/share/nginx/html/style.css
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 8080
