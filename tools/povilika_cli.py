#!/usr/bin/env python3
"""Povilika GIS utility: GeoServer setup and survey ingestion.

  povilika_cli.py setup
  povilika_cli.py ingest FILE --farm NAME --field NAME --date YYYY-MM-DD [--title T] [--move] [--replace]
  povilika_cli.py list
  povilika_cli.py remove LAYER_NAME

Settings are read from server/.env (override with --env-file or environment variables).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

import requests

WORKSPACE = "povilika"
NAMESPACE_URI = "urn:povilika"
DATASTORE = "povilika_db"
EDITOR_ROLE = "ROLE_POVILIKA_EDITOR"
PEST = "Cuscuta campestris"

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV = ROOT / "server" / ".env"

RASTER_EXT = {".tif", ".tiff"}
VECTOR_EXT = {".gpkg", ".shp"}
SHP_SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx")

TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюяәғқңөұүһі",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
     "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya",
     "a", "g", "k", "n", "o", "u", "u", "h", "i"],
))


def slug(text: str) -> str:
    out = "".join(TRANSLIT.get(c, c) for c in text.strip().lower())
    out = re.sub(r"[^a-z0-9]+", "_", out).strip("_")
    if not out:
        raise SystemExit(f"Не удалось построить имя из '{text}'")
    return out


def layer_id(*parts: str) -> str:
    name = "_".join(p for p in parts if p)
    return name if name[0].isalpha() else f"s_{name}"


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                values[key.strip()] = val.strip().strip('"').strip("'")
    values.update({k: v for k, v in os.environ.items() if k in values or k.startswith(("GEOSERVER_", "POSTGRES_", "APP_", "SURVEYS_", "PUBLIC_"))})
    return values


@dataclass
class Config:
    url: str
    user: str
    password: str
    host_dir: Path
    container_dir: str
    env: dict[str, str]

    @classmethod
    def from_env(cls, env_file: Path) -> "Config":
        env = load_env(env_file)
        host_dir = Path(env.get("SURVEYS_HOST_DIR", "./data/surveys"))
        if not host_dir.is_absolute():
            host_dir = (env_file.parent / host_dir).resolve()
        return cls(
            url=env.get("GEOSERVER_URL", "http://localhost:8080/geoserver").rstrip("/"),
            user=env.get("GEOSERVER_ADMIN_USER", "admin"),
            password=env.get("GEOSERVER_ADMIN_PASSWORD", "geoserver"),
            host_dir=host_dir,
            container_dir=env.get("SURVEYS_CONTAINER_DIR", "/data/surveys").rstrip("/"),
            env=env,
        )


class GeoServer:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.s = requests.Session()
        self.s.auth = (cfg.user, cfg.password)
        self.rest = f"{cfg.url}/rest"

    def _check(self, r: requests.Response, ok=(200, 201)) -> requests.Response:
        if r.status_code not in ok:
            raise SystemExit(f"GeoServer {r.request.method} {r.url} -> {r.status_code}: {r.text[:500]}")
        return r

    def get(self, path: str, **kw):
        return self.s.get(f"{self.rest}{path}", headers={"Accept": "application/json"}, timeout=60, **kw)

    def exists(self, path: str) -> bool:
        return self.get(path).status_code == 200

    def post_json(self, path: str, body: dict, ok=(200, 201)):
        return self._check(self.s.post(f"{self.rest}{path}", json=body, timeout=120), ok)

    def put_json(self, path: str, body: dict):
        return self._check(self.s.put(f"{self.rest}{path}", json=body, timeout=120))

    def delete(self, path: str, ok=(200, 404)):
        return self._check(self.s.delete(f"{self.rest}{path}", timeout=120), ok)

    def wfs_transaction(self, xml: str) -> str:
        r = self._check(self.s.post(
            f"{self.cfg.url}/{WORKSPACE}/wfs",
            data=xml.encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=utf-8"},
            timeout=60,
        ), ok=(200,))
        if "ExceptionReport" in r.text or "ServiceException" in r.text:
            raise SystemExit(f"WFS-T ошибка: {r.text[:800]}")
        return r.text

    def wfs_features(self, type_name: str, cql: str | None = None) -> list[dict]:
        params = {
            "service": "WFS", "version": "1.1.0", "request": "GetFeature",
            "typeName": f"{WORKSPACE}:{type_name}", "outputFormat": "application/json",
            "srsName": "EPSG:4326",
        }
        if cql:
            params["CQL_FILTER"] = cql
        r = self._check(self.s.get(f"{self.cfg.url}/{WORKSPACE}/wfs", params=params, timeout=60), ok=(200,))
        return r.json().get("features", [])


# ---------------------------------------------------------------- setup

def _ensure_admin_password(cfg: Config) -> None:
    url = f"{cfg.url}/rest/about/version.json"
    if requests.get(url, auth=(cfg.user, cfg.password), timeout=30).status_code == 200:
        return
    if requests.get(url, auth=("admin", "geoserver"), timeout=30).status_code != 200:
        raise SystemExit("Не удаётся войти в GeoServer ни с паролем из .env, ни со стандартным.")
    r = requests.put(f"{cfg.url}/rest/security/self/password", auth=("admin", "geoserver"),
                     json={"newPassword": cfg.password}, timeout=30)
    if r.status_code != 200:
        raise SystemExit(f"Не удалось сменить пароль администратора: {r.status_code} {r.text[:200]}")
    print("[+] стандартный пароль администратора заменён паролем из .env")


def cmd_setup(cfg: Config, args) -> None:
    _ensure_admin_password(cfg)
    gs = GeoServer(cfg)
    env = cfg.env

    if gs.exists(f"/workspaces/{WORKSPACE}"):
        print(f"[=] workspace {WORKSPACE}")
    else:
        gs.post_json("/namespaces", {"namespace": {"prefix": WORKSPACE, "uri": NAMESPACE_URI}})
        print(f"[+] workspace {WORKSPACE}")

    if gs.exists(f"/workspaces/{WORKSPACE}/datastores/{DATASTORE}"):
        print(f"[=] datastore {DATASTORE}")
    else:
        params = {
            "dbtype": "postgis", "host": "postgis", "port": "5432",
            "database": env.get("POSTGRES_DB", "povilika"),
            "user": env.get("GEOSERVER_DB_USER", "geoserver_app"),
            "passwd": env.get("GEOSERVER_DB_PASSWORD", ""),
            "schema": "public", "Expose primary keys": "false",
        }
        gs.post_json(f"/workspaces/{WORKSPACE}/datastores", {"dataStore": {
            "name": DATASTORE,
            "connectionParameters": {"entry": [{"@key": k, "$": v} for k, v in params.items()]},
        }})
        print(f"[+] datastore {DATASTORE} (PostGIS)")

    world = {"minx": -180, "maxx": 180, "miny": -90, "maxy": 90, "crs": "EPSG:4326"}
    for table, title in (("surveys", "Каталог съёмок"), ("foci", "Очаги поражения повиликой")):
        path = f"/workspaces/{WORKSPACE}/datastores/{DATASTORE}/featuretypes"
        if gs.exists(f"{path}/{table}"):
            print(f"[=] layer {table}")
            continue
        gs.post_json(path, {"featureType": {
            "name": table, "nativeName": table, "title": title,
            "srs": "EPSG:4326", "projectionPolicy": "FORCE_DECLARED",
            "nativeBoundingBox": world, "latLonBoundingBox": world,
        }})
        print(f"[+] layer {table}")

    sld = ROOT / "server" / "styles" / "foci.sld"
    if not gs.exists(f"/workspaces/{WORKSPACE}/styles/foci"):
        gs._check(gs.s.post(
            f"{gs.rest}/workspaces/{WORKSPACE}/styles?name=foci",
            data=sld.read_bytes(),
            headers={"Content-Type": "application/vnd.ogc.sld+xml"},
            timeout=60,
        ))
        gs.put_json(f"/layers/{WORKSPACE}:foci", {"layer": {
            "defaultStyle": {"name": f"{WORKSPACE}:foci", "workspace": WORKSPACE},
        }})
        print("[+] style foci")

    app_user = env.get("APP_USER", "povilika_app")
    app_password = env.get("APP_PASSWORD")
    if not app_password:
        raise SystemExit("APP_PASSWORD не задан в .env")

    roles_list = gs.get("/security/roles").json().get("roles", [])
    if EDITOR_ROLE not in roles_list:
        gs._check(gs.s.post(f"{gs.rest}/security/roles/role/{EDITOR_ROLE}", timeout=60))
        print(f"[+] role {EDITOR_ROLE}")
    users = gs.get("/security/usergroup/users").json().get("users", [])
    if any(u.get("userName") == app_user for u in users):
        gs.post_json(f"/security/usergroup/user/{app_user}",
                     {"user": {"userName": app_user, "password": app_password, "enabled": True}})
        print(f"[=] user {app_user} (пароль обновлён)")
    else:
        gs.post_json("/security/usergroup/users",
                     {"user": {"userName": app_user, "password": app_password, "enabled": True}})
        print(f"[+] user {app_user}")
    gs._check(gs.s.post(f"{gs.rest}/security/roles/role/{EDITOR_ROLE}/user/{app_user}", timeout=60))

    rule = f"{WORKSPACE}.foci.w"
    acl = gs.get("/security/acl/layers").json()
    roles = f"ADMIN,{EDITOR_ROLE}"
    if rule in acl:
        gs.put_json("/security/acl/layers", {rule: roles})
    else:
        gs.post_json("/security/acl/layers", {rule: roles})
    print(f"[+] право записи {rule} -> {roles}")

    settings = gs.get("/settings").json()
    # По умолчанию WFS отдаёт 4 знака (~10 м) — контуры очагов и KML огрубляются.
    settings["global"]["settings"]["numDecimals"] = 8
    hostname = env.get("PUBLIC_HOSTNAME", "")
    if hostname and hostname != "maps.example.com":
        settings["global"]["settings"]["proxyBaseUrl"] = f"https://{hostname}/geoserver"
        print(f"[+] proxyBaseUrl https://{hostname}/geoserver")
    gs.put_json("/settings", settings)
    print("[+] точность координат в WFS: 8 знаков")

    print("Готово.")


# ---------------------------------------------------------------- ingest

def _bbox_polygon_gml(b: dict) -> str:
    x1, y1, x2, y2 = b["minx"], b["miny"], b["maxx"], b["maxy"]
    coords = f"{x1} {y1} {x2} {y1} {x2} {y2} {x1} {y2} {x1} {y1}"
    return (f'<gml:Polygon srsName="EPSG:4326"><gml:exterior><gml:LinearRing>'
            f"<gml:posList>{coords}</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon>")


def _register_survey(gs: GeoServer, *, layer: str, farm: str, field: str, date: dt.date,
                     kind: str, title: str, file_path: str, bbox: dict) -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    props = {
        "layer_name": layer, "farm": farm, "field": field, "survey_date": date.isoformat(),
        "kind": kind, "title": title, "file_path": file_path, "created_at": now,
    }
    body = "".join(f"<{WORKSPACE}:{k}>{escape(str(v))}</{WORKSPACE}:{k}>" for k, v in props.items())
    gs.wfs_transaction(f"""<wfs:Transaction service="WFS" version="1.1.0"
  xmlns:wfs="http://www.opengis.net/wfs" xmlns:gml="http://www.opengis.net/gml"
  xmlns:{WORKSPACE}="{NAMESPACE_URI}">
  <wfs:Insert><{WORKSPACE}:surveys>{body}<{WORKSPACE}:geom>{_bbox_polygon_gml(bbox)}</{WORKSPACE}:geom></{WORKSPACE}:surveys></wfs:Insert>
