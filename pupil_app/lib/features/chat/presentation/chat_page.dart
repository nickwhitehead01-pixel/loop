import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';

import '../../../core/models/chat_message.dart';
import '../../../core/models/hub_settings.dart';
import '../../connection/presentation/connect_page.dart';
import '../data/chat_socket_client.dart';

class ChatPage extends StatefulWidget {
  const ChatPage({super.key, required this.initialSettings});

  final HubSettings initialSettings;

  @override
  State<ChatPage> createState() => _ChatPageState();
}

class _ChatPageState extends State<ChatPage> {
  final TextEditingController _composerController = TextEditingController();
  final ChatSocketClient _client = ChatSocketClient();
  final List<ChatMessage> _messages = <ChatMessage>[];

  StreamSubscription<ChatStreamFrame>? _subscription;
  int? _activeAssistantIndex;
  bool _connected = false;
  bool _reconnecting = false;

  String _friendlyConnectionError(Object error) {
    if (error is TimeoutException) {
      return 'Connection timed out. Check that the backend is running.';
    }

    if (error is SocketException) {
      final String lower = error.toString().toLowerCase();
      if (lower.contains('connection refused')) {
        return 'Connection refused. Backend is likely not running.';
      }
      return 'Could not reach Hub. Check network and Hub URL.';
    }

    final String lower = error.toString().toLowerCase();
    if (lower.contains('timed out')) {
      return 'Connection timed out. Check that the backend is running.';
    }
    if (lower.contains('connection refused')) {
      return 'Connection refused. Backend is likely not running.';
    }
    if (lower.contains('failed host lookup') || lower.contains('no address associated')) {
      return 'Could not resolve Hub address. Check the Hub URL.';
    }

    return 'Could not connect to Hub. Please verify settings and retry.';
  }

  @override
  void initState() {
    super.initState();
    _connect();
  }

  Future<void> _connect() async {
    await _subscription?.cancel();
    final Stream<ChatStreamFrame> stream = _client.connect(
      widget.initialSettings.hubUri,
      widget.initialSettings.pupilId,
    );

    _subscription = stream.listen(
      (ChatStreamFrame frame) {
        if (!mounted) {
          return;
        }

        setState(() {
          _connected = true;

          if (_activeAssistantIndex != null && frame.token.isNotEmpty) {
            final ChatMessage current = _messages[_activeAssistantIndex!];
            _messages[_activeAssistantIndex!] = current.copyWith(text: current.text + frame.token);
          }

          if (frame.done) {
            _activeAssistantIndex = null;
          }

          if (frame.error != null && frame.error!.isNotEmpty) {
            _messages.add(ChatMessage(role: MessageRole.system, text: 'Stream error: ${frame.error}'));
          }
        });
      },
      onError: (Object error) {
        if (!mounted) {
          return;
        }
        final String friendly = _friendlyConnectionError(error);
        setState(() {
          _connected = false;
          if (_activeAssistantIndex != null) {
            final int index = _activeAssistantIndex!;
            final ChatMessage current = _messages[index];
            if (current.text.isEmpty) {
              _messages[index] = current.copyWith(text: friendly);
            }
            _activeAssistantIndex = null;
          }
          _messages.add(ChatMessage(role: MessageRole.system, text: friendly));
        });
      },
      onDone: () {
        if (!mounted) {
          return;
        }
        setState(() {
          _connected = false;
        });
      },
      cancelOnError: false,
    );
  }

  Future<void> _retryConnect() async {
    if (_reconnecting) {
      return;
    }

    setState(() {
      _reconnecting = true;
    });

    await _connect();

    if (!mounted) {
      return;
    }

    setState(() {
      _reconnecting = false;
    });
  }

  Future<void> _send() async {
    final String text = _composerController.text.trim();
    if (text.isEmpty) {
      return;
    }

    if (!_connected) {
      await _connect();
    }

    setState(() {
      _messages.add(ChatMessage(role: MessageRole.user, text: text));
      _messages.add(const ChatMessage(role: MessageRole.assistant, text: ''));
      _activeAssistantIndex = _messages.length - 1;
      _composerController.clear();
    });

    _client.sendMessage(message: text);
  }

  @override
  void dispose() {
    _composerController.dispose();
    _subscription?.cancel();
    _client.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('Pupil ${widget.initialSettings.pupilId}'),
        actions: <Widget>[
          IconButton(
            onPressed: () {
              Navigator.of(context).pushReplacement(
                MaterialPageRoute<ConnectPage>(builder: (_) => const ConnectPage()),
              );
            },
            icon: const Icon(Icons.settings),
            tooltip: 'Hub settings',
          ),
        ],
      ),
      body: SafeArea(
        child: Column(
          children: <Widget>[
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(12),
              color: _connected ? Colors.green.shade50 : Colors.orange.shade50,
              child: Row(
                children: <Widget>[
                  Expanded(
                    child: Text(_connected ? 'Connected to Hub' : 'Disconnected - reconnect on send'),
                  ),
                  if (!_connected)
                    TextButton.icon(
                      onPressed: _reconnecting ? null : _retryConnect,
                      icon: _reconnecting
                          ? const SizedBox(
                              width: 14,
                              height: 14,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(Icons.refresh),
                      label: Text(_reconnecting ? 'Retrying...' : 'Retry connection'),
                    ),
                ],
              ),
            ),
            Expanded(
              child: ListView.builder(
                padding: const EdgeInsets.all(12),
                itemCount: _messages.length,
                itemBuilder: (BuildContext context, int index) {
                  final ChatMessage message = _messages[index];
                  final bool isUser = message.role == MessageRole.user;
                  final Alignment align = isUser ? Alignment.centerRight : Alignment.centerLeft;
                  final Color color = isUser ? Colors.teal.shade100 : Colors.grey.shade200;
                  return Align(
                    alignment: align,
                    child: Container(
                      margin: const EdgeInsets.symmetric(vertical: 6),
                      padding: const EdgeInsets.all(12),
                      constraints: const BoxConstraints(maxWidth: 580),
                      decoration: BoxDecoration(
                        color: color,
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: Text(message.text.isEmpty ? '...' : message.text),
                    ),
                  );
                },
              ),
            ),
            Padding(
              padding: const EdgeInsets.all(12),
              child: Row(
                children: <Widget>[
                  Expanded(
                    child: TextField(
                      controller: _composerController,
                      decoration: const InputDecoration(labelText: 'Message'),
                      minLines: 1,
                      maxLines: 4,
                    ),
                  ),
                  const SizedBox(width: 8),
                  FilledButton(
                    onPressed: _send,
                    child: const Text('Send'),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
