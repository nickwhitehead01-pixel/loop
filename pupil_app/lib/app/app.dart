import 'package:flutter/material.dart';

import '../core/models/hub_settings.dart';
import '../features/chat/presentation/chat_page.dart';
import '../features/connection/data/hub_settings_store.dart';
import '../features/connection/presentation/connect_page.dart';
import 'theme.dart';

class PupilApp extends StatelessWidget {
  const PupilApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Loop Pupil',
      theme: buildAppTheme(),
      home: const _AppGate(),
    );
  }
}

class _AppGate extends StatefulWidget {
  const _AppGate();

  @override
  State<_AppGate> createState() => _AppGateState();
}

class _AppGateState extends State<_AppGate> {
  final HubSettingsStore _store = HubSettingsStore();
  late final Future<HubSettings?> _settingsFuture = _store.load();

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<HubSettings?>(
      future: _settingsFuture,
      builder: (context, snapshot) {
        if (!snapshot.hasData) {
          return const ConnectPage();
        }

        final HubSettings settings = snapshot.data!;
        return ChatPage(initialSettings: settings);
      },
    );
  }
}
