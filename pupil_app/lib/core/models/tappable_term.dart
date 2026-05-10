/// A word or short phrase the teacher's transcript-generation agent has
/// flagged as worth explaining to the pupil.
///
/// Rendered with a dotted underline in the transcript; tapping reveals the
/// pre-generated [explanation] in a popover.
class TappableTerm {
  const TappableTerm({
    required this.term,
    required this.explanation,
  });

  final String term;
  final String explanation;

  factory TappableTerm.fromJson(Map<String, dynamic> json) {
    return TappableTerm(
      term: (json['term'] as String? ?? '').trim(),
      explanation: (json['explanation'] as String? ?? '').trim(),
    );
  }
}
