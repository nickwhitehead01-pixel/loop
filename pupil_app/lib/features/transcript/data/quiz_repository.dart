import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../../core/networking/hub_uri.dart';

/// Thin wrapper around POST /pupil/{pupilId}/quiz/{questionId}/answer.
///
/// The backend rejects (409) when the question is no longer in 'sent' status,
/// which is the only failure mode the pupil UI cares about — it means the
/// teacher closed it or the timer expired between render and submit. The
/// caller surfaces that as "too slow!" rather than a generic error.
class QuizRepository {
  QuizRepository({http.Client? client}) : _client = client ?? http.Client();

  final http.Client _client;

  Future<QuizSubmitResult> submitAnswer({
    required Uri hubUri,
    required int pupilId,
    required int questionId,
    required String answer,
  }) async {
    final Uri uri = pupilQuizAnswerUri(hubUri, pupilId, questionId);
    final http.Response res = await _client.post(
      uri,
      headers: <String, String>{'Content-Type': 'application/json'},
      body: jsonEncode(<String, dynamic>{'pupil_answer': answer}),
    );
    if (res.statusCode == 409) {
      return QuizSubmitResult.tooLate;
    }
    if (res.statusCode >= 200 && res.statusCode < 300) {
      return QuizSubmitResult.ok;
    }
    return QuizSubmitResult.error;
  }

  void dispose() => _client.close();
}

enum QuizSubmitResult { ok, tooLate, error }
