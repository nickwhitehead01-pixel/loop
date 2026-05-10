import 'dart:async';

import 'package:flutter/material.dart';

import '../../../app/theme.dart';
import '../../../core/models/hub_settings.dart';
import '../../../core/models/lesson_session.dart';
import '../../connection/presentation/connect_page.dart';
import '../data/sessions_repository.dart';
import 'transcript_page.dart';

/// Pupil's first stop after connect: pick which lesson session to enter.
/// Live sessions float to the top with a blue LIVE pill; ended ones list
/// below in muted ink so the pupil can revisit a past lesson.
///
/// Auto-polls every 5 seconds so a newly-started live session appears
/// without the pupil needing to pull-to-refresh.
class SessionPickerPage extends StatefulWidget {
  const SessionPickerPage({
    super.key,
    required this.settings,
    this.repository,
  });

  final HubSettings settings;
  final SessionsRepository? repository;

  @override
  State<SessionPickerPage> createState() => _SessionPickerPageState();
}

class _SessionPickerPageState extends State<SessionPickerPage> {
  late final SessionsRepository _repo = widget.repository ?? SessionsRepository();

  List<LessonSession>? _sessions;
  String? _error;
  bool _loading = false;
  bool _navigating = false;

  Timer? _pollTimer;

  @override
  void initState() {
    super.initState();
    _load();
    // Poll every 5 seconds so a live session appears automatically.
    _pollTimer = Timer.periodic(const Duration(seconds: 5), (_) => _silentLoad());
  }

  /// Full load — shows spinner on first fetch, replaces list on subsequent.
  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final List<LessonSession> result = await _repo.listForPupil(
        hubUri: widget.settings.hubUri,
        pupilId: widget.settings.pupilId,
      );
      _applyResult(result);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  /// Silent background refresh — no spinner, no error banner.
  Future<void> _silentLoad() async {
    if (_navigating) return;
    try {
      final List<LessonSession> result = await _repo.listForPupil(
        hubUri: widget.settings.hubUri,
        pupilId: widget.settings.pupilId,
      );
      _applyResult(result);
    } catch (_) {
      // Silently ignore poll errors — next tick will retry.
    }
  }

  void _applyResult(List<LessonSession> result) {
    result.sort((LessonSession a, LessonSession b) {
      if (a.isLive != b.isLive) return a.isLive ? -1 : 1;
      return b.startedAt.compareTo(a.startedAt);
    });
    if (!mounted) return;

    final List<LessonSession> prevSessions = _sessions ?? <LessonSession>[];
    final bool wasNoLive = !prevSessions.any((LessonSession s) => s.isLive);
    final List<LessonSession> nowLive =
        result.where((LessonSession s) => s.isLive).toList();

    setState(() {
      _sessions = result;
      _loading = false;
    });

    // Auto-navigate when a live session first appears and the pupil isn't
    // already inside a session. If multiple live sessions exist, let them choose.
    if (wasNoLive && nowLive.length == 1 && !_navigating) {
      _open(nowLive.first);
    }
  }

  void _open(LessonSession session) {
    _navigating = true;
    Navigator.of(context)
        .push(
          MaterialPageRoute<void>(
            builder: (_) => TranscriptPage(
              settings: widget.settings,
              session: session,
            ),
          ),
        )
        .then((_) {
      // Refresh immediately on return so status (live→ended) is up to date.
      _navigating = false;
      _load();
    });
  }

  void _editConnection() {
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<ConnectPage>(builder: (_) => const ConnectPage()),
    );
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    if (widget.repository == null) {
      _repo.dispose();
    }
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
        title: Text('Choose a lesson', style: LoopType.speaker),
        actions: <Widget>[
          IconButton(
            tooltip: 'Hub settings',
            onPressed: _editConnection,
            icon: const Icon(Icons.settings, color: LoopColors.ink),
          ),
        ],
      ),
      body: SafeArea(child: _buildBody()),
    );
  }

  Widget _buildBody() {
    if (_loading && _sessions == null) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null) {
      return _ErrorState(message: _error!, onRetry: _load);
    }
    final List<LessonSession> sessions = _sessions ?? <LessonSession>[];
    if (sessions.isEmpty) {
      return _EmptyState(onRefresh: _load);
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView.separated(
        padding: const EdgeInsets.symmetric(horizontal: 36, vertical: 24),
        itemCount: sessions.length,
        separatorBuilder: (_, _) => const SizedBox(height: 12),
        itemBuilder: (BuildContext context, int i) {
          final LessonSession s = sessions[i];
          return _SessionCard(session: s, onTap: () => _open(s));
        },
      ),
    );
  }
}

