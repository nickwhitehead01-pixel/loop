abstract class HubDiscoveryService {
  Future<List<DiscoveredHub>> discover({Duration timeout = const Duration(seconds: 3)});
}

class DiscoveredHub {
  const DiscoveredHub({required this.host, required this.port, this.displayName});

  final String host;
  final int port;
  final String? displayName;

  Uri get uri => Uri(scheme: 'http', host: host, port: port);
}

class StubHubDiscoveryService implements HubDiscoveryService {
  @override
  Future<List<DiscoveredHub>> discover({Duration timeout = const Duration(seconds: 3)}) async {
    return const <DiscoveredHub>[];
  }
}
