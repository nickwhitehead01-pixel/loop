import 'package:shared_preferences/shared_preferences.dart';

import '../../../core/models/hub_settings.dart';

class HubSettingsStore {
  static const String _hubUrlKey = 'hub_url';
  static const String _pupilIdKey = 'pupil_id';

  Future<HubSettings?> load() async {
    final SharedPreferences prefs = await SharedPreferences.getInstance();
    final String? hubUrl = prefs.getString(_hubUrlKey);
    final int? pupilId = prefs.getInt(_pupilIdKey);

    if (hubUrl == null || pupilId == null) {
      return null;
    }

    final Uri? parsed = Uri.tryParse(hubUrl);
    if (parsed == null || !(parsed.scheme == 'http' || parsed.scheme == 'https')) {
      return null;
    }

    return HubSettings(hubUri: parsed, pupilId: pupilId);
  }

  Future<void> save(HubSettings settings) async {
    final SharedPreferences prefs = await SharedPreferences.getInstance();
    await prefs.setString(_hubUrlKey, settings.hubUri.toString());
    await prefs.setInt(_pupilIdKey, settings.pupilId);
  }

  Future<void> clear() async {
    final SharedPreferences prefs = await SharedPreferences.getInstance();
    await prefs.remove(_hubUrlKey);
    await prefs.remove(_pupilIdKey);
  }
}
