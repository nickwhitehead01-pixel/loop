import 'dart:async';

import 'package:flutter/material.dart';

import '../../../app/theme.dart';
import '../../../core/models/quiz_question.dart';
import '../data/quiz_repository.dart';

/// Full-width sheet that drops over the transcript while a quiz question is
/// open. It owns the countdown ticker and the answer-submit lifecycle, and
/// dismisses itself when one of:
///   - the timer hits zero
///   - the teacher manually closes the question (parent removes the widget)
///   - the pupil submits successfully
/// happens.
///
/// The widget intentionally does NOT show grades back to the pupil — grading
/// is a teacher-facing concept in v1. After submit it just says "Sent!" and
/// fades out a couple of seconds later. Grade-reveal can come later.
class QuizModal extends StatefulWidget {
  const QuizModal({
    super.key,
    required this.question,
    required this.hubUri,
    required this.pupilId,
    required this.repository,
    required this.onDismiss,
  });

  final QuizQuestion question;
  final Uri hubUri;
  final int pupilId;
  final QuizRepository repository;
  final VoidCallback onDismiss;

  @override
  State<QuizModal> createState() => _QuizModalState();
}

// Note: there is no explicit `error` state — submission errors stay in
// `answering` with a non-null `_errorMessage`, so the pupil can edit their
// answer and retry without losing what they typed.
enum _QuizModalStatus { answering, submitting, submitted, expired }

class _QuizModalState extends State<QuizModal> {
  late final TextEditingController _controller = TextEditingController();
  _QuizModalStatus _status = _QuizModalStatus.answering;
  String? _errorMessage;
  Timer? _ticker;
  Duration _remaining = Duration.zero;

  /// Local-clock deadline. We deliberately ignore widget.question.deadlineMs
  /// (the server's wall-clock millis) — SQLite strips tz info, and a naive
  /// .timestamp() call on the server can make the deadline off by hours
  /// depending on the server's timezone. The pupil-side countdown is pure UX;
  /// the server still drives the actual close via quiz_question_closed.
  late final int _localDeadlineMs;

  @override
  void initState() {
    super.initState();
    _localDeadlineMs = DateTime.now().millisecondsSinceEpoch +
        widget.question.timeLimitSeconds * 1000;
    _tick();
    _ticker = Timer.periodic(const Duration(milliseconds: 200), (_) => _tick());
  }

  void _tick() {
    if (!mounted) return;
    final int now = DateTime.now().millisecondsSinceEpoch;
    final int remainingMs = _localDeadlineMs - now;
    setState(() {
      _remaining = Duration(milliseconds: remainingMs.clamp(0, 1 << 31));
    });
    if (remainingMs <= 0 && _status == _QuizModalStatus.answering) {
      // Time's up locally — lock input but stay mounted; the real dismiss
      // happens when the server's quiz_question_closed event arrives.
      setState(() => _status = _QuizModalStatus.expired);
      _ticker?.cancel();
    }
  }

  Future<void> _submit() async {
    final String answer = _controller.text.trim();
    if (answer.isEmpty) {
      setState(() => _errorMessage = 'Type your answer first.');
      return;
    }
    setState(() {
      _status = _QuizModalStatus.submitting;
      _errorMessage = null;
    });
    final QuizSubmitResult result = await widget.repository.submitAnswer(
      hubUri: widget.hubUri,
      pupilId: widget.pupilId,
      questionId: widget.question.id,
      answer: answer,
    );
    if (!mounted) return;
    switch (result) {
      case QuizSubmitResult.ok:
        setState(() => _status = _QuizModalStatus.submitted);
        // Self-dismiss after a beat so the pupil sees confirmation but the
        // transcript comes back into view without them having to tap.
        Future<void>.delayed(const Duration(seconds: 2), () {
          if (mounted) widget.onDismiss();
        });
      case QuizSubmitResult.tooLate:
        setState(() => _status = _QuizModalStatus.expired);
      case QuizSubmitResult.error:
        setState(() {
          _status = _QuizModalStatus.answering;
          _errorMessage = 'Could not send your answer — try again.';
        });
    }
  }

  @override
  void dispose() {
    _ticker?.cancel();
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final int seconds = _remaining.inMilliseconds <= 0
        ? 0
        : (_remaining.inMilliseconds / 1000).ceil();
    final bool urgent = seconds <= 5 && _status == _QuizModalStatus.answering;
    final bool inputLocked = _status != _QuizModalStatus.answering;

    // Plain ColoredBox for the scrim, NOT Material — wrapping the whole tree
    // in Material(color: black) was making the inner TextField inherit a dark
    // surface theme, which is why it looked unresponsive. The card itself is
    // its own Material so the TextField gets proper light-theme defaults.
    return ColoredBox(
      color: Colors.black54,
      child: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 560),
          child: Material(
            color: LoopColors.paper,
            borderRadius: BorderRadius.circular(20),
            elevation: 8,
            child: Padding(
            padding: const EdgeInsets.fromLTRB(28, 24, 28, 24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Row(
                  children: <Widget>[
                    Expanded(
                      child: Text(
                        'Quiz time!',
                        style: LoopType.speaker,
                      ),
                    ),
                    Text(
                      '${seconds}s',
                      style: LoopType.speaker.copyWith(
                        color: urgent ? Colors.red : LoopColors.action,
                        fontFeatures: const <FontFeature>[FontFeature.tabularFigures()],
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 16),
                Text(
                  widget.question.questionText,
                  style: LoopType.dialogue,
                ),
                const SizedBox(height: 16),
                TextField(
                  controller: _controller,
                  enabled: !inputLocked,
                  autofocus: true,
                  maxLines: 2,
                  textInputAction: TextInputAction.send,
                  onSubmitted: (_) => _submit(),
                  decoration: InputDecoration(
                    hintText: 'Type your answer…',
                    border: const OutlineInputBorder(),
                    filled: true,
                    fillColor: LoopColors.paperShade,
                  ),
                ),
                if (_errorMessage != null) ...<Widget>[
                  const SizedBox(height: 8),
                  Text(_errorMessage!, style: LoopType.ui.copyWith(color: Colors.red)),
                ],
                const SizedBox(height: 16),
                Row(
                  mainAxisAlignment: MainAxisAlignment.end,
                  children: <Widget>[
                    if (_status == _QuizModalStatus.submitted)
                      Text('Sent!', style: LoopType.ui.copyWith(color: LoopColors.action)),
                    if (_status == _QuizModalStatus.expired)
                      Text("Time's up", style: LoopType.ui.copyWith(color: LoopColors.inkMuted)),
                    if (_status == _QuizModalStatus.answering ||
                        _status == _QuizModalStatus.submitting)
                      FilledButton(
                        onPressed: _status == _QuizModalStatus.answering
                            ? _submit
                            : null,
                        child: Text(
                          _status == _QuizModalStatus.submitting
                              ? 'Sending…'
                              : 'Send answer',
                        ),
                      ),
                  ],
                ),
              ],
            ),
          ),
          ),
        ),
      ),
    );
  }
}