</wfs:Transaction>""")


def _unregister_survey(gs: GeoServer, layer: str) -> None:
    gs.wfs_transaction(f"""<wfs:Transaction service="WFS" version="1.1.0"
  xmlns:wfs="http://www.opengis.net/wfs" xmlns:ogc="http://www.opengis.net/ogc"
  xmlns:{WORKSPACE}="{NAMESPACE_URI}">
  <wfs:Delete typeName="{WORKSPACE}:surveys"><ogc:Filter><ogc:PropertyIsEqualTo>
    <ogc:PropertyName>layer_name</ogc:PropertyName><ogc:Literal>{escape(layer)}</ogc:Literal>
  </ogc:PropertyIsEqualTo></ogc:Filter></wfs:Delete>
</wfs:Transaction>""")


def publish_candidates(cfg: Config, survey: dict, polygons: list, sensitivity: int) -> int:
    """Replaces auto-detected foci of a survey. polygons: [(shapely Polygon EPSG:4326, area_ha)]."""
    gs = GeoServer(cfg)
    layer = escape(survey["layer_name"])
    gs.wfs_transaction(f"""<wfs:Transaction service="WFS" version="1.1.0"
  xmlns:wfs="http://www.opengis.net/wfs" xmlns:ogc="http://www.opengis.net/ogc"
  xmlns:{WORKSPACE}="{NAMESPACE_URI}">
  <wfs:Delete typeName="{WORKSPACE}:foci"><ogc:Filter><ogc:And>
    <ogc:PropertyIsEqualTo><ogc:PropertyName>survey_layer</ogc:PropertyName><ogc:Literal>{layer}</ogc:Literal></ogc:PropertyIsEqualTo>
    <ogc:PropertyIsEqualTo><ogc:PropertyName>source</ogc:PropertyName><ogc:Literal>auto</ogc:Literal></ogc:PropertyIsEqualTo>
  </ogc:And></ogc:Filter></wfs:Delete>
