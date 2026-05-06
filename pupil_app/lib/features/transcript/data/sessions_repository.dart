import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../../core/models/lesson_session.dart';
import '../../../core/models/transcript_chunk.dart';
import '../../../core/networking/hub_uri.dart';

class SessionsRepository {
  SessionsRepository({http.Client? client}) : _client = client ?? http.Client();

  final http.Client _client;

  Future<List<LessonSession>> listForPupil({
    required Uri hubUri,
    required int pupilId,
  }) async {
    final http.Response res = await _client
        .get(pupilSessionsUri(hubUri, pupilId))
        .timeout(const Duration(seconds: 5));
    if (res.statusCode != 200) {
      throw HubRequestException(
        'Could not load sessions (HTTP ${res.statusCode}).',
      );
    }
    final List<dynamic> raw = jsonDecode(res.body) as List<dynamic>;
    return raw
        .cast<Map<String, dynamic>>()
        .map(LessonSession.fromJson)
        .toList(growable: false);
  }

  Future<List<TranscriptChunk>> historyForSession({
    required Uri hubUri,
    required int sessionId,
  }) async {
    final http.Response res = await _client
        .get(sessionTranscriptUri(hubUri, sessionId))
        .timeout(const Duration(seconds: 5));
    if (res.statusCode != 200) {
      throw HubRequestException(
        'Could not load transcript (HTTP ${res.statusCode}).',
      );
    }
    final List<dynamic> raw = jsonDecode(res.body) as List<dynamic>;
    return raw
        .cast<Map<String, dynamic>>()
        .map(TranscriptChunk.fromHistoryJson)
        .toList(growable: false);
  }

  void dispose() {
    _client.close();
  }
}

class HubRequestException implements Exception {
  HubRequestException(this.message);
  final String message;

  @override
  String toString() => message;
}
