FROM postgis/postgis:17-3.5

USER root

# postgres h3 binary
RUN apt-get update && \
    apt-get install -y \
    postgresql-17 \
    postgresql-17-postgis-3 \
    postgresql-17-h3 && \
    rm -rf /var/lib/apt/lists/*

