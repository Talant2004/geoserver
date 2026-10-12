import 'dart:typed_data';

import 'package:flutter/services.dart' show rootBundle;
import 'package:intl/intl.dart';
import 'package:pdf/pdf.dart';
import 'package:pdf/widgets.dart' as pw;

import '../geo.dart';
import '../geoserver_api.dart';
import '../models.dart';

/// Полётное задание для агродрона: точечная обработка очагов повилики.
class FlightTask {
  FlightTask({
    required this.survey,
    required this.hotspots,
    required this.solutionRate,
    this.productRate,
    this.organization = '',
    DateTime? createdAt,
  }) : createdAt = createdAt ?? DateTime.now();

  final Survey survey;
  final List<Hotspot> hotspots;

  /// Норма расхода рабочего раствора, л/га.
  final double solutionRate;

  /// Норма расхода препарата, л/га (необязательно).
  final double? productRate;
  final String organization;
  final DateTime createdAt;

  double get totalAreaHa => hotspots.fold(0.0, (s, h) => s + h.areaHa);
  double get solutionVolume => solutionRate * totalAreaHa;
  double? get productVolume => productRate == null ? null : productRate! * totalAreaHa;

  String get fileBaseName =>
      'zadanie_${survey.layerName}_${DateFormat('yyyyMMdd_HHmm').format(createdAt)}';
}

String _xml(String s) =>
    s.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');

String buildKml(FlightTask t) {
  final b = StringBuffer()
    ..writeln('<?xml version="1.0" encoding="UTF-8"?>')
    ..writeln('<kml xmlns="http://www.opengis.net/kml/2.2"><Document>')
    ..writeln('<name>${_xml('Полётное задание — ${t.survey.farm} / ${t.survey.field}')}</name>')
    ..writeln('<description>${_xml('$pestName; норма ${fmtNum(t.solutionRate, 1)} л/га; '
        'площадь ${fmtNum(t.totalAreaHa, 3)} га; раствор ${fmtNum(t.solutionVolume, 1)} л')}</description>')
    ..writeln('<Style id="spray"><LineStyle><color>ff0000ff</color><width>2</width></LineStyle>'
        '<PolyStyle><color>590000ff</color></PolyStyle></Style>');
  for (final (i, h) in t.hotspots.indexed) {
    final ring = [...h.points, h.points.first];
    final coords = ring.map((p) => '${p.longitude},${p.latitude},0').join(' ');
    b.writeln('<Placemark><name>Очаг ${i + 1}</name>'
        '<description>${fmtNum(h.areaHa, 4)} га</description><styleUrl>#spray</styleUrl>'
        '<Polygon><outerBoundaryIs><LinearRing><coordinates>$coords</coordinates>'
        '</LinearRing></outerBoundaryIs></Polygon></Placemark>');
  }
  b.writeln('</Document></kml>');
  return b.toString();
}

const _green = PdfColor.fromInt(0xFF2E7D32);
const _muted = PdfColor.fromInt(0xFF7F8C8D);
const _mapWidthPx = 1200;
const _mapHeightPx = 720;

