import 'package:latlong2/latlong.dart';

List<LatLng> _ring(Map<String, dynamic>? geometry) {
  if (geometry == null) return const [];
  final rings = geometry['coordinates'] as List;
  if (rings.isEmpty) return const [];
  final pts = (rings.first as List)
      .map((c) => LatLng((c[1] as num).toDouble(), (c[0] as num).toDouble()))
      .toList();
  if (pts.length > 1 && pts.first == pts.last) pts.removeLast();
  return pts;
}

DateTime? _date(Object? v) {
  if (v is! String || v.isEmpty) return null;
  return DateTime.tryParse(v) ?? DateTime.tryParse(v.substring(0, 10));
}

class Survey {
  const Survey({
    required this.layerName,
    required this.farm,
    required this.field,
    required this.date,
    required this.kind,
    required this.title,
    required this.extent,
  });

  final String layerName;
  final String farm;
  final String field;
  final DateTime date;
  final String kind;
  final String title;
  final List<LatLng> extent;

  bool get isRaster => kind == 'raster';

  factory Survey.fromFeature(Map<String, dynamic> f) {
    final p = f['properties'] as Map<String, dynamic>;
    return Survey(
      layerName: p['layer_name'] as String,
      farm: p['farm'] as String? ?? '',
      field: p['field'] as String? ?? '',
      date: _date(p['survey_date']) ?? DateTime(1970),
      kind: p['kind'] as String? ?? 'raster',
      title: p['title'] as String? ?? p['layer_name'] as String,
      extent: _ring(f['geometry'] as Map<String, dynamic>?),
    );
  }
}

/// Очаг поражения: оконтурен вручную или найден автоматически по снимку.
class Hotspot {
  const Hotspot({
    this.fid,
    required this.surveyLayer,
    required this.points,
    required this.areaHa,
    this.createdAt,
    this.note,
    this.source,
  });

  final String? fid;
  final String surveyLayer;
  final List<LatLng> points;
  final double areaHa;
  final DateTime? createdAt;
  final String? note;
  final String? source;

  bool get isAuto => source == 'auto';

  factory Hotspot.fromFeature(Map<String, dynamic> f) {
    final p = f['properties'] as Map<String, dynamic>;
    return Hotspot(
      fid: f['id'] as String?,
      surveyLayer: p['survey_layer'] as String? ?? '',
      points: _ring(f['geometry'] as Map<String, dynamic>?),
      areaHa: (p['area_ha'] as num?)?.toDouble() ?? 0,
      createdAt: _date(p['created_at']),
      note: p['note'] as String?,
      source: p['source'] as String?,
    );
  }
}
