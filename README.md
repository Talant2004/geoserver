# Повилика — модуль геоданных

Центральное хранилище съёмок (GeoServer + PostGIS на домашнем сервере, доступ через Cloudflare Tunnel) и мобильное приложение на Flutter: просмотр карт, оконтуривание очагов *Cuscuta campestris*, полётное задание для агродрона (PDF + KML).

```
server/   docker-compose: GeoServer, PostGIS, cloudflared; схема БД; стиль очагов
tools/    povilika_cli.py — настройка GeoServer и загрузка файлов по иерархии
app/      Flutter-приложение «Повилика»
```

## Как это устроено

- Файлы съёмок лежат на диске сервера: `server/data/surveys/<хозяйство>/<поле>/<дата>/<raster|vector>/`.
- `povilika_cli.py ingest` копирует файл туда, публикует его в GeoServer (рабочая область `povilika`) и добавляет запись в каталог — слой `povilika:surveys` в PostGIS.
- Приложение читает каталог по WFS, показывает снимки кэшированными тайлами GeoWebCache (TMS), а очаги сохраняет в слой `povilika:foci` через WFS-T. Отдельного backend нет.
- Чтение доступно анонимно, запись очагов — только пользователю приложения (`APP_USER`) и администратору.

## 1. Сервер

Требования: Ubuntu Server 22.04+, Docker Engine с плагином Compose, Python 3.9+, 8 ГБ ОЗУ, место под снимки.

```bash
cd server
cp .env.example .env        # заменить все пароли, указать PUBLIC_HOSTNAME
docker compose up -d        # GeoServer + PostGIS
pip install -r ../tools/requirements.txt
python3 ../tools/povilika_cli.py setup
```

Админка GeoServer: `http://localhost:8080/geoserver` (логин/пароль из `.env`).

GeoServer ходит в базу под отдельной ролью `GEOSERVER_DB_USER`: она видит только таблицы `surveys` и `foci` и не может ничего, кроме чтения, добавления и удаления строк. Роль создаётся при первом запуске PostGIS (пустой `data/postgis`).

### Обновления безопасности

GeoServer открыт в интернет, поэтому обновлять его нужно сразу после выхода исправлений безопасности ([объявления](https://geoserver.org/announcements/)). Минимальная версия — 2.28.5 или 3.0.1 (CVE-2026-76904, SQL-инъекция в PostGIS без авторизации).

```bash
# в server/.env поменять GEOSERVER_VERSION, затем:
docker compose pull geoserver && docker compose up -d geoserver
```

## 2. Cloudflare Tunnel

1. Домен должен обслуживаться DNS Cloudflare (NS-записи у регистратора указывают на Cloudflare).
2. Cloudflare Zero Trust → Networks → Tunnels → Create tunnel → Cloudflared. Скопируйте токен в `TUNNEL_TOKEN` в `server/.env`.
3. В туннеле добавьте Public Hostname: поддомен `maps`, домен ваш, сервис `HTTP` → `geoserver:8080`.
4. Запустите коннектор и повторите setup (он пропишет публичный адрес в GeoServer):

```bash
docker compose --profile tunnel up -d
python3 ../tools/povilika_cli.py setup
```

Проверка: `https://maps.<домен>/geoserver/web/`.

## 3. Загрузка съёмок

**Кнопками:** ярлык «Повилика — загрузка снимков» на рабочем столе (или `python3 tools/povilika_gui.py`). «Выбрать папку…» → проверить хозяйство, поле и дату (берутся из имени вида `19082026_Поле_RGB.tif`, двойной щелчок — исправить) → «Загрузить на сервер». Вкладка «Опубликованные» — просмотр на карте и снятие с публикации.

**Из командной строки:**

Запускается на сервере (или на рабочей станции, если `SURVEYS_HOST_DIR` — смонтированная сетевая папка сервера, а `GEOSERVER_URL` указывает на сервер). Большие файлы не идут через туннель: у Cloudflare лимит 100 МБ на запрос.

```bash
python3 tools/povilika_cli.py ingest ortho.tif --farm "Береке" --field "Поле 12" --date 2026-07-15
python3 tools/povilika_cli.py ingest detections.gpkg --farm "Береке" --field "Поле 12" --date 2026-07-15
python3 tools/povilika_cli.py list
python3 tools/povilika_cli.py remove bereke_pole_12_20260715_ortho
```

Форматы: растр — GeoTIFF (`.tif`), вектор — GeoPackage (`.gpkg`) или Shapefile (`.shp` с `.dbf/.shx/.prj` рядом). Для тяжёлых ортофотопланов заранее переведите их в COG, чтобы GeoServer не читал файл целиком:

```bash
gdal_translate ortho.tif ortho_cog.tif -of COG -co COMPRESS=DEFLATE -co BIGTIFF=IF_SAFER
```

Опции: `--move` (переместить вместо копирования), `--replace` (перезаписать слой), `--title`.

Тестовый снимок без реальных данных: `python3 tools/make_test_ortho.py test.tif` — синтетическое поле с пятнами под Алматы.

### Поиск очагов по снимку

Вкладка «Опубликованные» → выбрать растр → «Найти очаги…». Красным на превью подсвечиваются пиксели цвета повилики, ползунок меняет чувствительность (обычно 55–65; на 80 и выше в маску попадают почва и сухая трава). «Рассчитать очаги» строит контуры с буфером, «Отправить в приложение» заменяет прежние автоматические очаги этого снимка новыми. Ручные очаги не трогаются. В приложении найденные очаги оранжевые: лишние агроном удаляет нажатием на очаг, пропущенные обводит вручную.

Для поиска нужны numpy, rasterio и shapely. Они стоят в `tools/.venv`, и ярлык запускает программу оттуда. Установка с нуля:

```bash
python3 -m venv --system-site-packages tools/.venv && tools/.venv/bin/pip install -r tools/requirements.txt
```

## 4. Мобильное приложение

Требуется Flutter 3.22+.

```bash
cd app
flutter create . --project-name povilika --org kz.povilika --platforms android,ios
flutter pub get
flutter run
```

Для релизной сборки Android добавьте в `android/app/src/main/AndroidManifest.xml` (внутри `<manifest>`):

```xml
<uses-permission android:name="android.permission.INTERNET"/>
```

При первом запуске откройте «Настройки»: адрес `https://maps.<домен>/geoserver`, пользователь и пароль — `APP_USER` / `APP_PASSWORD` из `server/.env`.

Сценарий: «Карты» → выбрать съёмку → «Оконтурить очаг» (нажатия по карте) → «Сохранить» → кнопка «Полётное задание» → ввести норму расхода рабочего раствора (л/га) → PDF и KML для пульта агродрона.

Объём рабочего раствора = норма (л/га) × суммарная площадь очагов (га). Если указана норма препарата, отдельно считается объём препарата.