Future<Uint8List> buildPdf(FlightTask t, GeoServerApi api) async {
  Future<pw.Font> font(String name) async =>
      pw.Font.ttf(await rootBundle.load('assets/fonts/Roboto-$name.ttf'));
  final theme = pw.ThemeData.withFont(
    base: await font('Regular'),
    bold: await font('Bold'),
    italic: await font('Italic'),
  );

  final allPoints = t.hotspots.expand((h) => h.points);
  final box = MercatorBox.around(allPoints, aspect: _mapHeightPx / _mapWidthPx);
  final image = await api.mapImage(t.survey.layerName, box, _mapWidthPx, _mapHeightPx);

  final dateFmt = DateFormat('dd.MM.yyyy');
  final doc = pw.Document(title: 'Полётное задание', creator: 'Повилика');

  doc.addPage(pw.MultiPage(
    pageFormat: PdfPageFormat.a4,
    margin: const pw.EdgeInsets.all(36),
    theme: theme,
    footer: (ctx) => pw.Container(
      padding: const pw.EdgeInsets.only(top: 8),
      decoration: const pw.BoxDecoration(
        border: pw.Border(top: pw.BorderSide(color: PdfColors.grey300)),
      ),
      child: pw.Row(
        mainAxisAlignment: pw.MainAxisAlignment.spaceBetween,
        children: [
          pw.Text('Сформировано в мобильном приложении «Повилика»',
              style: const pw.TextStyle(fontSize: 9, color: _muted)),
          pw.Text('Стр. ${ctx.pageNumber} из ${ctx.pagesCount}',
              style: const pw.TextStyle(fontSize: 9, color: _muted)),
        ],
      ),
    ),
    build: (ctx) => [
      _header(t),
      pw.SizedBox(height: 16),
      _parameters(t, dateFmt),
      pw.SizedBox(height: 18),
      pw.Text('Карта-схема зон обработки',
          style: const pw.TextStyle(fontSize: 13, fontWeight: pw.FontWeight.bold)),
      pw.SizedBox(height: 8),
      _map(t, box, image),
      pw.SizedBox(height: 4),
      pw.Text('Контуры зон обработки прилагаются в формате KML для загрузки в пульт агродрона.',
          style: const pw.TextStyle(fontSize: 9, color: _muted)),
      pw.SizedBox(height: 16),
      _hotspotTable(t),
      pw.SizedBox(height: 36),
      pw.Row(
        mainAxisAlignment: pw.MainAxisAlignment.spaceBetween,
        children: [
          pw.Text('Ответственный агроном: ______________________'),
          pw.Text('Оператор БПЛА: ______________________'),
        ],
      ),
    ],
  ));
  return doc.save();
}

pw.Widget _header(FlightTask t) {
  return pw.Container(
    padding: const pw.EdgeInsets.only(bottom: 12),
    decoration: const pw.BoxDecoration(
      border: pw.Border(bottom: pw.BorderSide(color: _green, width: 2)),
    ),
    child: pw.Row(
      mainAxisAlignment: pw.MainAxisAlignment.spaceBetween,
      crossAxisAlignment: pw.CrossAxisAlignment.end,
      children: [
        pw.Column(crossAxisAlignment: pw.CrossAxisAlignment.start, children: [
          pw.Text('Полётное задание БПЛА',
              style: const pw.TextStyle(fontSize: 20, fontWeight: pw.FontWeight.bold)),
          pw.Text('Технологическая карта точечного опрыскивания',
              style: const pw.TextStyle(fontSize: 10, color: _muted)),
        ]),
        pw.Column(crossAxisAlignment: pw.CrossAxisAlignment.end, children: [
          pw.Text('Проект «Повилика»', style: const pw.TextStyle(fontWeight: pw.FontWeight.bold)),
          if (t.organization.isNotEmpty)
            pw.Text(t.organization, style: const pw.TextStyle(fontSize: 9, color: _muted)),
        ]),
      ],
    ),
  );
}

