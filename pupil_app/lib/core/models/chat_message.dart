class ChatMessage {
  const ChatMessage({required this.role, required this.text});

  final MessageRole role;
  final String text;

  ChatMessage copyWith({MessageRole? role, String? text}) {
    return ChatMessage(role: role ?? this.role, text: text ?? this.text);
  }
}

enum MessageRole { user, assistant, system }
