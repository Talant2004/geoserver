import 'package:shared_preferences/shared_preferences.dart';

class AppSettings {
  const AppSettings({
    required this.serverUrl,
    required this.username,
    required this.password,
    required this.organization,
  });

  /// Например: https://maps.example.com/geoserver
  final String serverUrl;
  final String username;
  final String password;
  final String organization;

  bool get isConfigured => serverUrl.isNotEmpty;

  static Future<AppSettings> load() async {
    final p = await SharedPreferences.getInstance();
    return AppSettings(
      serverUrl: p.getString('serverUrl') ?? '',
      username: p.getString('username') ?? 'povilika_app',
      password: p.getString('password') ?? '',
      organization: p.getString('organization') ?? '',
    );
  }

  Future<void> save() async {
    final p = await SharedPreferences.getInstance();
    await p.setString('serverUrl', serverUrl.trim().replaceAll(RegExp(r'/+$'), ''));
    await p.setString('username', username.trim());
    await p.setString('password', password);
    await p.setString('organization', organization.trim());
  }
}
