import 'package:flutter/material.dart';

import '../settings.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key, required this.settings});

  final AppSettings settings;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late final _url = TextEditingController(text: widget.settings.serverUrl);
  late final _user = TextEditingController(text: widget.settings.username);
  late final _password = TextEditingController(text: widget.settings.password);
  late final _org = TextEditingController(text: widget.settings.organization);
  final _form = GlobalKey<FormState>();

  @override
  void dispose() {
    for (final c in [_url, _user, _password, _org]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    if (!_form.currentState!.validate()) return;
    final s = AppSettings(
      serverUrl: _url.text.trim().replaceAll(RegExp(r'/+$'), ''),
      username: _user.text.trim(),
      password: _password.text,
      organization: _org.text.trim(),
    );
    await s.save();
    if (mounted) Navigator.pop(context, s);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Настройки')),
      body: Form(
        key: _form,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            TextFormField(
              controller: _url,
              keyboardType: TextInputType.url,
              decoration: const InputDecoration(
                labelText: 'Адрес GeoServer',
                hintText: 'https://maps.example.com/geoserver',
              ),
              validator: (v) {
                final uri = Uri.tryParse(v?.trim() ?? '');
                return uri != null && uri.hasScheme && uri.host.isNotEmpty
                    ? null
                    : 'Укажите полный адрес, начиная с https://';
              },
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _user,
              decoration: const InputDecoration(labelText: 'Пользователь'),
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _password,
              obscureText: true,
              decoration: const InputDecoration(labelText: 'Пароль'),
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _org,
              decoration: const InputDecoration(
                labelText: 'Организация (для шапки отчёта)',
              ),
            ),
            const SizedBox(height: 24),
            FilledButton(onPressed: _save, child: const Text('Сохранить')),
          ],
        ),
      ),
    );
  }
}