</wfs:Transaction>""")
    if not polygons:
        return 0
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    inserts = []
    for poly, area_ha in polygons:
        coords = " ".join(f"{x} {y}" for x, y in poly.exterior.coords)
        props = {
            "survey_layer": survey["layer_name"], "farm": survey["farm"], "field": survey["field"],
            "pest": PEST, "area_ha": f"{area_ha:.6f}", "source": "auto",
            "sensitivity": str(sensitivity), "created_at": now,
        }
        body = "".join(f"<{WORKSPACE}:{k}>{escape(v)}</{WORKSPACE}:{k}>" for k, v in props.items())
        inserts.append(
            f"<{WORKSPACE}:foci>{body}<{WORKSPACE}:geom><gml:Polygon srsName=\"EPSG:4326\"><gml:exterior>"
            f"<gml:LinearRing><gml:posList>{coords}</gml:posList></gml:LinearRing></gml:exterior>"
            f"</gml:Polygon></{WORKSPACE}:geom></{WORKSPACE}:foci>")
    text = gs.wfs_transaction(f"""<wfs:Transaction service="WFS" version="1.1.0"
  xmlns:wfs="http://www.opengis.net/wfs" xmlns:gml="http://www.opengis.net/gml"
  xmlns:{WORKSPACE}="{NAMESPACE_URI}">
  <wfs:Insert>{''.join(inserts)}</wfs:Insert>
