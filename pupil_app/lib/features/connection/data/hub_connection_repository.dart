import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../../core/networking/hub_uri.dart';

class HubConnectionRepository {
  const HubConnectionRepository();

  Future<bool> testHealth(Uri hubUri) async {
    final Uri uri = healthUri(hubUri);
    final http.Response response = await http.get(uri).timeout(const Duration(seconds: 4));

    if (response.statusCode != 200) {
      return false;
    }

    if (response.body.isEmpty) {
      return true;
    }

    try {
      final dynamic parsed = jsonDecode(response.body);
      return parsed is Map<String, dynamic>;
    } catch (_) {
      return true;
    }
  }
}
