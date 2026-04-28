Uri wsUriForPupilChat(Uri hubUri, int pupilId) {
  final String wsScheme = hubUri.scheme == 'https' ? 'wss' : 'ws';
  final int port = hubUri.hasPort ? hubUri.port : (hubUri.scheme == 'https' ? 443 : 80);

  return Uri(
    scheme: wsScheme,
    host: hubUri.host,
    port: port,
    path: '/pupil/ws/$pupilId/chat',
  );
}

Uri healthUri(Uri hubUri) {
  final int port = hubUri.hasPort ? hubUri.port : (hubUri.scheme == 'https' ? 443 : 80);

  return Uri(
    scheme: hubUri.scheme,
    host: hubUri.host,
    port: port,
    path: '/health',
  );
}
