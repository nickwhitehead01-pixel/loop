import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';

import '../../../app/theme.dart';
import '../../../core/models/hub_settings.dart';
import '../../../core/models/lesson_session.dart';
import '../../../core/models/transcript_chunk.dart';
import '../../../core/models/prompt_card.dart';
import '../../../core/models/quiz_question.dart';
import '../../../core/models/tappable_term.dart';
import '../../../core/widgets/composer.dart';
import '../../../core/widgets/notebook_gutter.dart';
import '../../../core/widgets/prompt_card_row.dart';
import '../../../core/widgets/turn.dart' as ui;
import '../../../core/widgets/waveform.dart';
import '../../chat/data/chat_socket_client.dart';
import '../data/quiz_repository.dart';
import '../data/sessions_repository.dart';
import '../data/transcript_socket_client.dart';
import 'quiz_modal.dart';

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
  final QuizRepository _quizRepository = QuizRepository();

  // Two independent scroll positions: the left column tracks the teacher's
  // live transcript (driven by the existing NotebookGutter), the right
  // column tracks the chat with the Class Helper. Keeping them separate
  // means a teacher chunk arriving mid-conversation doesn't kick the chat
  // back to the bottom, and vice versa.
  final ScrollController _scroll = ScrollController();
  final ScrollController _chatScroll = ScrollController();
  final List<_Entry> _entries = <_Entry>[];

  StreamSubscription<TranscriptChunk>? _transcriptSub;
  StreamSubscription<List<PromptCard>>? _promptCardSub;
  StreamSubscription<List<TappableTerm>>? _tappableTermSub;
  StreamSubscription<ChatStreamFrame>? _chatSub;
  StreamSubscription<QuizQuestion>? _quizOpenSub;
  StreamSubscription<int>? _quizCloseSub;

  /// The currently-open quiz question, if any. `null` whenever no modal
  /// should be shown — set by the WS opened event and cleared by either the
  /// closed event, the in-modal "Sent!" delay, or the timer expiring.
  QuizQuestion? _activeQuizQuestion;

  List<PromptCard> _promptCards = const <PromptCard>[];

  /// Cumulative tappable-term lookup, keyed by lowercased term so re-broadcasts
  /// can revise an existing explanation. Passed to every `Turn` so that a term
  /// flagged in batch N also retroactively underlines its earlier occurrences.
  final Map<String, TappableTerm> _tappableTerms = <String, TappableTerm>{};

  String? _error;
  bool _historyLoaded = false;
  bool _chatConnected = false;
  bool _awaitingChatReply = false;
  DateTime? _lastChunkAt;

  Timer? _waveTimer;
  Timer? _replyTimeoutTimer;
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
    // Only establish the chat connection if the user hasn't already triggered
    // one by sending a message while history was loading. If _chatSub is
    // already set, _connectChat() would cancel that live subscription and the
    // in-flight response tokens would be silently dropped.
    if (_chatSub == null) _connectChat();
    debugPrint('[TranscriptPage] session ${widget.session.id} isLive=${widget.session.isLive}');
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
    _promptCardSub?.cancel();
    _tappableTermSub?.cancel();

    debugPrint('[TranscriptPage] Connecting transcript WS for session ${widget.session.id}');
    _transcriptClient.connect(
      hubUri: widget.settings.hubUri,
      sessionId: widget.session.id,
    );

    _transcriptSub = _transcriptClient.transcriptChunks.listen(
      (TranscriptChunk chunk) {
        debugPrint('[TranscriptPage] Received transcript chunk: ${chunk.content.length} chars');
        if (!mounted) return;
        setState(() {
          _entries.add(_Entry(speaker: _Speaker.teacher, text: chunk.content));
          _lastChunkAt = DateTime.now();
        });
        _scrollToBottomSoon();
      },
      onError: (Object error) {
        debugPrint('[TranscriptPage] Transcript WS error: $error');
        if (!mounted) return;
        setState(() => _error = _friendlyError(error));
      },
      onDone: () {
        // Transcript stream closed; the waveform will fall back to "waiting"
        // through the periodic recompute, so no explicit state to flip here.
      },
      cancelOnError: false,
    );

    _promptCardSub = _transcriptClient.promptCardUpdates.listen(
      (List<PromptCard> cards) {
        debugPrint('[TranscriptPage] Received ${cards.length} prompt cards');
        if (!mounted) return;
        setState(() => _promptCards = cards);
      },
      cancelOnError: false,
    );

    // Quiz events ride the same /subscribe channel as transcript chunks —
    // the backend's broadcast_to_pupils helper uses the same subscriber set.
    _quizOpenSub = _transcriptClient.quizQuestionOpened.listen(
      (QuizQuestion q) {
        debugPrint('[TranscriptPage] quiz_question_opened id=${q.id}');
        if (!mounted) return;
        setState(() => _activeQuizQuestion = q);
      },
      cancelOnError: false,
    );
    _quizCloseSub = _transcriptClient.quizQuestionClosed.listen(
      (int closedId) {
        debugPrint('[TranscriptPage] quiz_question_closed id=$closedId');
        if (!mounted) return;
        // Only dismiss if it's the same question we're showing — guards
        // against a stray late event from a previous question id.
        if (_activeQuizQuestion?.id == closedId) {
          setState(() => _activeQuizQuestion = null);
        }
      },
      cancelOnError: false,
    );

    _tappableTermSub = _transcriptClient.tappableTermUpdates.listen(
      (List<TappableTerm> terms) {
        debugPrint('[TranscriptPage] Received ${terms.length} tappable terms');
        if (!mounted) return;
        setState(() {
          // Merge; last-write-wins so re-broadcasts can revise an explanation.
          for (final TappableTerm t in terms) {
            _tappableTerms[t.term.toLowerCase()] = t;
          }
        });
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
            _replyTimeoutTimer?.cancel();
          }
        });
        // Helper tokens land in the right column, so scroll that one only.
        _scrollToBottomSoon(_chatScroll);
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

  void _scrollToBottomSoon([ScrollController? controller]) {
    final ScrollController target = controller ?? _scroll;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!target.hasClients) return;
      target.animateTo(
        target.position.maxScrollExtent,
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
    // The pupil's question + the pending helper turn both land in the right
    // column, so scroll that one.
    _scrollToBottomSoon(_chatScroll);

    // Safety timeout: if no {done:true} arrives within 3 minutes, unlock the
    // Composer so the pupil isn't stuck waiting forever. The model is slow on
    // CPU; 3 minutes is generous but prevents a permanent UI deadlock.
    _replyTimeoutTimer?.cancel();
    _replyTimeoutTimer = Timer(const Duration(minutes: 3), () {
      if (!mounted) return;
      final _Entry? streaming = _streamingHelperEntry();
      if (streaming != null) {
        setState(() {
          if (streaming.text.isEmpty) {
            streaming.text = 'Sorry, the response took too long. Please try again.';
          }
          streaming.isStreaming = false;
          _awaitingChatReply = false;
        });
      }
    });
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
    _replyTimeoutTimer?.cancel();
    _transcriptSub?.cancel();
    _promptCardSub?.cancel();
    _tappableTermSub?.cancel();
    _quizOpenSub?.cancel();
    _quizCloseSub?.cancel();
    _chatSub?.cancel();
    _transcriptClient.close();
    _chatClient.close();
    _quizRepository.dispose();
    if (widget.repository == null) {
      _repo.dispose();
    }
    _scroll.dispose();
    _chatScroll.dispose();
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
        title: Row(
          children: <Widget>[
            Expanded(
              child: Text(widget.session.title, style: LoopType.speaker),
            ),
            _SessionStatusPill(status: widget.session.status),
          ],
        ),
        leading: IconButton(
          tooltip: 'Back to lessons',
          icon: const Icon(Icons.arrow_back, color: LoopColors.ink),
          onPressed: () => Navigator.of(context).pop(),
        ),
      ),
      body: SafeArea(
        bottom: false,
        child: Stack(
          children: <Widget>[
            // The top-level layout is now a row split: teacher transcript
            // on the left, Class Helper conversation on the right. The
            // waveform and any error banner span both columns at the top.
            Column(
              children: <Widget>[
                Padding(
                  padding: const EdgeInsets.fromLTRB(36, 12, 36, 16),
                  child: Waveform(state: _waveState),
                ),
                if (_error != null) _ErrorBanner(message: _error!),
                Expanded(child: _buildBody()),
              ],
            ),
            if (_activeQuizQuestion != null)
              Positioned.fill(
                child: QuizModal(
                  // Key on the question id so a brand-new question replaces
                  // the modal cleanly instead of inheriting stale state.
                  key: ValueKey<int>(_activeQuizQuestion!.id),
                  question: _activeQuizQuestion!,
                  hubUri: widget.settings.hubUri,
                  pupilId: widget.settings.pupilId,
                  repository: _quizRepository,
                  onDismiss: () {
                    if (!mounted) return;
                    setState(() => _activeQuizQuestion = null);
                  },
                ),
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
    // Waiting room: session is live but teacher hasn't spoken yet.
    if (widget.session.isLive && _entries.isEmpty && _error == null) {
      return const _WaitingRoom();
    }
    // Two columns: teacher transcript on the left (with notebook gutter),
    // Class Helper conversation on the right. The pupil's chat is now a
    // permanent visual sidebar rather than a strip wedged underneath the
    // transcript — easier to keep an eye on both streams at once.
    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Expanded(child: _buildTranscriptColumn()),
        NotebookGutter(controller: _scroll),
        _buildChatColumn(context),
      ],
    );
  }

  Widget _buildTranscriptColumn() {
    final List<_Entry> teacherEntries = _entries
        .where((_Entry e) => e.speaker == _Speaker.teacher)
        .toList(growable: false);
    return ScrollConfiguration(
      // Main column is tap-only — pupils scroll via the right gutter.
      behavior: const _NoScrollBehaviour(),
      child: ListView.builder(
        controller: _scroll,
        padding: const EdgeInsets.fromLTRB(36, 8, 12, 24),
        itemCount: teacherEntries.isEmpty ? 1 : teacherEntries.length,
        itemBuilder: (BuildContext context, int i) {
          if (teacherEntries.isEmpty) {
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
          final _Entry e = teacherEntries[i];
          return ui.Turn(
            speaker: _label(e.speaker),
            text: e.text,
            isStreaming: e.isStreaming,
            tappable: true, // teacher turns only — tappable underlines apply
            terms: _tappableTerms,
          );
        },
      ),
    );
  }

  Widget _buildChatColumn(BuildContext context) {
    // Width tuned for an iPad in landscape — narrow enough that the
    // transcript still gets the lion's share, wide enough to read the
    // helper's streaming reply comfortably.
    const double chatColumnWidth = 360;
    final List<_Entry> chatEntries = _entries
        .where((_Entry e) => e.speaker != _Speaker.teacher)
        .toList(growable: false);
    return Container(
      width: chatColumnWidth,
      decoration: BoxDecoration(
        color: LoopColors.paperShade,
        border: Border(left: BorderSide(color: LoopColors.inkSoft)),
      ),
      child: Column(
        children: <Widget>[
          // Prompt cards float at the top of the chat column so they read
          // as suggested starters for the conversation below.
          PromptCardRow(
            cards: _promptCards,
            onCardTap: _onSend,
          ),
          Expanded(
            child: chatEntries.isEmpty
                ? _ChatEmptyState()
                : ListView.builder(
                    controller: _chatScroll,
                    padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
                    itemCount: chatEntries.length,
                    itemBuilder: (BuildContext context, int i) {
                      final _Entry e = chatEntries[i];
                      return ui.Turn(
                        speaker: _label(e.speaker),
                        text: e.text,
                        isStreaming: e.isStreaming,
                        // Tappable underlines only make sense on teacher
                        // turns; the pupil's own words and the helper's
                        // streamed reply stay plain.
                        tappable: false,
                        terms: _tappableTerms,
                      );
                    },
                  ),
          ),
          Composer(
            enabled: !_awaitingChatReply,
            onSend: _onSend,
            placeholder: _awaitingChatReply
                ? 'Class Helper is answering…'
                : 'Ask the Class Helper…',
          ),
        ],
      ),
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

/// Empty state for the chat column — visible until the pupil sends their
/// first question (or taps a prompt card, which has the same effect).
class _ChatEmptyState extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
        child: Text(
          'Tap a prompt card above, or type a question to ask the Class Helper.',
          textAlign: TextAlign.center,
          style: LoopType.ui.copyWith(color: LoopColors.inkMuted),
        ),
      ),
    );
  }
}


