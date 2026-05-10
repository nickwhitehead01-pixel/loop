import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../models/tappable_term.dart';

/// Renders [text] with any occurrence of a flagged [TappableTerm] wearing
/// the Looplense dotted underline. Tapping a matched word reveals its
/// explanation **inline** — the sentence flows around it — and tapping again
/// collapses it.
///
/// Mirrors the `tappable-word` preview in the design system:
/// - dotted blue underline at rest
/// - tap → em-dash + muted explanation expands inline between this word and
///   the next
/// - tinted background while open so the active word is easy to find
class TappableText extends StatefulWidget {
  const TappableText({
    super.key,
    required this.text,
    required this.terms,
    required this.style,
  });

  /// The full transcript chunk to render.
  final String text;

  /// Currently-known terms keyed by lowercased [TappableTerm.term].
  final Map<String, TappableTerm> terms;

  /// Style applied to non-tappable runs. Tappable runs inherit and overlay
  /// the dotted underline; inline definitions inherit and re-style.
  final TextStyle style;

  @override
  State<TappableText> createState() => _TappableTextState();
}

class _TappableTextState extends State<TappableText> {
  /// Per-occurrence open state. Indexed by match position so two occurrences
  /// of the same term open independently. Rebuilt whenever [text] or [terms]
  /// changes (e.g. when Gemma flags a new term mid-lesson and this turn
  /// retroactively picks up underlines).
  late List<ValueNotifier<bool>> _open;
  late List<_Match> _matches;
  late List<TapGestureRecognizer> _recognizers;

  @override
  void initState() {
    super.initState();
    _rebuildMatches();
  }

  @override
  void didUpdateWidget(covariant TappableText oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.text != widget.text ||
        !_termSetEqual(oldWidget.terms, widget.terms)) {
      _disposeMatches();
      _rebuildMatches();
    }
  }

  bool _termSetEqual(Map<String, TappableTerm> a, Map<String, TappableTerm> b) {
    if (a.length != b.length) return false;
    for (final String k in a.keys) {
      if (!b.containsKey(k)) return false;
    }
    return true;
  }

  void _rebuildMatches() {
    _matches = _findMatches(widget.text, widget.terms);
    _open = List<ValueNotifier<bool>>.generate(
      _matches.length,
      (_) => ValueNotifier<bool>(false),
    );
    _recognizers = List<TapGestureRecognizer>.generate(_matches.length, (int i) {
      return TapGestureRecognizer()
        ..onTap = () {
          _open[i].value = !_open[i].value;
        };
    });
  }

  void _disposeMatches() {
    for (final TapGestureRecognizer r in _recognizers) {
      r.dispose();
    }
    for (final ValueNotifier<bool> n in _open) {
      n.dispose();
    }
  }

  @override
  void dispose() {
    _disposeMatches();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (widget.text.isEmpty) {
      return Text(widget.text, style: widget.style);
    }
    if (_matches.isEmpty) {
      return Text(widget.text, style: widget.style);
    }

    final List<InlineSpan> spans = <InlineSpan>[];
    int cursor = 0;
    for (int i = 0; i < _matches.length; i++) {
      final _Match m = _matches[i];
      if (m.start > cursor) {
        spans.add(TextSpan(text: widget.text.substring(cursor, m.start), style: widget.style));
      }
      final String original = widget.text.substring(m.start, m.end);
      spans.add(_buildTappableSpan(original, m.term, _open[i], _recognizers[i]));
      spans.add(WidgetSpan(
        alignment: PlaceholderAlignment.baseline,
        baseline: TextBaseline.alphabetic,
        child: _InlineDefinition(
          term: m.term,
          openNotifier: _open[i],
          baseStyle: widget.style,
        ),
      ));
      cursor = m.end;
    }
    if (cursor < widget.text.length) {
      spans.add(TextSpan(text: widget.text.substring(cursor), style: widget.style));
    }

    return Text.rich(TextSpan(style: widget.style, children: spans));
  }

  TextSpan _buildTappableSpan(
    String original,
    TappableTerm term,
    ValueNotifier<bool> openNotifier,
    TapGestureRecognizer recognizer,
  ) {
    return TextSpan(
      text: original,
      style: widget.style.copyWith(
        decoration: TextDecoration.underline,
        decorationStyle: TextDecorationStyle.dotted,
        decorationThickness: 2,
        decorationColor: const Color(0xBF3A66DB), // 75% --action
      ),
      recognizer: recognizer,
      semanticsLabel: '$original (tap for explanation)',
    );
  }
}