pw.Widget _parameters(FlightTask t, DateFormat dateFmt) {
  pw.TableRow row(String label, pw.Widget value, {bool highlight = false}) => pw.TableRow(
        decoration: highlight ? const pw.BoxDecoration(color: PdfColor.fromInt(0xFFE8F5E9)) : null,
        children: [
          pw.Padding(
            padding: const pw.EdgeInsets.symmetric(vertical: 5, horizontal: 6),
            child: pw.Text(label, style: const pw.TextStyle(color: _muted)),
          ),
          pw.Padding(padding: const pw.EdgeInsets.symmetric(vertical: 5, horizontal: 6), child: value),
        ],
      );
  pw.Widget bold(String s, {double size = 11}) =>
      pw.Text(s, style: pw.TextStyle(fontWeight: pw.FontWeight.bold, fontSize: size));

  return pw.Table(
    columnWidths: const {0: pw.FlexColumnWidth(1.1), 1: pw.FlexColumnWidth(1.4)},
    border: const pw.TableBorder(horizontalInside: pw.BorderSide(color: PdfColors.grey200)),
    children: [
      row('Вредный организм', pw.RichText(text: const pw.TextSpan(children: [
        pw.TextSpan(text: pestName, style: pw.TextStyle(fontStyle: pw.FontStyle.italic)),
        pw.TextSpan(text: ' (повилика полевая)'),
      ]))),
      row('Хозяйство / поле', bold('${t.survey.farm} / ${t.survey.field}')),
      row('Дата съёмки', bold(dateFmt.format(t.survey.date))),
      row('Норма расхода рабочего раствора', bold('${fmtNum(t.solutionRate, 1)} л/га')),
      row('Общая площадь обработки', bold('${fmtNum(t.totalAreaHa, 3)} га (очагов: ${t.hotspots.length})')),
      row('Объём рабочего раствора', bold('${fmtNum(t.solutionVolume, 1)} л', size: 14), highlight: true),
      if (t.productRate != null) ...[
        row('Норма расхода препарата', bold('${fmtNum(t.productRate!, 2)} л/га')),
        row('Объём препарата', bold('${fmtNum(t.productVolume!, 2)} л', size: 14), highlight: true),
      ],
      row('Дата создания задания', bold(DateFormat('dd.MM.yyyy HH:mm').format(t.createdAt))),
    ],
  );
}

pw.Widget _map(FlightTask t, MercatorBox box, Uint8List? image) {
  const width = 523.0;
  const height = width * _mapHeightPx / _mapWidthPx;
  return pw.Container(
    decoration: pw.BoxDecoration(border: pw.Border.all(color: PdfColors.grey400)),
    child: pw.CustomPaint(
      size: const PdfPoint(width, height),
      foregroundPainter: (canvas, size) {
        for (final h in t.hotspots) {
          final pts = h.points.map((p) {
            final m = toMercator(p);
            return PdfPoint(
              (m.x - box.minX) / box.width * size.x,
              (m.y - box.minY) / box.height * size.y,
            );
          }).toList();
          void path() {
            canvas.moveTo(pts.first.x, pts.first.y);
            for (final p in pts.skip(1)) {
              canvas.lineTo(p.x, p.y);
            }
            canvas.closePath();
          }

          canvas
            ..saveContext()
            ..setGraphicState(const PdfGraphicState(opacity: 0.35))
            ..setFillColor(PdfColors.red);
          path();
          canvas
            ..fillPath()
            ..restoreContext()
            ..setStrokeColor(PdfColors.red900)
            ..setLineWidth(1.2);
          path();
          canvas.strokePath();
        }
      },
      child: image != null
          ? pw.Image(pw.MemoryImage(image), width: width, height: height, fit: pw.BoxFit.fill)
          : pw.Container(
              width: width,
              height: height,
              color: PdfColors.grey100,
              alignment: pw.Alignment.bottomLeft,
              padding: const pw.EdgeInsets.all(6),
              child: pw.Text('Подложка недоступна — показаны только контуры',
                  style: const pw.TextStyle(fontSize: 8, color: _muted)),
            ),
    ),
  );
}

pw.Widget _hotspotTable(FlightTask t) {
  return pw.TableHelper.fromTextArray(
    headers: ['№', 'Площадь, га', 'Центр (широта, долгота)', 'Раствор, л'],
    data: [
      for (final (i, h) in t.hotspots.indexed)
        [
          '${i + 1}',
          fmtNum(h.areaHa, 4),
          () {
            final c = centroid(h.points);
            return '${c.latitude.toStringAsFixed(6)}, ${c.longitude.toStringAsFixed(6)}';
          }(),
          fmtNum(h.areaHa * t.solutionRate, 2),
        ],
    ],
    headerStyle: const pw.TextStyle(fontWeight: pw.FontWeight.bold, fontSize: 9, color: PdfColors.white),
    headerDecoration: const pw.BoxDecoration(color: _green),
    cellStyle: const pw.TextStyle(fontSize: 9),
    cellAlignments: {
      0: pw.Alignment.center,
      1: pw.Alignment.centerRight,
      2: pw.Alignment.center,
      3: pw.Alignment.centerRight,
    },
  );
}
