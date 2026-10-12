CREATE EXTENSION IF NOT EXISTS postgis;


CREATE TABLE IF NOT EXISTS surveys (
    id          serial PRIMARY KEY,
    layer_name  text NOT NULL UNIQUE,
    farm        text NOT NULL,
    field       text NOT NULL,
    survey_date date NOT NULL,
    kind        text NOT NULL CHECK (kind IN ('raster', 'vector')),
    title       text,
    file_path   text,
    created_at  timestamptz,
    geom        geometry(Polygon, 4326)
);
CREATE INDEX IF NOT EXISTS surveys_geom_idx ON surveys USING gist (geom);
CREATE INDEX IF NOT EXISTS surveys_farm_field_idx ON surveys (farm, field, survey_date);

-- Очаги поражения, оконтуренные в мобильном приложении (пишутся через WFS-T).
CREATE TABLE IF NOT EXISTS foci (
    id           serial PRIMARY KEY,
    survey_layer text NOT NULL,
    farm         text,
    field        text,
    pest         text,
    area_ha      double precision,
    note         text,
    source       text,      -- 'manual' — нарисован в приложении, 'auto' — найден детектором
    sensitivity  integer,   -- чувствительность детектора для 'auto'
    created_at   timestamptz,
    geom         geometry(Polygon, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS foci_geom_idx ON foci USING gist (geom);
CREATE INDEX IF NOT EXISTS foci_survey_idx ON foci (survey_layer);
