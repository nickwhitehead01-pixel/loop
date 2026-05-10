import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/io.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../../../core/models/prompt_card.dart';
import '../../../core/models/transcript_chunk.dart';
import '../../../core/networking/hub_uri.dart';

/// Subscribes to `/session/ws/{id}/subscribe` and surfaces two typed streams:
/// - [transcriptChunks]: live teacher speech frames
/// - [promptCardUpdates]: context-aware pupil prompt card batches
///
/// The client is **idempotent and self-healing**:
/// - Repeated [connect] calls for the same session reuse the live channel
///   instead of stacking new WebSockets on top.
/// - Calling [connect] for a different session swaps the channel cleanly.
/// - If the channel errors or closes while still active, the client reconnects
///   on an exponential backoff (1s → 2s → 4s → 8s → 16s cap) until [close]
///   is called or a different session is requested.
class TranscriptSocketClient {
  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _wsSub;

  // Broadcast so the page can listen via `transcriptChunks`/`promptCardUpdates`
  // and so multiple page widgets sharing this client wouldn't conflict.
  final StreamController<TranscriptChunk> _transcriptController =
      StreamController<TranscriptChunk>.broadcast();
  final StreamController<List<PromptCard>> _promptCardController =
      StreamController<List<PromptCard>>.broadcast();

  Stream<TranscriptChunk> get transcriptChunks => _transcriptController.stream;
  Stream<List<PromptCard>> get promptCardUpdates => _promptCardController.stream;

  // Active target — used to know whether a repeat [connect] is redundant.
  Uri? _hubUri;
  int? _sessionId;

  // Reconnect state.
  Timer? _reconnectTimer;
  int _reconnectAttempt = 0;
  bool _closedByCaller = false;

  static const Duration _connectTimeout = Duration(seconds: 5);
  static const Duration _maxBackoff = Duration(seconds: 16);

  void connect({
    required Uri hubUri,
    required int sessionId,
  }) {
    // Same target, channel still alive → no-op. Stops the rebuild loop where
    // TranscriptPage calls connect on every navigation tick.
    final bool sameTarget = _hubUri == hubUri && _sessionId == sessionId;
    if (sameTarget && _channel != null) {
      debugPrint('[TranscriptSocketClient] connect noop (already on $sessionId)');
      return;
    }

    _closedByCaller = false;
    _hubUri = hubUri;
    _sessionId = sessionId;
    _reconnectAttempt = 0;
    _openChannel();
  }

  void _openChannel() {
    _disposeChannel();

    final Uri? hubUri = _hubUri;
    final int? sessionId = _sessionId;
    if (hubUri == null || sessionId == null) return;

    final Uri socketUri = wsUriForSessionTranscript(hubUri, sessionId);
    debugPrint('[TranscriptSocketClient] opening $socketUri (attempt ${_reconnectAttempt + 1})');

    late WebSocketChannel channel;
    try {
      channel = IOWebSocketChannel.connect(
        socketUri,
        connectTimeout: _connectTimeout,
      );
    } catch (e) {
      debugPrint('[TranscriptSocketClient] connect threw: $e');
      _scheduleReconnect();
      return;
    }
    _channel = channel;

    _wsSub = channel.stream.listen(
      (dynamic data) {
        // First successful frame proves the channel works — reset backoff.
        _reconnectAttempt = 0;
        _dispatch(data);
      },
      onError: (Object error) {
        debugPrint('[TranscriptSocketClient] stream error: $error');
        if (!_transcriptController.isClosed) {
          _transcriptController.addError(error);
        }
        _scheduleReconnect();
      },
      onDone: () {
        debugPrint('[TranscriptSocketClient] stream done');
        _scheduleReconnect();
      },
      cancelOnError: false,
    );
  }

  void _dispatch(dynamic data) {
    try {
      final dynamic payload = jsonDecode(data as String);
      if (payload is! Map<String, dynamic>) return;
      final String? type = payload['type'] as String?;
      if (type == 'transcript') {
        if (!_transcriptController.isClosed) {
          _transcriptController.add(TranscriptChunk.fromBroadcastJson(payload));
        }
      } else if (type == 'prompt_cards') {
        final dynamic raw = payload['cards'];
        if (raw is List) {
          final List<PromptCard> cards = raw
              .whereType<Map<String, dynamic>>()
              .map(PromptCard.fromJson)
              .toList();
          if (cards.isNotEmpty && !_promptCardController.isClosed) {
            _promptCardController.add(cards);
          }
        }
      }
    } catch (e) {
      debugPrint('[TranscriptSocketClient] dispatch error: $e');
    }
  }

  void _scheduleReconnect() {
    if (_closedByCaller) return;
    if (_hubUri == null || _sessionId == null) return;

    _reconnectTimer?.cancel();
    final int factor = 1 << _reconnectAttempt.clamp(0, 4); // 1,2,4,8,16
    final Duration delay = Duration(seconds: factor);
    final Duration effective = delay > _maxBackoff ? _maxBackoff : delay;
    _reconnectAttempt++;
    debugPrint('[TranscriptSocketClient] reconnect in ${effective.inSeconds}s');
    _reconnectTimer = Timer(effective, _openChannel);
  }

  void _disposeChannel() {
    _wsSub?.cancel();
    _wsSub = null;
    final WebSocketChannel? old = _channel;
    _channel = null;
    if (old != null) {
      // Sink close is async, fire-and-forget — we don't need to await before
      // opening the next channel.
      old.sink.close().catchError((_) {});
    }
  }

  Future<void> close() async {
    _closedByCaller = true;
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    _disposeChannel();
    _hubUri = null;
    _sessionId = null;
    if (!_transcriptController.isClosed) await _transcriptController.close();
    if (!_promptCardController.isClosed) await _promptCardController.close();
  }
}