/// Shown when the session is live but no speech has arrived yet.
class _WaitingRoom extends StatelessWidget {
  const _WaitingRoom();

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 48),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            const SizedBox(
              width: 40,
              height: 40,
              child: CircularProgressIndicator(
                strokeWidth: 2,
                color: LoopColors.action,
              ),
            ),
            const SizedBox(height: 28),
            Text(
              'Waiting for your teacher…',
              textAlign: TextAlign.center,
              style: LoopType.ui.copyWith(color: LoopColors.inkMuted),
            ),
            const SizedBox(height: 8),
            Text(
              'The lesson transcript will appear here once your teacher begins.',
              textAlign: TextAlign.center,
              style: LoopType.caption.copyWith(height: 1.5),
            ),
          ],
        ),
      ),
    );
  }
}

/// LIVE / ENDED badge shown in the AppBar next to the session title.
class _SessionStatusPill extends StatelessWidget {
  const _SessionStatusPill({required this.status});

  final SessionStatus status;

  @override
  Widget build(BuildContext context) {
    final Color bg;
    final Color fg;
    final String label;

    switch (status) {
      case SessionStatus.open:
        bg = const Color(0xFFFFF3DC);
        fg = const Color(0xFFB96F00);
        label = 'OPEN';
        break;
      case SessionStatus.live:
        bg = const Color(0xFFDCE4F7);
        fg = LoopColors.action;
        label = 'LIVE';
        break;
      case SessionStatus.ended:
        bg = LoopColors.paperInput;
        fg = LoopColors.inkMuted;
        label = 'ENDED';
        break;
      case SessionStatus.unknown:
        return const SizedBox.shrink();
    }

    return Container(
      height: 26,
      padding: const EdgeInsets.symmetric(horizontal: 10),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          if (status == SessionStatus.live || status == SessionStatus.open)
            Container(
              width: 6,
              height: 6,
              margin: const EdgeInsets.only(right: 6),
              decoration: BoxDecoration(
                color: fg,
                shape: BoxShape.circle,
              ),
            ),
          Text(
            label,
            style: TextStyle(
              fontFamily: LoopType.family,
              fontSize: 11,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.5,
              color: fg,
            ),
          ),
        ],
      ),
    );
  }
}
