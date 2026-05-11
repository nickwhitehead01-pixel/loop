import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

import '../../app/theme.dart';

enum WaveformState { paused, waiting, listening }

/// Status card with an animated bar field. Mirrors design/preview/waveform.html.
///
/// Pupils don't have access to teacher audio, so the waveform is driven by the
/// arrival of transcript chunks: each new chunk boosts the level for a few
/// seconds (listening), it decays to a low murmur (waiting), or sits flat when
/// the session is paused/ended.
class Waveform extends StatefulWidget {
  const Waveform({
    super.key,
    required this.state,
    this.barCount = 48,
  });

  final WaveformState state;
  final int barCount;

  @override
  State<Waveform> createState() => _WaveformState();
}

class _WaveformState extends State<Waveform> with SingleTickerProviderStateMixin {
  late final Ticker _ticker;
  final Random _rand = Random();
  late List<double> _levels = List<double>.filled(widget.barCount, 0.08);
  Duration _now = Duration.zero;

  @override
  void initState() {
    super.initState();
    _ticker = Ticker(_tick)..start();
  }

  @override
  void didUpdateWidget(covariant Waveform oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.barCount != widget.barCount) {
      _levels = List<double>.filled(widget.barCount, 0.08);
    }
  }

  void _tick(Duration elapsed) {
    _now = elapsed;
    final List<double> next = List<double>.filled(widget.barCount, 0);
    final double t = _now.inMilliseconds.toDouble();
    switch (widget.state) {
      case WaveformState.listening:
        for (int i = 0; i < widget.barCount; i++) {
          final double envelope = 0.55 + 0.35 * sin(t * 0.0022 + i * 0.18);
          final double wobble = sin(t * 0.009 + i * 0.6) * 0.35 +
              sin(t * 0.017 + i * 0.21) * 0.35 +
              (_rand.nextDouble() - 0.5) * 0.25;
          next[i] = (envelope + wobble * 0.5).clamp(0.05, 1.0);
        }
        break;
      case WaveformState.waiting:
        for (int i = 0; i < widget.barCount; i++) {
          final double amp = 0.08 +
              0.04 * sin(t * 0.004 + i * 0.4) +
              (_rand.nextDouble() - 0.5) * 0.02;
          next[i] = max(0, amp);
        }
        break;
      case WaveformState.paused:
        for (int i = 0; i < widget.barCount; i++) {
          next[i] = 0.06;
        }
        break;
    }
    setState(() {
      _levels = next;
    });
  }

  @override
  void dispose() {
    _ticker.dispose();
    super.dispose();
  }

  Color _barColor() {
    switch (widget.state) {
      case WaveformState.listening:
        return LoopColors.action;
      case WaveformState.waiting:
        return LoopColors.action.withValues(alpha: 0.45);
      case WaveformState.paused:
        return LoopColors.ink.withValues(alpha: 0.30);
    }
  }

  String _stateLabel() {
    switch (widget.state) {
      case WaveformState.listening:
        return 'LISTENING';
      case WaveformState.waiting:
        return 'WAITING…';
      case WaveformState.paused:
        return 'PAUSED';
    }
  }

  @override
  Widget build(BuildContext context) {
    final Color barColor = _barColor();
    return Container(
      decoration: BoxDecoration(
        color: LoopColors.paperShade,
        borderRadius: BorderRadius.circular(14),
      ),
      padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 16),
      child: Row(
        children: <Widget>[
          _StatusDot(state: widget.state),
          const SizedBox(width: 14),
          Expanded(
            child: SizedBox(
              height: 56,
              child: Row(
                children: <Widget>[
                  for (int i = 0; i < widget.barCount; i++) ...<Widget>[
                    Expanded(
                      child: AnimatedContainer(
                        duration: const Duration(milliseconds: 120),
                        height: 4 + _levels[i] * 48,
                        decoration: BoxDecoration(
                          color: barColor,
                          borderRadius: BorderRadius.circular(3),
                        ),
                      ),
                    ),
                    if (i < widget.barCount - 1) const SizedBox(width: 4),
                  ],
                ],
              ),
            ),
          ),
          const SizedBox(width: 14),
          SizedBox(
            width: 84,
            child: Text(
              _stateLabel(),
              textAlign: TextAlign.right,
              style: LoopType.caption,
            ),
          ),
        ],
      ),
    );
  }
}

class _StatusDot extends StatefulWidget {
  const _StatusDot({required this.state});

  final WaveformState state;

  @override
  State<_StatusDot> createState() => _StatusDotState();
}

class _StatusDotState extends State<_StatusDot> with SingleTickerProviderStateMixin {
  late final AnimationController _pulse = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1400),
  )..repeat();

  @override
  void dispose() {
    _pulse.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final Color color;
    switch (widget.state) {
      case WaveformState.listening:
        color = LoopColors.action;
        break;
      case WaveformState.waiting:
        color = LoopColors.action.withValues(alpha: 0.45);
        break;
      case WaveformState.paused:
        color = LoopColors.inkMuted;
        break;
    }
    return AnimatedBuilder(
      animation: _pulse,
      builder: (BuildContext context, _) {
        final double t = _pulse.value;
        final double radius = widget.state == WaveformState.listening ? 10 * t : 0;
        final double opacity = widget.state == WaveformState.listening ? (1 - t) * 0.45 : 0;
        return Container(
          width: 10,
          height: 10,
          decoration: BoxDecoration(
            color: color,
            shape: BoxShape.circle,
            boxShadow: <BoxShadow>[
              BoxShadow(
                color: LoopColors.action.withValues(alpha: opacity),
                blurRadius: 0,
                spreadRadius: radius,
              ),
            ],
          ),
        );
      },
    );
  }
}
