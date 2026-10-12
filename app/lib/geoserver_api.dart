import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import 'geo.dart';
import 'models.dart';
import 'settings.dart';

const workspace = 'povilika';
const namespaceUri = 'urn:povilika';
const pestName = 'Cuscuta campestris';

class GeoServerException implements Exception {
  GeoServerException(this.message);
  final String message;
  @override
  String toString() => message;
}

String _xml(String s) => s
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');

class GeoServerApi {
  GeoServerApi(this.settings);

  final AppSettings settings;
  static const _timeout = Duration(seconds: 30);

  String get base => settings.serverUrl;

  Map<String, String> get _authHeader => {
        'Authorization':
            'Basic ${base64Encode(utf8.encode('${settings.username}:${settings.password}'))}',
      };

  /// Кэшированные тайлы GeoWebCache (TMS, сетка EPSG:900913).
  String tileUrlTemplate(String layer) =>
      '$base/gwc/service/tms/1.0.0/$workspace:$layer@EPSG:900913@png/{z}/{x}/{y}.png';

  Future<List<Map<String, dynamic>>> _features(String typeName, {String? cql}) async {
    final uri = Uri.parse('$base/$workspace/wfs').replace(queryParameters: {
      'service': 'WFS',
      'version': '1.1.0',
      'request': 'GetFeature',
      'typeName': '$workspace:$typeName',
      'outputFormat': 'application/json',
      'srsName': 'EPSG:4326',
      if (cql != null) 'CQL_FILTER': cql,
    });
    final r = await http.get(uri).timeout(_timeout);
    if (r.statusCode != 200) {
      throw GeoServerException('Сервер ответил ${r.statusCode}');
    }
    final data = jsonDecode(utf8.decode(r.bodyBytes));
    if (data is! Map<String, dynamic>) {
      throw GeoServerException('Неожиданный ответ сервера');
    }
    return (data['features'] as List).cast<Map<String, dynamic>>();
  }

  Future<List<Survey>> surveys() async {
    final list = (await _features('surveys')).map(Survey.fromFeature).toList();
    list.sort((a, b) => b.date.compareTo(a.date));
    return list;
  }

  Future<List<Hotspot>> hotspots(String surveyLayer) async {
    final cql = "survey_layer = '${surveyLayer.replaceAll("'", "''")}'";
    return (await _features('foci', cql: cql)).map(Hotspot.fromFeature).toList();
  }

  Future<String> _transaction(String body) async {
    final xml = '<?xml version="1.0" encoding="UTF-8"?>'
        '<wfs:Transaction service="WFS" version="1.1.0" '
        'xmlns:wfs="http://www.opengis.net/wfs" xmlns:gml="http://www.opengis.net/gml" '
        'xmlns:ogc="http://www.opengis.net/ogc" xmlns:$workspace="$namespaceUri">'
        '$body</wfs:Transaction>';
    final r = await http
        .post(
          Uri.parse('$base/$workspace/wfs'),
          headers: {..._authHeader, 'Content-Type': 'text/xml; charset=utf-8'},
          body: utf8.encode(xml),
        )
        .timeout(_timeout);
    final text = utf8.decode(r.bodyBytes);
    if (r.statusCode == 401 || r.statusCode == 403) {
      throw GeoServerException('Нет прав на запись. Проверьте логин и пароль в настройках.');
    }
    if (r.statusCode != 200 || text.contains('ExceptionReport') || text.contains('ServiceException')) {
      throw GeoServerException('Ошибка сохранения (${r.statusCode})');
    }
    return text;
  }

  Future<String?> insertHotspot(Survey survey, Hotspot h) async {
    final ring = [...h.points, h.points.first];
    final posList = ring.map((p) => '${p.longitude} ${p.latitude}').join(' ');
    final props = <String, String>{
      'survey_layer': survey.layerName,
      'farm': survey.farm,
      'field': survey.field,
      'pest': pestName,
      'area_ha': h.areaHa.toStringAsFixed(6),
      if (h.note != null && h.note!.isNotEmpty) 'note': h.note!,
      'source': 'manual',
      'created_at': DateTime.now().toUtc().toIso8601String(),
    };
    final fields = props.entries.map((e) => '<$workspace:${e.key}>${_xml(e.value)}</$workspace:${e.key}>').join();
    final text = await _transaction(
      '<wfs:Insert><$workspace:foci>$fields'
      '<$workspace:geom><gml:Polygon srsName="EPSG:4326"><gml:exterior><gml:LinearRing>'
      '<gml:posList>$posList</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></$workspace:geom>'
      '</$workspace:foci></wfs:Insert>',
    );
    return RegExp(r'fid="([^"]+)"').firstMatch(text)?.group(1);
  }

  Future<void> deleteHotspot(String fid) async {
    await _transaction(
      '<wfs:Delete typeName="$workspace:foci"><ogc:Filter>'
      '<ogc:FeatureId fid="${_xml(fid)}"/></ogc:Filter></wfs:Delete>',
    );
  }

  Future<void> deleteAutoHotspots(String surveyLayer) async {
    String eq(String name, String value) => '<ogc:PropertyIsEqualTo><ogc:PropertyName>$name</ogc:PropertyName>'
        '<ogc:Literal>${_xml(value)}</ogc:Literal></ogc:PropertyIsEqualTo>';
    await _transaction(
      '<wfs:Delete typeName="$workspace:foci"><ogc:Filter><ogc:And>'
      '${eq('survey_layer', surveyLayer)}${eq('source', 'auto')}'
      '</ogc:And></ogc:Filter></wfs:Delete>',
    );
  }

  /// Статичное изображение слоя (WMS GetMap) для отчёта.
  Future<Uint8List?> mapImage(String layer, MercatorBox box, int width, int height) async {
    final uri = Uri.parse('$base/$workspace/wms').replace(queryParameters: {
      'service': 'WMS',
      'version': '1.1.1',
      'request': 'GetMap',
      'layers': '$workspace:$layer',
      'styles': '',
      'srs': 'EPSG:3857',
      'bbox': box.bboxParam,
      'width': '$width',
      'height': '$height',
      'format': 'image/png',
      'bgcolor': '0xFFFFFF',
    });
    try {
      final r = await http.get(uri).timeout(const Duration(seconds: 60));
      final type = r.headers['content-type'] ?? '';
      return r.statusCode == 200 && type.startsWith('image/') ? r.bodyBytes : null;
    } on Exception {
      return null;
    }
  }
}
