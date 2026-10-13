import 'package:shared_preferences/shared_preferences.dart';

/// Подставляются при сборке: --dart-define=POVILIKA_PASSWORD=... (см. build_apk.sh).
const kDefaultUser = String.fromEnvironment('POVILIKA_USER', defaultValue: 'povilika_app');
const kDefaultPassword = String.fromEnvironment('POVILIKA_PASSWORD');

class AppSettings {
  const AppSettings({
    required this.serverUrl,
    required this.username,
    required this.password,
    required this.organization,
    this.autoUrl = true,
  });

  /// Например: https://maps.example.com/geoserver.
  /// При [autoUrl] — последний адрес, полученный с GitHub.
  final String serverUrl;
  final String username;
  final String password;
  final String organization;

  /// Брать адрес сервера из файла, который сервер публикует на GitHub.
  final bool autoUrl;

  bool get isConfigured => autoUrl || serverUrl.isNotEmpty;

  AppSettings copyWith({String? serverUrl}) => AppSettings(
        serverUrl: serverUrl ?? this.serverUrl,
        username: username,
        password: password,
        organization: organization,
        autoUrl: autoUrl,
      );

  static Future<AppSettings> load() async {
    final p = await SharedPreferences.getInstance();
    String orDefault(String key, String def) {
      final v = p.getString(key) ?? '';
      return v.isEmpty ? def : v;
    }

    return AppSettings(
      serverUrl: p.getString('serverUrl') ?? '',
      username: orDefault('username', kDefaultUser),
      password: orDefault('password', kDefaultPassword),
      organization: p.getString('organization') ?? '',
      autoUrl: p.getBool('autoUrl') ?? true,
    );
  }

  Future<void> save() async {
    final p = await SharedPreferences.getInstance();
    await p.setString('serverUrl', serverUrl.trim().replaceAll(RegExp(r'/+$'), ''));
    await p.setString('username', username.trim());
    await p.setString('password', password);
    await p.setString('organization', organization.trim());
    await p.setBool('autoUrl', autoUrl);
  }
}