</wfs:Transaction>""")
    m = re.search(r"totalInserted>(\d+)<", text)
    return int(m.group(1)) if m else len(polygons)


def _transfer(src: Path, dst: Path, move: bool, progress=None) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    if move and src.stat().st_dev == dst.parent.stat().st_dev:
        src.rename(dst)
        return
    total, done = src.stat().st_size, 0
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        while chunk := fin.read(8 * 1024 * 1024):
            fout.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    shutil.copystat(src, dst)
    if move:
        src.unlink()


def _publish_raster(gs: GeoServer, store: str, container_path: str) -> list[tuple[str, dict]]:
    gs._check(gs.s.put(
        f"{gs.rest}/workspaces/{WORKSPACE}/coveragestores/{store}/external.geotiff",
        params={"configure": "first", "coverageName": store},
        data=container_path,
        headers={"Content-Type": "text/plain"},
        timeout=600,
    ))
    cov = gs.get(f"/workspaces/{WORKSPACE}/coveragestores/{store}/coverages/{store}").json()
    return [(store, cov["coverage"]["latLonBoundingBox"])]


def _publish_shapefile(gs: GeoServer, store: str, container_path: str) -> list[tuple[str, dict]]:
    gs._check(gs.s.put(
        f"{gs.rest}/workspaces/{WORKSPACE}/datastores/{store}/external.shp",
        params={"configure": "all"},
        data=container_path,
        headers={"Content-Type": "text/plain"},
        timeout=600,
    ))
    ft = gs.get(f"/workspaces/{WORKSPACE}/datastores/{store}/featuretypes/{store}").json()
    return [(store, ft["featureType"]["latLonBoundingBox"])]


def _publish_geopackage(gs: GeoServer, store: str, container_path: str) -> list[tuple[str, dict]]:
    gs.post_json(f"/workspaces/{WORKSPACE}/datastores", {"dataStore": {
        "name": store,
        "connectionParameters": {"entry": [
            {"@key": "database", "$": container_path},
            {"@key": "dbtype", "$": "geopkg"},
        ]},
    }})
    path = f"/workspaces/{WORKSPACE}/datastores/{store}/featuretypes"
    available = gs.get(f"{path}?list=available").json().get("list", {}).get("string", [])
    if isinstance(available, str):
        available = [available]
    if not available:
        raise SystemExit("В GeoPackage не найдено векторных таблиц")
    published = []
    for table in available:
        name = store if len(available) == 1 else layer_id(store, slug(table))
        gs.post_json(path, {"featureType": {"name": name, "nativeName": table, "title": table}})
        ft = gs.get(f"{path}/{name}").json()
        published.append((name, ft["featureType"]["latLonBoundingBox"]))
    return published


def store_name(farm: str, field: str, date: dt.date, stem: str) -> str:
    """Layer name: farm_field_date plus whatever the file name adds beyond date and field."""
    farm_s, field_s = slug(farm), slug(field)
    extra = slug(stem)
    for token in (date.strftime("%d%m%Y"), date.strftime("%Y%m%d"), date.isoformat().replace("-", "_"), field_s):
        extra = extra.replace(token, "")
    extra = re.sub(r"_+", "_", extra).strip("_")
    return layer_id(farm_s, field_s, date.strftime("%Y%m%d"), extra)


def ingest_file(cfg: Config, file: str | Path, *, farm: str, field: str, date: dt.date | str,
                title: str | None = None, move: bool = False, replace: bool = False,
                progress=None, log=print) -> list[str]:
    """Copies a survey file into the archive hierarchy and publishes it. Returns layer names."""
    src = Path(file).expanduser().resolve()
    if not src.exists():
        raise SystemExit(f"Файл не найден: {src}")
    ext = src.suffix.lower()
    if ext in RASTER_EXT:
        kind = "raster"
    elif ext in VECTOR_EXT:
        kind = "vector"
    else:
        raise SystemExit(f"Неподдерживаемый формат {ext}. Растр: .tif/.tiff, вектор: .gpkg/.shp")

    if isinstance(date, str):
        try:
            date = dt.date.fromisoformat(date)
        except ValueError:
            raise SystemExit("Дата должна быть в формате YYYY-MM-DD")

    store = store_name(farm, field, date, src.stem)
    rel_dir = Path(slug(farm)) / slug(field) / date.isoformat() / kind
    gs = GeoServer(cfg)

    store_kind = "coveragestores" if kind == "raster" else "datastores"
    if gs.exists(f"/workspaces/{WORKSPACE}/{store_kind}/{store}"):
        if not replace:
            raise SystemExit(f"Слой {store} уже существует. Включите перезапись (--replace).")
        _remove_store(gs, store)

    needed = src.stat().st_size
    cfg.host_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(cfg.host_dir).free
    if needed > free:
        raise SystemExit(f"Не хватает места: нужно {needed / 2**30:.1f} ГБ, свободно {free / 2**30:.1f} ГБ")

    if ext == ".shp":
        dst = cfg.host_dir / rel_dir / f"{store}.shp"
        for side in SHP_SIDECARS:
            part = src.with_suffix(side)
            if not part.exists():
                part = src.with_suffix(side.upper())
            if part.exists():
                _transfer(part, dst.with_suffix(side), move)
    else:
        dst = cfg.host_dir / rel_dir / f"{store}{ext}"
        _transfer(src, dst, move, progress)
    container_path = f"{cfg.container_dir}/{(rel_dir / dst.name).as_posix()}"
    log(f"[+] файл: {dst}")

    if ext in RASTER_EXT:
        published = _publish_raster(gs, store, container_path)
    elif ext == ".shp":
        published = _publish_shapefile(gs, store, container_path)
    else:
        published = _publish_geopackage(gs, store, container_path)

    title = title or f"{farm} / {field} / {date.isoformat()}"
    for name, bbox in published:
        _register_survey(gs, layer=name, farm=farm, field=field, date=date, kind=kind,
                         title=title if len(published) == 1 else f"{title} / {name}",
                         file_path=container_path, bbox=bbox)
        log(f"[+] опубликован слой {WORKSPACE}:{name}")
    return [name for name, _ in published]


def cmd_ingest(cfg: Config, args) -> None:
    ingest_file(cfg, args.file, farm=args.farm, field=args.field, date=args.date,
                title=args.title, move=args.move, replace=args.replace)


# ---------------------------------------------------------------- list / remove

def _remove_store(gs: GeoServer, store: str) -> None:
    for kind in ("coveragestores", "datastores"):
        gs.delete(f"/workspaces/{WORKSPACE}/{kind}/{store}?recurse=true")
    for f in gs.wfs_features("surveys", f"file_path LIKE '%/{store}.%'"):
        _unregister_survey(gs, f["properties"]["layer_name"])


def cmd_list(cfg: Config, args) -> None:
    feats = GeoServer(cfg).wfs_features("surveys")
    if args.json:
        print(json.dumps([f["properties"] for f in feats], ensure_ascii=False, indent=2))
        return
    feats.sort(key=lambda f: (f["properties"]["farm"], f["properties"]["field"], f["properties"]["survey_date"]))
    for f in feats:
        p = f["properties"]
        print(f"{p['survey_date'][:10]}  {p['kind']:6}  {p['farm']} / {p['field']}  ->  {WORKSPACE}:{p['layer_name']}")
    print(f"Всего: {len(feats)}")


def cmd_remove(cfg: Config, args) -> None:
    gs = GeoServer(cfg)
    feats = gs.wfs_features("surveys", f"layer_name = '{args.layer.replace(chr(39), chr(39) * 2)}'")
    if not feats:
        raise SystemExit(f"Слой {args.layer} не найден в каталоге")
    file_path = feats[0]["properties"]["file_path"]
    store = Path(file_path).stem
    _remove_store(gs, store)
    print(f"[-] слой и хранилище {store} удалены из GeoServer (файл на диске сохранён: {file_path})")


def main() -> None:
    p = argparse.ArgumentParser(description="Повилика: управление геоданными GeoServer")
    p.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("setup", help="первичная настройка GeoServer")

    ing = sub.add_parser("ingest", help="загрузить и опубликовать файл съёмки")
    ing.add_argument("file")
    ing.add_argument("--farm", required=True, help="хозяйство")
    ing.add_argument("--field", required=True, help="поле")
    ing.add_argument("--date", required=True, help="дата съёмки YYYY-MM-DD")
    ing.add_argument("--title")
    ing.add_argument("--move", action="store_true", help="переместить файл вместо копирования")
    ing.add_argument("--replace", action="store_true", help="перезаписать существующий слой")

    ls = sub.add_parser("list", help="каталог опубликованных съёмок")
    ls.add_argument("--json", action="store_true")

    rm = sub.add_parser("remove", help="снять слой с публикации")
    rm.add_argument("layer")

    args = p.parse_args()
    cfg = Config.from_env(args.env_file)
    {"setup": cmd_setup, "ingest": cmd_ingest, "list": cmd_list, "remove": cmd_remove}[args.cmd](cfg, args)


if __name__ == "__main__":
    try:
        main()
    except requests.ConnectionError as e:
        sys.exit(f"Нет связи с GeoServer: {e}")
