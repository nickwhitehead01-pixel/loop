import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';

import '../../../core/models/hub_settings.dart';
import '../../chat/presentation/chat_page.dart';
import '../data/hub_connection_repository.dart';
import '../data/hub_settings_store.dart';

class ConnectPage extends StatefulWidget {
  const ConnectPage({super.key});

  @override
  State<ConnectPage> createState() => _ConnectPageState();
}

class _ConnectPageState extends State<ConnectPage> {
  final GlobalKey<FormState> _formKey = GlobalKey<FormState>();
  final TextEditingController _hubUrlController = TextEditingController(text: _defaultHubUrl());
  final TextEditingController _pupilIdController = TextEditingController(text: '1');
  final HubConnectionRepository _connectionRepo = const HubConnectionRepository();
  final HubSettingsStore _store = HubSettingsStore();

  bool _testing = false;
  bool _saving = false;

  static String _defaultHubUrl() {
    if (!kIsWeb && defaultTargetPlatform == TargetPlatform.android) {
      // Android emulators use 10.0.2.2 to reach services running on the host machine.
      return 'http://10.0.2.2:8000';
    }
    return 'http://192.168.50.60:8000';
  }

  @override
  void dispose() {
    _hubUrlController.dispose();
    _pupilIdController.dispose();
    super.dispose();
  }

  Future<void> _testConnection() async {
    if (!_formKey.currentState!.validate()) {
      return;
    }

    setState(() {
      _testing = true;
    });

    final Uri hubUri = Uri.parse(_hubUrlController.text.trim());
    final HubHealthCheckResult result = await _connectionRepo.testHealth(hubUri);

    if (!mounted) {
      return;
    }

    setState(() {
      _testing = false;
    });

    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(result.userMessage)),
    );
  }

  Future<void> _saveAndContinue() async {
    if (!_formKey.currentState!.validate()) {
      return;
    }

    setState(() {
      _saving = true;
    });

    final HubSettings settings = HubSettings(
      hubUri: Uri.parse(_hubUrlController.text.trim()),
      pupilId: int.parse(_pupilIdController.text.trim()),
    );

    final HubHealthCheckResult result = await _connectionRepo.testHealth(settings.hubUri);

    if (!mounted) {
      return;
    }

    if (!result.isReachable) {
      setState(() {
        _saving = false;
      });
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(result.userMessage)),
      );
      return;
    }

    await _store.save(settings);

    if (!mounted) {
      return;
    }

    setState(() {
      _saving = false;
    });

    Navigator.of(context).pushReplacement(
      MaterialPageRoute<ChatPage>(builder: (_) => ChatPage(initialSettings: settings)),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Connect to Loop Hub')),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Form(
            key: _formKey,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                const Text(
                  'Enter the Hub URL and pupil ID to start chatting on the local school network.',
                ),
                const SizedBox(height: 16),
                TextFormField(
                  controller: _hubUrlController,
                  keyboardType: TextInputType.url,
                  decoration: const InputDecoration(labelText: 'Hub URL'),
                  validator: (String? value) {
                    if (value == null || value.trim().isEmpty) {
                      return 'Hub URL is required';
                    }
                    final Uri? uri = Uri.tryParse(value.trim());
                    if (uri == null || !(uri.scheme == 'http' || uri.scheme == 'https')) {
                      return 'Use a valid http or https URL';
                    }
                    if (uri.host.isEmpty) {
                      return 'URL must include a host';
                    }
                    return null;
                  },
                ),
                const SizedBox(height: 12),
                TextFormField(
                  controller: _pupilIdController,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(labelText: 'Pupil ID'),
                  validator: (String? value) {
                    if (value == null || value.trim().isEmpty) {
                      return 'Pupil ID is required';
                    }
                    final int? parsed = int.tryParse(value.trim());
                    if (parsed == null || parsed <= 0) {
                      return 'Pupil ID must be a positive number';
                    }
                    return null;
                  },
                ),
                const SizedBox(height: 16),
                ElevatedButton(
                  onPressed: _testing ? null : _testConnection,
                  child: Text(_testing ? 'Testing...' : 'Test Hub Connection'),
                ),
                const SizedBox(height: 8),
                FilledButton(
                  onPressed: _saving ? null : _saveAndContinue,
                  child: Text(_saving ? 'Connecting...' : 'Continue to Chat'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
