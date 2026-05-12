/// A live quiz question pushed to a pupil over the session WebSocket.
///
/// `correctAnswer` is intentionally absent — the backend strips it from the
/// pupil-facing payload so a curious pupil can't read the answer off the wire
/// before submitting.
class QuizQuestion {
  const QuizQuestion({
    required this.id,
    required this.sessionId,
    required this.questionText,
    required this.timeLimitSeconds,
    required this.deadlineMs,
    this.topicTag,
  });

  final int id;
  final int sessionId;
  final String questionText;
  final int timeLimitSeconds;

  /// Server-clock unix-millis after which the client should refuse input.
  /// Drives the countdown directly so all pupils agree on the deadline even
  /// if their device clocks drift.
  final int deadlineMs;
  final String? topicTag;

  factory QuizQuestion.fromBroadcastJson(Map<String, dynamic> payload) {
    // The backend sends {"type":"quiz_question_opened","question":{...}}.
    final Map<String, dynamic> q =
        (payload['question'] as Map).cast<String, dynamic>();
    return QuizQuestion(
      id: q['id'] as int,
      sessionId: q['session_id'] as int,
      questionText: q['question_text'] as String,
      timeLimitSeconds: (q['time_limit_seconds'] as num).toInt(),
      deadlineMs: (q['deadline_ms'] as num).toInt(),
      topicTag: q['topic_tag'] as String?,
    );
  }
}
