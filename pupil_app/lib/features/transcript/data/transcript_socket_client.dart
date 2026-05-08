import 'dart:convert';

import 'package:web_socket_channel/io.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../../../core/models/transcript_chunk.dart';
import '../../../core/networking/hub_uri.dart';

/// Subscribes to `/session/ws/{id}/subscribe` and surfaces typed transcript
/// frames. The hub only sends `{type:"transcript",...}` payloads on this
/// channel, but we ignore unrecognised frames defensively.
class TranscriptSocketClient {
  WebSocketChannel? _channel;

  Stream<TranscriptChunk> connect({
    required Uri hubUri,
    required int sessionId,
  }) {
    final Uri socketUri = wsUriForSessionTranscript(hubUri, sessionId);
    _channel = IOWebSocketChannel.connect(
      socketUri,
      connectTimeout: const Duration(seconds: 3),
    );
    return _channel!.stream
        .map<TranscriptChunk?>((dynamic data) {
          final dynamic payload = jsonDecode(data as String);
          if (payload is! Map<String, dynamic>) return null;
          if (payload['type'] != 'transcript') return null;
          return TranscriptChunk.fromBroadcastJson(payload);
        })
        .where((TranscriptChunk? c) => c != null)
        .cast<TranscriptChunk>();
  }

  Future<void> close() async {
    await _channel?.sink.close();
    _channel = null;
  }
}
