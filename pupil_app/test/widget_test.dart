import 'package:flutter_test/flutter_test.dart';

import 'package:pupil_app/app/app.dart';

void main() {
  testWidgets('shows connect screen on first launch', (WidgetTester tester) async {
    await tester.pumpWidget(const PupilApp());
    await tester.pumpAndSettle();

    expect(find.text('Connect to Loop Hub'), findsOneWidget);
    expect(find.text('Hub URL'), findsOneWidget);
    expect(find.text('Pupil ID'), findsOneWidget);
  });
}
