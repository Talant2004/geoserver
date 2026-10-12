import 'dart:math' as math;

import 'package:latlong2/latlong.dart';

const double _earthRadius = 6378137.0;

double _rad(double deg) => deg * math.pi / 180;

/// Площадь полигона на сфере, м².
double polygonAreaM2(List<LatLng> points) {
  if (points.length < 3) return 0;
  var total = 0.0;
  for (var i = 0; i < points.length; i++) {
    final a = points[i];
    final b = points[(i + 1) % points.length];
    total += _rad(b.longitude - a.longitude) *
        (2 + math.sin(_rad(a.latitude)) + math.sin(_rad(b.latitude)));
  }
  return (total * _earthRadius * _earthRadius / 2).abs();
}

double polygonAreaHa(List<LatLng> points) => polygonAreaM2(points) / 10000;

LatLng centroid(List<LatLng> points) {
  final lat = points.map((p) => p.latitude).reduce((a, b) => a + b) / points.length;
  final lon = points.map((p) => p.longitude).reduce((a, b) => a + b) / points.length;
  return LatLng(lat, lon);
}

/// Проекция Web Mercator (EPSG:3857), метры.
math.Point<double> toMercator(LatLng p) => math.Point(
      _earthRadius * _rad(p.longitude),
      _earthRadius * math.log(math.tan(math.pi / 4 + _rad(p.latitude) / 2)),
    );

class MercatorBox {
  MercatorBox(this.minX, this.minY, this.maxX, this.maxY);

  final double minX, minY, maxX, maxY;

  double get width => maxX - minX;
  double get height => maxY - minY;

  /// Охват точек с полями [margin] (доля) и заданным отношением высоты к ширине.
  factory MercatorBox.around(Iterable<LatLng> points,
      {double margin = 0.2, double aspect = 0.6, double minSize = 100}) {
    final m = points.map(toMercator).toList();
    var minX = m.map((p) => p.x).reduce(math.min);
    var maxX = m.map((p) => p.x).reduce(math.max);
    var minY = m.map((p) => p.y).reduce(math.min);
    var maxY = m.map((p) => p.y).reduce(math.max);
    final cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    var w = math.max((maxX - minX) * (1 + 2 * margin), minSize);
    var h = math.max((maxY - minY) * (1 + 2 * margin), minSize * aspect);
    if (h / w > aspect) {
      w = h / aspect;
    } else {
      h = w * aspect;
    }
    return MercatorBox(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2);
  }

  String get bboxParam => '$minX,$minY,$maxX,$maxY';
}

String fmtNum(double v, [int digits = 2]) => v.toStringAsFixed(digits).replaceAll('.', ',');
