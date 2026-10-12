import 'dart:math' as math;

import 'package:flutter_test/flutter_test.dart';
import 'package:latlong2/latlong.dart';
import 'package:povilika/geo.dart';

void main() {
  test('квадрат 100×100 м — около 1 га', () {
    const lat = 43.25, lon = 76.9;
    const dLat = 100 / 111320;
    final dLon = 100 / (111320 * math.cos(lat * math.pi / 180));
    final square = [
      const LatLng(lat, lon),
      LatLng(lat, lon + dLon),
      LatLng(lat + dLat, lon + dLon),
      const LatLng(lat + dLat, lon),
    ];
    expect(polygonAreaHa(square), closeTo(1.0, 0.001));
  });

  test('охват сохраняет заданное соотношение сторон', () {
    final box = MercatorBox.around(
      const [LatLng(43.25, 76.90), LatLng(43.26, 76.95)],
      aspect: 0.6,
    );
    expect(box.height / box.width, closeTo(0.6, 1e-9));
  });
}
