enum SessionStatus { live, ended, unknown }

SessionStatus _parseStatus(String? raw) {
  switch (raw) {
    case 'live':
      return SessionStatus.live;
    case 'ended':
      return SessionStatus.ended;
    default:
      return SessionStatus.unknown;
  }
}

class LessonSession {
  const LessonSession({
    required this.id,
    required this.title,
    required this.status,
    required this.startedAt,
    this.endedAt,
  });

  final int id;
  final String title;
  final SessionStatus status;
  final DateTime startedAt;
  final DateTime? endedAt;

  bool get isLive => status == SessionStatus.live;

  factory LessonSession.fromJson(Map<String, dynamic> json) {
    return LessonSession(
      id: json['id'] as int,
      title: (json['title'] as String?) ?? 'Untitled session',
      status: _parseStatus(json['status'] as String?),
      startedAt: DateTime.parse(json['started_at'] as String),
      endedAt: json['ended_at'] == null
          ? null
          : DateTime.parse(json['ended_at'] as String),
    );
  }
}
