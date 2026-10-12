#!/bin/bash
# GeoServer connects with a restricted role: SQL injection through GeoServer
# must not reach superuser rights (COPY ... PROGRAM, DROP, other tables).
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v gs_user="$GEOSERVER_DB_USER" -v gs_pass="$GEOSERVER_DB_PASSWORD" -v db="$POSTGRES_DB" <<'EOSQL'
CREATE ROLE :"gs_user" LOGIN PASSWORD :'gs_pass' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
GRANT CONNECT ON DATABASE :"db" TO :"gs_user";
GRANT USAGE ON SCHEMA public TO :"gs_user";
GRANT SELECT ON geometry_columns, spatial_ref_sys TO :"gs_user";
GRANT SELECT, INSERT, DELETE ON surveys, foci TO :"gs_user";
GRANT USAGE, SELECT ON SEQUENCE surveys_id_seq, foci_id_seq TO :"gs_user";
EOSQL
