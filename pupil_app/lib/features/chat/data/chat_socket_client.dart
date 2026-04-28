import 'dart:convert';

import 'package:web_socket_channel/io.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../../../core/networking/hub_uri.dart';

class ChatSocketClient {
  WebSocketChannel? _channel;

  Stream<ChatStreamFrame> connect(Uri hubUri, int pupilId) {
    final Uri socketUri = wsUriForPupilChat(hubUri, pupilId);
    _channel = IOWebSocketChannel.connect(socketUri);

    return _channel!.stream.map((dynamic data) {
      final dynamic payload = jsonDecode(data as String);
      final String token = (payload['token'] ?? '').toString();
      final bool done = payload['done'] == true;
      return ChatStreamFrame(token: token, done: done);
    }).handleError((Object error) {
      return ChatStreamFrame(token: '', done: true, error: error.toString());
    });
  }

  void sendMessage({required String message, String? conversationId, String? sessionId}) {
    final Map<String, dynamic> payload = <String, dynamic>{
      'message': message,
      'conversation_id': conversationId,
      'session_id': sessionId,
    };
    _channel?.sink.add(jsonEncode(payload));
  }

  Future<void> close() async {
    await _channel?.sink.close();
  }
}

class ChatStreamFrame {
  const ChatStreamFrame({required this.token, required this.done, this.error});

  final String token;
  final bool done;
  final String? error;
}
