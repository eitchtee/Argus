FROM node:lts-alpine

WORKDIR /usr/src/frontend

COPY ./docker/dev/vite/entrypoint.sh /usr/local/bin/vite-entrypoint
RUN sed -i 's/\r$//g' /usr/local/bin/vite-entrypoint && \
    chmod +x /usr/local/bin/vite-entrypoint

ENV PATH=./node_modules/.bin/:$PATH

ENTRYPOINT ["vite-entrypoint"]
