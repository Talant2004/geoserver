import 'package:flutter_test/flutter_test.dart';
import 'package:povilika/server_locator.dart';

void main() {
  test('адрес из файла сервера', () {
    expect(
      parseServerUrl('{"url": "https://a-b-c-d.trycloudflare.com/geoserver/", "updated": "2026-10-13T13:00:00+05:00"}'),
      'https://a-b-c-d.trycloudflare.com/geoserver',
    );
  });

  test('мусор и не-https отвергаются', () {
    expect(parseServerUrl('not json'), isNull);
    expect(parseServerUrl('{"url": 5}'), isNull);
    expect(parseServerUrl('{"url": "http://evil.example/geoserver"}'), isNull);
    expect(parseServerUrl('{}'), isNull);
  });
}
