import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../models/tappable_term.dart';
import 'tappable_text.dart';

/// One spoken turn — uppercase speaker label sat above body dialogue.
///
/// Mirrors `ui_kits/classroom/Turn.jsx`. The whole component reads as a single
/// "who said what" block with the design system's notebook rhythm (28/42 body,
/// 18 bold caps speaker).
///
/// If [terms] is non-empty, words and phrases that match any known
/// [TappableTerm] are rendered with a dotted underline and become tappable —
/// tap reveals the pre-generated explanation inline (the sentence flows
/// around it), tap again to collapse.
class Turn extends StatelessWidget {
  const Turn({
    super.key,
    required this.speaker,
    required this.text,
    this.isStreaming = false,
    this.terms = const <String, TappableTerm>{},
    this.tappable = true,
  });

  final String speaker;
  final String text;

  /// While the assistant is mid-stream, append a soft caret so the pupil
  /// sees output is in flight without a separate spinner.
  final bool isStreaming;

  /// Lookup keyed by lowercased `TappableTerm.term`. Pass an empty map to
  /// render plain text (e.g. for the pupil's own messages where tappable
  /// underlines would just be visual noise).
  final Map<String, TappableTerm> terms;

  /// When false (e.g. the pupil's own `YOU` turn), the body renders as plain
  /// text regardless of what's in [terms].
  final bool tappable;

  @override
  Widget build(BuildContext context) {
    final String body =
        isStreaming && text.isEmpty ? '…' : (isStreaming ? '$text ▍' : text);
    final bool useTappable = tappable && terms.isNotEmpty && !isStreaming;
    return Padding(
      padding: const EdgeInsets.only(bottom: LoopSpacing.s5),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(speaker.toUpperCase(), style: LoopType.speaker),
          const SizedBox(height: LoopSpacing.s1),
          if (useTappable)
            TappableText(
              text: body,
              terms: terms,
              style: LoopType.dialogue,
            )
          else
            Text(body, style: LoopType.dialogue),
        ],
      ),
    );
  }
}
