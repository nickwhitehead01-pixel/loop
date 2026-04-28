import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import '../../../core/networking/hub_uri.dart';

class HubConnectionRepository {
  const HubConnectionRepository();

  Future<HubHealthCheckResult> testHealth(Uri hubUri) async {
    final Uri uri = healthUri(hubUri);
    try {
      final http.Response response = await http.get(uri).timeout(const Duration(seconds: 4));

      if (response.statusCode != 200) {
        return HubHealthCheckResult(
          isReachable: false,
          userMessage: 'Hub responded, but /health returned ${response.statusCode}.',
          technicalError: 'HTTP ${response.statusCode}',
        );
      }

      if (response.body.isEmpty) {
        return const HubHealthCheckResult(
          isReachable: true,
          userMessage: 'Hub is reachable.',
        );
      }

      try {
        final dynamic parsed = jsonDecode(response.body);
        if (parsed is Map<String, dynamic>) {
          return const HubHealthCheckResult(
            isReachable: true,
            userMessage: 'Hub is reachable.',
          );
        }
        return const HubHealthCheckResult(
          isReachable: true,
          userMessage: 'Hub is reachable.',
        );
      } catch (_) {
        return const HubHealthCheckResult(
          isReachable: true,
          userMessage: 'Hub is reachable.',
        );
      }
    } on TimeoutException catch (error) {
      return HubHealthCheckResult(
        isReachable: false,
        userMessage: 'Connection timed out. Check that the backend is running.',
        technicalError: error.toString(),
      );
    } on SocketException catch (error) {
      final String lower = error.toString().toLowerCase();
      if (lower.contains('connection refused')) {
        return HubHealthCheckResult(
          isReachable: false,
          userMessage: 'Connection was refused. Backend is likely not running.',
          technicalError: error.toString(),
        );
      }

      return HubHealthCheckResult(
        isReachable: false,
        userMessage: 'Could not reach Hub. Check URL, network, and backend status.',
        technicalError: error.toString(),
      );
    } on http.ClientException catch (error) {
      return HubHealthCheckResult(
        isReachable: false,
        userMessage: 'Invalid network request. Verify the Hub URL format.',
        technicalError: error.toString(),
      );
    } catch (error) {
      return HubHealthCheckResult(
        isReachable: false,
        userMessage: 'Could not connect to Hub right now.',
        technicalError: error.toString(),
      );
    }
  }
}

class HubHealthCheckResult {
  const HubHealthCheckResult({
    required this.isReachable,
    required this.userMessage,
    this.technicalError,
  });

  final bool isReachable;
  final String userMessage;
  final String? technicalError;
}
