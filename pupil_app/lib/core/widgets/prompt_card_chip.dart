import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../models/prompt_card.dart';

/// A pill-shaped chip that displays a single prompt card.
/// Tapping it calls [onTap] with the card's question text.
class PromptCardChip extends StatelessWidget {
  const PromptCardChip({
    super.key,
    required this.card,
    required this.onTap,
  });

  final PromptCard card;
  final void Function(String text) onTap;

  @override
  Widget build(BuildContext context) {
    final Color background = LoopCardColors.colorFor(card.colorKey);
    return Material(
      color: background,
      borderRadius: BorderRadius.circular(40),
      child: InkWell(
        borderRadius: BorderRadius.circular(40),
        onTap: () => onTap(card.text),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 12),
          child: Text(
            card.text,
            style: LoopType.ui.copyWith(
              fontSize: 14,
              color: LoopColors.onActionAlt,
              fontWeight: FontWeight.w500,
            ),
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ),
    );
  }
}
