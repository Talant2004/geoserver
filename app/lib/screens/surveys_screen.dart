import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../geoserver_api.dart';
import '../models.dart';
import '../settings.dart';
import 'map_screen.dart';
import 'settings_screen.dart';

class SurveysScreen extends StatefulWidget {
  const SurveysScreen({super.key, required this.initialSettings});

  final AppSettings initialSettings;

  @override
  State<SurveysScreen> createState() => _SurveysScreenState();
}

class _SurveysScreenState extends State<SurveysScreen> {
  late AppSettings _settings = widget.initialSettings;
  late GeoServerApi _api = GeoServerApi(_settings);
  Future<List<Survey>>? _future;
  final _dateFmt = DateFormat('dd.MM.yyyy');

  @override
  void initState() {
    super.initState();
    if (_settings.isConfigured) {
      _future = _api.surveys();
    } else {
      WidgetsBinding.instance.addPostFrameCallback((_) => _openSettings());
    }
  }

  Future<void> _reload() async {
    final f = _api.surveys();
    setState(() => _future = f);
    await f.catchError((_) => <Survey>[]);
  }

  Future<void> _openSettings() async {
    final s = await Navigator.push<AppSettings>(
      context,
      MaterialPageRoute(builder: (_) => SettingsScreen(settings: _settings)),
    );
    if (s == null) return;
    setState(() {
      _settings = s;
      _api = GeoServerApi(s);
      _future = _api.surveys();
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Карты'),
        actions: [
          IconButton(icon: const Icon(Icons.settings), onPressed: _openSettings),
        ],
      ),
      body: _future == null
          ? const Center(child: Text('Укажите адрес сервера в настройках'))
          : FutureBuilder<List<Survey>>(
              future: _future,
              builder: (context, snap) {
                if (snap.connectionState != ConnectionState.done) {
                  return const Center(child: CircularProgressIndicator());
                }
                if (snap.hasError) {
                  return _Message(
                    icon: Icons.cloud_off,
                    text: 'Не удалось загрузить каталог:\n${snap.error}',
                    onRetry: _reload,
                  );
                }
                final surveys = snap.data!;
                if (surveys.isEmpty) {
                  return _Message(icon: Icons.map_outlined, text: 'Съёмок пока нет', onRetry: _reload);
                }
                return RefreshIndicator(onRefresh: _reload, child: _buildList(surveys));
              },
            ),
    );
  }

  Widget _buildList(List<Survey> surveys) {
    final groups = <String, List<Survey>>{};
    for (final s in surveys) {
      groups.putIfAbsent('${s.farm} / ${s.field}', () => []).add(s);
    }
    final keys = groups.keys.toList()..sort();
    return ListView(
      children: [
        for (final key in keys) ...[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 4),
            child: Text(key, style: Theme.of(context).textTheme.titleSmall),
          ),
          for (final s in groups[key]!)
            ListTile(
              leading: Icon(s.isRaster ? Icons.satellite_alt : Icons.polyline),
              title: Text(_dateFmt.format(s.date)),
              subtitle: Text(s.title, maxLines: 1, overflow: TextOverflow.ellipsis),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => Navigator.push(
                context,
                MaterialPageRoute(builder: (_) => MapScreen(api: _api, survey: s)),
              ),
            ),
        ],
      ],
    );
  }
}

class _Message extends StatelessWidget {
  const _Message({required this.icon, required this.text, required this.onRetry});

  final IconData icon;
  final String text;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 48, color: Colors.grey),
            const SizedBox(height: 12),
            Text(text, textAlign: TextAlign.center),
            const SizedBox(height: 12),
            OutlinedButton(onPressed: onRetry, child: const Text('Повторить')),
          ],
        ),
      ),
    );
  }
}
