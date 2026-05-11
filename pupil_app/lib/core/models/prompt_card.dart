/// A single context-aware prompt card generated from the teacher's live speech.
class PromptCard {
  const PromptCard({required this.text, required this.colorKey});

  /// The question text shown on the card.
  final String text;

  /// Colour identifier: "blue", "green", or "amber".
  final String colorKey;

  factory PromptCard.fromJson(Map<String, dynamic> json) => PromptCard(
        text: json['text'] as String,
        colorKey: json['color'] as String,
      );
}
