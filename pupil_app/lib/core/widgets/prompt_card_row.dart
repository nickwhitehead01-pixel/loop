import 'package:flutter/material.dart';

import '../models/prompt_card.dart';
import 'prompt_card_chip.dart';

/// A horizontally scrollable row of [PromptCardChip]s.
///
/// Disappears (zero height) when [cards] is empty so it takes no layout space
/// between the waveform and the transcript feed.
class PromptCardRow extends StatelessWidget {
  const PromptCardRow({
    super.key,
    required this.cards,
    required this.onCardTap,
  });

  final List<PromptCard> cards;
  final void Function(String text) onCardTap;

  @override
  Widget build(BuildContext context) {
    return AnimatedSwitcher(
      duration: const Duration(milliseconds: 300),
      transitionBuilder: (Widget child, Animation<double> animation) =>
          FadeTransition(opacity: animation, child: child),
      child: cards.isEmpty
          ? const SizedBox.shrink()
          : SizedBox(
              key: ValueKey<int>(cards.length),
              height: 80,
              child: ListView.separated(
                scrollDirection: Axis.horizontal,
                padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
                itemCount: cards.length,
                separatorBuilder: (_, __) => const SizedBox(width: 10),
                itemBuilder: (BuildContext context, int i) => PromptCardChip(
                  card: cards[i],
                  onTap: onCardTap,
                ),
              ),
            ),
    );
  }
}
