import 'dart:async';
import 'dart:convert';

import 'package:web_socket_channel/io.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../../../core/models/prompt_card.dart';
import '../../../core/models/transcript_chunk.dart';
import '../../../core/networking/hub_uri.dart';

/// Subscribes to `/session/ws/{id}/subscribe` and surfaces two typed streams:
/// - [transcriptChunks]: live teacher speech frames
/// - [promptCardUpdates]: context-aware pupil prompt card batches
class TranscriptSocketClient {
  WebSocketChannel? _channel;

  final StreamController<TranscriptChunk> _transcriptController =
      StreamController<TranscriptChunk>.broadcast();
  final StreamController<List<PromptCard>> _promptCardController =
      StreamController<List<PromptCard>>.broadcast();

  Stream<TranscriptChunk> get transcriptChunks => _transcriptController.stream;
  Stream<List<PromptCard>> get promptCardUpdates => _promptCardController.stream;

  void connect({
    required Uri hubUri,
    required int sessionId,
  }) {
    final Uri socketUri = wsUriForSessionTranscript(hubUri, sessionId);
    _channel = IOWebSocketChannel.connect(
      socketUri,
      connectTimeout: const Duration(seconds: 3),
    );
    _channel!.stream.listen(
      (dynamic data) {
        final dynamic payload = jsonDecode(data as String);
        if (payload is! Map<String, dynamic>) return;
        final String? type = payload['type'] as String?;
        if (type == 'transcript') {
          _transcriptController.add(TranscriptChunk.fromBroadcastJson(payload));
        } else if (type == 'prompt_cards') {
          final dynamic raw = payload['cards'];
          if (raw is List) {
            final List<PromptCard> cards = raw
                .whereType<Map<String, dynamic>>()
                .map(PromptCard.fromJson)
                .toList();
            if (cards.isNotEmpty) _promptCardController.add(cards);
          }
        }
      },
      onError: (Object error) {
        _transcriptController.addError(error);
      },
      onDone: () {
        if (!_transcriptController.isClosed) _transcriptController.close();
        if (!_promptCardController.isClosed) _promptCardController.close();
      },
      cancelOnError: false,
    );
  }

  Future<void> close() async {
    await _channel?.sink.close();
    _channel = null;
    if (!_transcriptController.isClosed) await _transcriptController.close();
    if (!_promptCardController.isClosed) await _promptCardController.close();
  }
}