/// Animated inline reveal — collapsed to zero width when closed, grows to its
/// natural width with a smooth ease-out when [openNotifier] is true.
class _InlineDefinition extends StatelessWidget {
  const _InlineDefinition({
    required this.term,
    required this.openNotifier,
    required this.baseStyle,
  });

  final TappableTerm term;
  final ValueNotifier<bool> openNotifier;
  final TextStyle baseStyle;

  @override
  Widget build(BuildContext context) {
    // The body inherits the surrounding font and size so the line height
    // stays consistent; only the weight/colour change to signal it's
    // ancillary text. Em-dash carries the brand blue at the design's 65%.
    final TextStyle bodyStyle = baseStyle.copyWith(
      color: LoopColors.inkMuted,
      fontWeight: FontWeight.w500,
    );
    final TextStyle dashStyle = baseStyle.copyWith(
      color: LoopColors.action.withValues(alpha: 0.65),
      fontWeight: FontWeight.w700,
    );

    return ValueListenableBuilder<bool>(
      valueListenable: openNotifier,
      builder: (BuildContext context, bool open, _) {
        return AnimatedSize(
          duration: const Duration(milliseconds: 320),
          curve: Curves.easeOutCubic,
          alignment: Alignment.centerLeft,
          child: open
              ? Padding(
                  // Tiny left margin matches the design's 4-6 px gap so the
                  // explanation reads as separate from the underlined word.
                  padding: const EdgeInsets.only(left: 4, right: 4),
                  child: AnimatedOpacity(
                    duration: const Duration(milliseconds: 220),
                    opacity: 1,
                    child: Text.rich(
                      TextSpan(
                        children: <InlineSpan>[
                          TextSpan(text: '— ', style: dashStyle),
                          TextSpan(text: term.explanation, style: bodyStyle),
                        ],
                      ),
                    ),
                  ),
                )
              : const SizedBox.shrink(),
        );
      },
    );
  }
}

class _Match {
  const _Match({required this.start, required this.end, required this.term});
  final int start;
  final int end;
  final TappableTerm term;
}

/// Finds all non-overlapping matches of [terms] in [text], preferring the
/// longest possible match at each cursor position.
List<_Match> _findMatches(String text, Map<String, TappableTerm> terms) {
  if (terms.isEmpty) return const <_Match>[];

  final List<TappableTerm> ordered = terms.values.toList()
    ..sort((TappableTerm a, TappableTerm b) => b.term.length.compareTo(a.term.length));

  // `\b` word boundaries — happy to underline "York" inside "York's" but
  // never inside "wolfish".
  final List<RegExp> patterns = ordered.map((TappableTerm t) {
    final String escaped = RegExp.escape(t.term);
    return RegExp(r'\b' + escaped + r'\b', caseSensitive: false);
  }).toList();

  final List<_Match> hits = <_Match>[];
  int cursor = 0;
  while (cursor < text.length) {
    int bestEnd = -1;
    TappableTerm? bestTerm;
    int bestStart = -1;
    for (int i = 0; i < ordered.length; i++) {
      final Match? m = patterns[i].matchAsPrefix(text, cursor);
      if (m != null && (m.end - m.start) > (bestEnd - bestStart)) {
        bestStart = m.start;
        bestEnd = m.end;
        bestTerm = ordered[i];
      }
    }
    if (bestTerm != null && bestEnd > bestStart) {
      hits.add(_Match(start: bestStart, end: bestEnd, term: bestTerm));
      cursor = bestEnd;
    } else {
      cursor += 1;
    }
  }
  return hits;
}
