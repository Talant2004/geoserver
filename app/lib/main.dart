import 'package:flutter/material.dart';

import 'screens/surveys_screen.dart';
import 'settings.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final settings = await AppSettings.load();
  runApp(PovilikaApp(settings: settings));
}

class PovilikaApp extends StatelessWidget {
  const PovilikaApp({super.key, required this.settings});

  final AppSettings settings;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Повилика — Карты',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorSchemeSeed: const Color(0xFF2E7D32),
        useMaterial3: true,
      ),
      home: SurveysScreen(initialSettings: settings),
    );
  }
}
