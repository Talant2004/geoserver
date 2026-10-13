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
  late bool _autoUrl = widget.settings.autoUrl;
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
      autoUrl: _autoUrl,
    );
    await s.save();
    if (mounted) Navigator.pop(context, s);
  }

  void _resetLogin() {
    setState(() {
      _user.text = kDefaultUser;
      _password.text = kDefaultPassword;
    });
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
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Определять адрес сервера автоматически'),
              subtitle: const Text('Рекомендуется. Выключайте, только если адрес дал администратор.'),
              value: _autoUrl,
              onChanged: (v) => setState(() => _autoUrl = v),
            ),
            const SizedBox(height: 8),
            TextFormField(
              controller: _url,
              enabled: !_autoUrl,
              keyboardType: TextInputType.url,
              decoration: InputDecoration(
                labelText: _autoUrl ? 'Текущий адрес сервера' : 'Адрес GeoServer',
                hintText: 'https://maps.example.com/geoserver',
              ),
              validator: (v) {
                if (_autoUrl) return null;
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
            if (kDefaultPassword.isNotEmpty)
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton(
                  onPressed: _resetLogin,
                  child: const Text('Вернуть встроенные логин и пароль'),
                ),
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
