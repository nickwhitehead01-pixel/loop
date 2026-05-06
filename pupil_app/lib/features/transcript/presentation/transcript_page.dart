import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';

import '../../../app/theme.dart';
import '../../../core/models/hub_settings.dart';
import '../../../core/models/lesson_session.dart';
import '../../../core/models/transcript_chunk.dart';
import '../../../core/widgets/composer.dart';
import '../../../core/widgets/notebook_gutter.dart';
import '../../../core/widgets/turn.dart' as ui;
import '../../../core/widgets/waveform.dart';
import '../../chat/data/chat_socket_client.dart';
import '../data/sessions_repository.dart';
import '../data/transcript_socket_client.dart';

/// Live-lesson surface: header waveform + LIVE pill, transcript Turn feed,
/// 153px ruled scroll gutter on the right, composer pinned at the bottom for
/// pupil questions answered by the Class Helper agent.
class TranscriptPage extends StatefulWidget {
  const TranscriptPage({
    super.key,
    required this.settings,
    required this.session,
    this.repository,
    this.transcriptClient,
    this.chatClient,
  });

  final HubSettings settings;
  final LessonSession session;
  final SessionsRepository? repository;
  final TranscriptSocketClient? transcriptClient;
  final ChatSocketClient? chatClient;

  @override
  State<TranscriptPage> createState() => _TranscriptPageState();
}

enum _Speaker { teacher, pupil, helper }

class _Entry {
  _Entry({required this.speaker, required this.text, this.isStreaming = false});
  final _Speaker speaker;
  String text;
  bool isStreaming;
}

class _TranscriptPageState extends State<TranscriptPage> {
  late final SessionsRepository _repo =
      widget.repository ?? SessionsRepository();
  late final TranscriptSocketClient _transcriptClient =
      widget.transcriptClient ?? TranscriptSocketClient();
  late final ChatSocketClient _chatClient = widget.chatClient ?? ChatSocketClient();

  final ScrollController _scroll = ScrollController();
  final List<_Entry> _entries = <_Entry>[];

  StreamSubscription<TranscriptChunk>? _transcriptSub;
  StreamSubscription<ChatStreamFrame>? _chatSub;

  String? _error;
  bool _historyLoaded = false;
  bool _chatConnected = false;
  bool _awaitingChatReply = false;
  DateTime? _lastChunkAt;

  Timer? _waveTimer;
  WaveformState _waveState = WaveformState.waiting;

  @override
  void initState() {
    super.initState();
    _bootstrap();
    _waveTimer = Timer.periodic(const Duration(milliseconds: 500), (_) => _recomputeWaveState());
  }

  Future<void> _bootstrap() async {
    await _loadHistory();
    if (!mounted) return;
    _connectChat();
    if (widget.session.isLive) {
      _connectTranscript();
    } else {
      setState(() => _waveState = WaveformState.paused);
    }
  }

