# Vanilla-JS frontend served by nginx. Backend is reached at the same origin
# via /api/* (proxied by nginx — see frontend.nginx.conf).
FROM nginx:1.27-alpine

COPY frontend.nginx.conf /etc/nginx/conf.d/default.conf
COPY web/ /usr/share/nginx/html/