class _SessionCard extends StatelessWidget {
  const _SessionCard({required this.session, required this.onTap});

  final LessonSession session;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: LoopColors.paperShade,
      borderRadius: BorderRadius.circular(14),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(14),
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Row(
            children: <Widget>[
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(
                      session.title,
                      style: LoopType.dialogue.copyWith(fontSize: 22, height: 28 / 22),
                    ),
                    const SizedBox(height: 6),
                    Text(_formatStarted(session.startedAt), style: LoopType.caption),
                  ],
                ),
              ),
              const SizedBox(width: 16),
              _StatusPill(status: session.status),
            ],
          ),
        ),
      ),
    );
  }

  String _formatStarted(DateTime when) {
    final DateTime local = when.toLocal();
    final String hh = local.hour.toString().padLeft(2, '0');
    final String mm = local.minute.toString().padLeft(2, '0');
    return 'STARTED ${local.day}/${local.month}  $hh:$mm';
  }
}

class _StatusPill extends StatelessWidget {
  const _StatusPill({required this.status});

  final SessionStatus status;

  @override
  Widget build(BuildContext context) {
    final Color bg;
    final Color fg;
    final String label;
    switch (status) {
      case SessionStatus.open:
        bg = const Color(0xFFFFF3DC); // warm amber-soft
        fg = const Color(0xFFB96F00);
        label = 'OPEN';
        break;
      case SessionStatus.live:
        bg = const Color(0xFFDCE4F7); // info-soft
        fg = LoopColors.action;
        label = 'LIVE';
        break;
      case SessionStatus.ended:
        bg = LoopColors.paperInput;
        fg = LoopColors.inkMuted;
        label = 'ENDED';
        break;
      case SessionStatus.unknown:
        bg = LoopColors.paperInput;
        fg = LoopColors.inkMuted;
        label = '—';
    }
    return Container(
      height: 30,
      padding: const EdgeInsets.symmetric(horizontal: 14),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Container(
            width: 7,
            height: 7,
            decoration: BoxDecoration(
              color: fg.withValues(alpha: 0.85),
              shape: BoxShape.circle,
            ),
          ),
          const SizedBox(width: 7),
          Text(
            label,
            style: TextStyle(
              fontFamily: LoopType.family,
              fontSize: 12.5,
              height: 30 / 12.5,
              fontWeight: FontWeight.w600,
              letterSpacing: 12.5 * 0.02,
              color: fg,
            ),
          ),
        ],
      ),
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.onRefresh});

  final Future<void> Function() onRefresh;

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      onRefresh: onRefresh,
      child: ListView(
        padding: const EdgeInsets.symmetric(horizontal: 36, vertical: 60),
        children: <Widget>[
          Text('No lessons yet', style: LoopType.dialogue),
          const SizedBox(height: 8),
          Text(
            'When your teacher starts a lesson, it will show up here. Pull down to refresh.',
            style: LoopType.ui.copyWith(color: LoopColors.inkMuted),
          ),
        ],
      ),
    );
  }
}

class _ErrorState extends StatelessWidget {
  const _ErrorState({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(36),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text('Could not load lessons', style: LoopType.dialogue),
          const SizedBox(height: 8),
          Text(message, style: LoopType.ui.copyWith(color: LoopColors.inkMuted)),
          const SizedBox(height: 20),
          OutlinedButton(onPressed: onRetry, child: const Text('Retry')),
        ],
      ),
    );
  }
}