  Future<void> _loadHistory() async {
    try {
      final List<TranscriptChunk> history = await _repo.historyForSession(
        hubUri: widget.settings.hubUri,
        sessionId: widget.session.id,
      );
      if (!mounted) return;
      setState(() {
        for (final TranscriptChunk c in history) {
          _entries.add(_Entry(speaker: _Speaker.teacher, text: c.content));
        }
        _historyLoaded = true;
      });
      _scrollToBottomSoon();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = _friendlyError(e);
        _historyLoaded = true;
      });
    }
  }

  void _connectTranscript() {
    _transcriptSub?.cancel();
    final Stream<TranscriptChunk> stream = _transcriptClient.connect(
      hubUri: widget.settings.hubUri,
      sessionId: widget.session.id,
    );
    _transcriptSub = stream.listen(
      (TranscriptChunk chunk) {
        if (!mounted) return;
        setState(() {
          _entries.add(_Entry(speaker: _Speaker.teacher, text: chunk.content));
          _lastChunkAt = DateTime.now();
        });
        _scrollToBottomSoon();
      },
      onError: (Object error) {
        if (!mounted) return;
        setState(() => _error = _friendlyError(error));
      },
      onDone: () {
        // Transcript stream closed; the waveform will fall back to "waiting"
        // through the periodic recompute, so no explicit state to flip here.
      },
      cancelOnError: false,
    );
  }

  void _connectChat() {
    _chatSub?.cancel();
    final Stream<ChatStreamFrame> stream = _chatClient.connect(
      widget.settings.hubUri,
      widget.settings.pupilId,
    );
    _chatSub = stream.listen(
      (ChatStreamFrame frame) {
        if (!mounted) return;
        setState(() {
          _chatConnected = true;
          final _Entry? streaming = _streamingHelperEntry();
          if (frame.token.isNotEmpty && streaming != null) {
            streaming.text += frame.token;
          }
          if (frame.done && streaming != null) {
            streaming.isStreaming = false;
            _awaitingChatReply = false;
          }
        });
        _scrollToBottomSoon();
      },
      onError: (Object error) {
        if (!mounted) return;
        setState(() {
          _chatConnected = false;
          _awaitingChatReply = false;
          final _Entry? streaming = _streamingHelperEntry();
          if (streaming != null) {
            if (streaming.text.isEmpty) {
              streaming.text = _friendlyError(error);
            }
            streaming.isStreaming = false;
          }
        });
      },
      onDone: () {
        if (!mounted) return;
        setState(() => _chatConnected = false);
      },
      cancelOnError: false,
    );
  }

  void _recomputeWaveState() {
    if (!mounted) return;
    final WaveformState next;
    if (!widget.session.isLive) {
      next = WaveformState.paused;
    } else if (_lastChunkAt != null &&
        DateTime.now().difference(_lastChunkAt!) < const Duration(seconds: 4)) {
      next = WaveformState.listening;
    } else {
      next = WaveformState.waiting;
    }
    if (next != _waveState) {
      setState(() => _waveState = next);
    }
  }

  void _scrollToBottomSoon() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scroll.hasClients) return;
      _scroll.animateTo(
        _scroll.position.maxScrollExtent,
        duration: const Duration(milliseconds: 220),
        curve: Curves.easeOut,
      );
    });
  }

  _Entry? _streamingHelperEntry() {
    for (int i = _entries.length - 1; i >= 0; i--) {
      final _Entry e = _entries[i];
      if (e.speaker == _Speaker.helper && e.isStreaming) return e;
    }
    return null;
  }

  void _onSend(String text) {
    if (!_chatConnected) {
      _connectChat();
    }
    setState(() {
      _entries.add(_Entry(speaker: _Speaker.pupil, text: text));
      _entries.add(_Entry(speaker: _Speaker.helper, text: '', isStreaming: true));
      _awaitingChatReply = true;
    });
    _chatClient.sendMessage(
      message: text,
      sessionId: widget.session.id,
    );
    _scrollToBottomSoon();
  }

  String _friendlyError(Object error) {
    if (error is TimeoutException) {
      return 'Connection timed out. Check that the Hub is reachable.';
    }
    if (error is SocketException) {
      return 'Could not reach the Hub. Check the Wi-Fi.';
    }
    if (error is HubRequestException) {
      return error.message;
    }
    return error.toString();
  }

  @override
  void dispose() {
    _waveTimer?.cancel();
    _transcriptSub?.cancel();
    _chatSub?.cancel();
    _transcriptClient.close();
    _chatClient.close();
    if (widget.repository == null) {
      _repo.dispose();
    }
    _scroll.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: LoopColors.paper,
      appBar: AppBar(
        backgroundColor: LoopColors.paper,
        surfaceTintColor: LoopColors.paper,
        elevation: 0,
        title: Text(widget.session.title, style: LoopType.speaker),
        leading: IconButton(
          tooltip: 'Back to lessons',
          icon: const Icon(Icons.arrow_back, color: LoopColors.ink),
          onPressed: () => Navigator.of(context).pop(),
        ),
      ),
      body: SafeArea(
        bottom: false,
        child: Column(
          children: <Widget>[
            Padding(
              padding: const EdgeInsets.fromLTRB(36, 12, 36, 16),
              child: Waveform(state: _waveState),
            ),
            if (_error != null) _ErrorBanner(message: _error!),
            Expanded(child: _buildBody()),
            Composer(
              enabled: !_awaitingChatReply,
              onSend: _onSend,
              placeholder: _awaitingChatReply
                  ? 'Class Helper is answering…'
                  : 'Ask the Class Helper…',
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildBody() {
    if (!_historyLoaded) {
      return const Center(child: CircularProgressIndicator());
    }
    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Expanded(
          child: ScrollConfiguration(
            // Main column is tap-only — pupils scroll via the right gutter.
            behavior: const _NoScrollBehaviour(),
            child: ListView.builder(
              controller: _scroll,
              padding: const EdgeInsets.fromLTRB(36, 8, 12, 24),
              itemCount: _entries.isEmpty ? 1 : _entries.length,
              itemBuilder: (BuildContext context, int i) {
                if (_entries.isEmpty) {
                  return Padding(
                    padding: const EdgeInsets.only(top: 24),
                    child: Text(
                      widget.session.isLive
                          ? 'Listening for your teacher…'
                          : 'No transcript was recorded for this lesson.',
                      style: LoopType.ui.copyWith(color: LoopColors.inkMuted),
                    ),
                  );
                }
                final _Entry e = _entries[i];
                return ui.Turn(
                  speaker: _label(e.speaker),
                  text: e.text,
                  isStreaming: e.isStreaming,
                );
              },
            ),
          ),
        ),
        NotebookGutter(controller: _scroll),
      ],
    );
  }

  String _label(_Speaker s) {
    switch (s) {
      case _Speaker.teacher:
        return 'TEACHER';
      case _Speaker.pupil:
        return 'YOU';
      case _Speaker.helper:
        return 'CLASS HELPER';
    }
  }
}

class _NoScrollBehaviour extends ScrollBehavior {
  const _NoScrollBehaviour();

  @override
  Widget buildScrollbar(BuildContext context, Widget child, ScrollableDetails details) => child;
}

class _ErrorBanner extends StatelessWidget {
  const _ErrorBanner({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      color: LoopColors.errorSoft,
      padding: const EdgeInsets.symmetric(horizontal: 36, vertical: 10),
      child: Text(
        message,
        style: LoopType.ui.copyWith(color: LoopColors.error),
      ),
    );
  }
}
