import 'package:flutter/material.dart';

import '../../app/theme.dart';

/// Bottom composer shell from the design system: cream sunken surface,
/// outlined input, primary chip "Send".
class Composer extends StatefulWidget {
  const Composer({
    super.key,
    required this.onSend,
    required this.enabled,
    this.placeholder = 'Ask the Class Helper…',
  });

  final ValueChanged<String> onSend;
  final bool enabled;
  final String placeholder;

  @override
  State<Composer> createState() => _ComposerState();
}

class _ComposerState extends State<Composer> {
  final TextEditingController _controller = TextEditingController();
  bool _hasText = false;

  @override
  void initState() {
    super.initState();
    _controller.addListener(() {
      final bool next = _controller.text.trim().isNotEmpty;
      if (next != _hasText) {
        setState(() => _hasText = next);
      }
    });
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _submit() {
    final String text = _controller.text.trim();
    if (text.isEmpty || !widget.enabled) {
      return;
    }
    widget.onSend(text);
    _controller.clear();
  }

  @override
  Widget build(BuildContext context) {
    final bool canSend = widget.enabled && _hasText;
    return Container(
      color: LoopColors.paperShade,
      padding: const EdgeInsets.fromLTRB(36, 20, 36, 20),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: <Widget>[
          Expanded(
            child: TextField(
              controller: _controller,
              enabled: widget.enabled,
              minLines: 1,
              maxLines: 4,
              textInputAction: TextInputAction.send,
              onSubmitted: (_) => _submit(),
              style: LoopType.dialogue.copyWith(fontSize: 18, height: 24 / 18),
              decoration: InputDecoration(hintText: widget.placeholder),
            ),
          ),
          const SizedBox(width: 16),
          _SendChip(enabled: canSend, onPressed: _submit),
        ],
      ),
    );
  }
}

class _SendChip extends StatelessWidget {
  const _SendChip({required this.enabled, required this.onPressed});

  final bool enabled;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    return Opacity(
      opacity: enabled ? 1 : 0.4,
      child: InkWell(
        onTap: enabled ? onPressed : null,
        borderRadius: const BorderRadius.all(LoopRadius.md),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 14),
          decoration: BoxDecoration(
            color: LoopColors.action,
            borderRadius: const BorderRadius.all(LoopRadius.md),
            boxShadow: const <BoxShadow>[
              BoxShadow(
                color: Color(0x40000000),
                blurRadius: 4,
                offset: Offset(0, 4),
              ),
            ],
          ),
          child: Text(
            'Send',
            style: LoopType.ui.copyWith(color: LoopColors.snow),
          ),
        ),
      ),
    );
  }
}
