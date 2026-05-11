int _portFor(Uri hubUri) =>
    hubUri.hasPort ? hubUri.port : (hubUri.scheme == 'https' ? 443 : 80);

String _wsSchemeFor(Uri hubUri) => hubUri.scheme == 'https' ? 'wss' : 'ws';

Uri _http(Uri hubUri, String path) => Uri(
      scheme: hubUri.scheme,
      host: hubUri.host,
      port: _portFor(hubUri),
      path: path,
    );

Uri _ws(Uri hubUri, String path) => Uri(
      scheme: _wsSchemeFor(hubUri),
      host: hubUri.host,
      port: _portFor(hubUri),
      path: path,
    );

Uri wsUriForPupilChat(Uri hubUri, int pupilId) =>
    _ws(hubUri, '/pupil/ws/$pupilId/chat');

Uri wsUriForSessionTranscript(Uri hubUri, int sessionId) =>
    _ws(hubUri, '/session/ws/$sessionId/subscribe');

Uri pupilSessionsUri(Uri hubUri, int pupilId) =>
    _http(hubUri, '/pupil/$pupilId/sessions');

Uri sessionTranscriptUri(Uri hubUri, int sessionId) =>
    _http(hubUri, '/session/$sessionId/transcript');

Uri healthUri(Uri hubUri) => _http(hubUri, '/health');
