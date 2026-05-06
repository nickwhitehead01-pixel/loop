import 'package:flutter/material.dart';

import '../../app/theme.dart';

/// One spoken turn — uppercase speaker label sat above body dialogue.
///
/// Mirrors `ui_kits/classroom/Turn.jsx`. The whole component reads as a single
/// "who said what" block with the design system's notebook rhythm (28/42 body,
/// 18 bold caps speaker).
class Turn extends StatelessWidget {
  const Turn({
    super.key,
    required this.speaker,
    required this.text,
    this.isStreaming = false,
  });

  final String speaker;
  final String text;

  /// While the assistant is mid-stream, append a soft caret so the pupil
  /// sees output is in flight without a separate spinner.
  final bool isStreaming;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: LoopSpacing.s5),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(speaker.toUpperCase(), style: LoopType.speaker),
          const SizedBox(height: LoopSpacing.s1),
          Text(
            isStreaming && text.isEmpty ? '…' : (isStreaming ? '$text ▍' : text),
            style: LoopType.dialogue,
          ),
        ],
      ),
    );
  }
}
