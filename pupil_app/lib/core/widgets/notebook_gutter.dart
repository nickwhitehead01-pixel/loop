import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';

import '../../app/theme.dart';

/// Right-edge ruled strip that doubles as a scroll handle for the transcript.
///
/// 153 px wide, 16 px rule gap, rgba(0,0,0,0.12) lines. Driving the bound
/// [ScrollController] from this column keeps the main column tap-only —
/// pupils can't accidentally fire a quick-ask chip while scrolling.
class NotebookGutter extends StatelessWidget {
  const NotebookGutter({
    super.key,
    required this.controller,
  });

  final ScrollController controller;

  void _scrollBy(double delta) {
    if (!controller.hasClients) {
      return;
    }
    final double next = (controller.offset + delta).clamp(
      0.0,
      controller.position.maxScrollExtent,
    );
    controller.jumpTo(next);
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: LoopSpacing.gutterWidth,
      child: MouseRegion(
        cursor: SystemMouseCursors.resizeUpDown,
        child: Listener(
          onPointerSignal: (PointerSignalEvent event) {
            if (event is PointerScrollEvent) {
              _scrollBy(event.scrollDelta.dy);
            }
          },
          child: GestureDetector(
            behavior: HitTestBehavior.opaque,
            onVerticalDragUpdate: (DragUpdateDetails details) {
              _scrollBy(-details.delta.dy);
            },
            child: const _RuledLines(),
          ),
        ),
      ),
    );
  }
}

class _RuledLines extends StatelessWidget {
  const _RuledLines();

  @override
  Widget build(BuildContext context) {
    return CustomPaint(
      painter: _RulesPainter(),
      size: Size.infinite,
    );
  }
}

class _RulesPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final Paint paint = Paint()
      ..color = LoopColors.inkSoft
      ..strokeWidth = 1;
    for (double y = LoopSpacing.ruleGap; y < size.height; y += LoopSpacing.ruleGap) {
      canvas.drawLine(Offset(0, y), Offset(size.width, y), paint);
    }
  }

  @override
  bool shouldRepaint(covariant _RulesPainter oldDelegate) => false;
}
