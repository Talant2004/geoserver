import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'package:printing/printing.dart';
import 'package:share_plus/share_plus.dart';

import '../geo.dart';
import '../geoserver_api.dart';
import '../models.dart';
import '../report/flight_task.dart';

class ReportScreen extends StatefulWidget {
  const ReportScreen({
    super.key,
    required this.api,
    required this.survey,
    required this.hotspots,
  });

  final GeoServerApi api;
  final Survey survey;
  final List<Hotspot> hotspots;

  @override
  State<ReportScreen> createState() => _ReportScreenState();
}

class _ReportScreenState extends State<ReportScreen> {
  final _solutionRate = TextEditingController(text: '15');
  final _productRate = TextEditingController();
  bool _busy = false;

  @override
  void dispose() {
    _solutionRate.dispose();
    _productRate.dispose();
    super.dispose();
  }

  double? _parse(TextEditingController c) =>
      double.tryParse(c.text.trim().replaceAll(',', '.'));

  FlightTask? get _task {
    final rate = _parse(_solutionRate);
    if (rate == null || rate <= 0) return null;
    final product = _parse(_productRate);
    return FlightTask(
      survey: widget.survey,
      hotspots: widget.hotspots,
      solutionRate: rate,
      productRate: product != null && product > 0 ? product : null,
      organization: widget.api.settings.organization,
    );
  }

  Future<void> _withTask(Future<void> Function(FlightTask t) action) async {
    final t = _task;
    if (t == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Укажите норму расхода рабочего раствора')),
      );
      return;
    }
    setState(() => _busy = true);
    try {
      await action(t);
    } on Exception catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Ошибка: $e')));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _previewPdf() => _withTask((t) async {
        final bytes = await buildPdf(t, widget.api);
        await Printing.layoutPdf(onLayout: (_) async => bytes, name: '${t.fileBaseName}.pdf');
      });

  Future<void> _sharePackage() => _withTask((t) async {
        final dir = await getTemporaryDirectory();
        final pdf = File('${dir.path}/${t.fileBaseName}.pdf');
        final kml = File('${dir.path}/${t.fileBaseName}.kml');
        await pdf.writeAsBytes(await buildPdf(t, widget.api));
        await kml.writeAsBytes(utf8.encode(buildKml(t)));
        await Share.shareXFiles(
          [
            XFile(pdf.path, mimeType: 'application/pdf'),
            XFile(kml.path, mimeType: 'application/vnd.google-earth.kml+xml'),
          ],
          subject: 'Полётное задание — ${t.survey.farm} / ${t.survey.field}',
        );
      });

  @override
  Widget build(BuildContext context) {
    final t = _task;
    final area = widget.hotspots.fold(0.0, (s, h) => s + h.areaHa);
    final text = Theme.of(context).textTheme;
    return Scaffold(
      appBar: AppBar(title: const Text('Полётное задание')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text('${widget.survey.farm} / ${widget.survey.field}', style: text.titleMedium),
          const SizedBox(height: 4),
          const Text('Вредный организм: $pestName', style: TextStyle(fontStyle: FontStyle.italic)),
          Text('Очагов: ${widget.hotspots.length} · площадь ${fmtNum(area, 3)} га'),
          const SizedBox(height: 20),
          TextField(
            controller: _solutionRate,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: const InputDecoration(
              labelText: 'Норма расхода рабочего раствора',
              suffixText: 'л/га',
            ),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _productRate,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: const InputDecoration(
              labelText: 'Норма расхода препарата (необязательно)',
              suffixText: 'л/га',
            ),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 20),
          Card(
            color: Theme.of(context).colorScheme.primaryContainer,
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Объём рабочего раствора', style: text.labelLarge),
                  Text(t == null ? '—' : '${fmtNum(t.solutionVolume, 1)} л', style: text.headlineMedium),
                  if (t?.productVolume != null) ...[
                    const SizedBox(height: 8),
                    Text('Объём препарата', style: text.labelLarge),
                    Text('${fmtNum(t!.productVolume!, 2)} л', style: text.titleLarge),
                  ],
                ],
              ),
            ),
          ),
          const SizedBox(height: 20),
          if (_busy) const LinearProgressIndicator(),
          const SizedBox(height: 8),
          FilledButton.icon(
            onPressed: _busy ? null : _previewPdf,
            icon: const Icon(Icons.picture_as_pdf),
            label: const Text('Просмотр / печать PDF'),
          ),
          const SizedBox(height: 8),
          OutlinedButton.icon(
            onPressed: _busy ? null : _sharePackage,
            icon: const Icon(Icons.share),
            label: const Text('Отправить PDF + KML'),
          ),
        ],
      ),
    );
  }
}
