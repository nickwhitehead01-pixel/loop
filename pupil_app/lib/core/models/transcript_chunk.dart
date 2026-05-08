class TranscriptChunk {
  const TranscriptChunk({
    required this.content,
    required this.timestampMs,
  });

  final String content;
  final int timestampMs;

  /// REST history rows include `id`/`session_id`/`created_at`; we only need
  /// the body and the timestamp to render in the live feed.
  factory TranscriptChunk.fromHistoryJson(Map<String, dynamic> json) {
    return TranscriptChunk(
      content: (json['content'] as String?) ?? '',
      timestampMs: (json['timestamp_ms'] as num?)?.toInt() ?? 0,
    );
  }

  /// WebSocket broadcasts arrive as `{type:"transcript", content, timestamp_ms}`.
  factory TranscriptChunk.fromBroadcastJson(Map<String, dynamic> json) {
    return TranscriptChunk(
      content: (json['content'] as String?) ?? '',
      timestampMs: (json['timestamp_ms'] as num?)?.toInt() ?? 0,
    );
  }
}
