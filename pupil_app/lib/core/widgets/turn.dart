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
    // Three render modes:
    //   1. Pre-token streaming  — show animated "thinking" dots
    //   2. Mid-stream            — show text plus a trailing caret
    //   3. Settled                — plain text, tappable if terms supplied
    final bool isThinking = isStreaming && text.isEmpty;
    final String body = isStreaming ? '$text ▍' : text;
    final bool useTappable = tappable && terms.isNotEmpty && !isStreaming;
    return Padding(
      padding: const EdgeInsets.only(bottom: LoopSpacing.s5),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(speaker.toUpperCase(), style: LoopType.speaker),
          const SizedBox(height: LoopSpacing.s1),
          if (isThinking)
            const _ThinkingDots()
          else if (useTappable)
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


/// Three softly-pulsing dots shown while the assistant is composing a reply
/// but hasn't streamed its first token yet. The classic "typing indicator"
/// shape — culturally instantly readable as "waiting on a response".
///
/// Each dot has its own staggered opacity Tween so they animate in sequence
/// (wave style), not all in unison. Total cycle is ~1.2s which reads as
/// "thinking" rather than "stuck".
class _ThinkingDots extends StatefulWidget {
  const _ThinkingDots();

  @override
  State<_ThinkingDots> createState() => _ThinkingDotsState();
}

class _ThinkingDotsState extends State<_ThinkingDots>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1200),
  )..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  // Each dot's opacity follows a sin-like curve offset by 1/3 of the cycle
  // from its neighbour, so the wave reads left-to-right.
  double _opacityFor(double t, double offset) {
    final double phase = (t + offset) % 1.0;
    // 0 → 1 → 0 over the cycle, clamped to a visible floor so a "resting"
    // dot isn't invisible.
    final double wave = phase < 0.5 ? phase * 2 : (1 - phase) * 2;
    return 0.25 + 0.75 * wave;
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _controller,
      builder: (BuildContext context, _) {
        final double t = _controller.value;
        return Row(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            _dot(_opacityFor(t, 0.00)),
            const SizedBox(width: 6),
            _dot(_opacityFor(t, 0.20)),
            const SizedBox(width: 6),
            _dot(_opacityFor(t, 0.40)),
          ],
        );
      },
    );
  }

  Widget _dot(double opacity) {
    return Opacity(
      opacity: opacity,
      child: Container(
        width: 8,
        height: 8,
        decoration: const BoxDecoration(
          color: LoopColors.action,
          shape: BoxShape.circle,
        ),
      ),
    );
  }
}
