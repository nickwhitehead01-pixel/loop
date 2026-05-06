import 'package:flutter/material.dart';

/// Looplense design tokens. Source of truth: design/looplense/colors_and_type.css.
class LoopColors {
  static const Color paper = Color(0xFFF7F3E9);
  static const Color paperShade = Color(0xFFE5E1D7);
  static const Color paperInput = Color(0xFFFAF3E2);

  static const Color ink = Color(0xFF2B2B2B);
  static const Color inkBlack = Color(0xFF000000);
  static const Color inkSoft = Color(0x1F000000); // rgba(0,0,0,0.12)
  static const Color inkMuted = Color(0xA32B2B2B); // rgba(43,43,43,0.64)

  static const Color action = Color(0xFF3A66DB);
  static const Color actionHover = Color(0xFF4570E0);
  static const Color actionPress = Color(0xFF3359C2);
  static const Color actionBright = Color(0xFF2F6FED);

  static const Color snow = Color(0xFFF7F3E9);
  static const Color onActionAlt = Color(0xFFFFFFFF);

  static const Color focusRing = Color(0x593A66DB); // rgba(58,102,219,0.35)

  static const Color success = Color(0xFF2D7A3F);
  static const Color successSoft = Color(0xFFE7EEDE);
  static const Color warn = Color(0xFF9A6A1E);
  static const Color warnSoft = Color(0xFFF3E7CF);
  static const Color error = Color(0xFFA03636);
  static const Color errorSoft = Color(0xFFF2D9D6);
}

class LoopType {
  static const String family = 'Lexend';

  static const TextStyle display = TextStyle(
    fontFamily: family,
    fontSize: 48,
    height: 72 / 48,
    fontWeight: FontWeight.w400,
    color: LoopColors.ink,
  );

  /// 28/42 — body dialogue rendered in the transcript.
  static const TextStyle dialogue = TextStyle(
    fontFamily: family,
    fontSize: 28,
    height: 42 / 28,
    fontWeight: FontWeight.w400,
    color: LoopColors.ink,
  );

  /// 18 bold ALL-CAPS speaker label sat above each turn.
  static const TextStyle speaker = TextStyle(
    fontFamily: family,
    fontSize: 18,
    height: 1.0,
    fontWeight: FontWeight.w700,
    letterSpacing: 18 * 0.02,
    color: LoopColors.ink,
  );

  static const TextStyle ui = TextStyle(
    fontFamily: family,
    fontSize: 16,
    height: 1.0,
    fontWeight: FontWeight.w500,
    color: LoopColors.ink,
  );

  /// Small uppercase utility caption — used for state labels (LISTENING / WAITING…).
  static const TextStyle caption = TextStyle(
    fontFamily: family,
    fontSize: 11,
    height: 12 / 11,
    fontWeight: FontWeight.w600,
    letterSpacing: 11 * 0.08,
    color: LoopColors.inkMuted,
  );
}

class LoopRadius {
  static const Radius sm = Radius.circular(11);
  static const Radius md = Radius.circular(12);
}

class LoopSpacing {
  static const double s1 = 4;
  static const double s2 = 8;
  static const double s3 = 10;
  static const double s4 = 16;
  static const double s5 = 28;
  static const double s6 = 36;
  static const double gutterWidth = 153;
  static const double ruleGap = 16;
}

ThemeData buildAppTheme() {
  final ColorScheme scheme = const ColorScheme(
    brightness: Brightness.light,
    primary: LoopColors.action,
    onPrimary: LoopColors.snow,
    secondary: LoopColors.action,
    onSecondary: LoopColors.snow,
    error: LoopColors.error,
    onError: LoopColors.onActionAlt,
    surface: LoopColors.paper,
    onSurface: LoopColors.ink,
  );

  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: LoopColors.paper,
    fontFamily: LoopType.family,
    textTheme: const TextTheme(
      displayLarge: LoopType.display,
      headlineLarge: LoopType.dialogue,
      titleLarge: LoopType.speaker,
      bodyLarge: LoopType.dialogue,
      bodyMedium: LoopType.ui,
      labelMedium: LoopType.caption,
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: LoopColors.paperInput,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.all(LoopRadius.sm),
        borderSide: const BorderSide(color: LoopColors.inkBlack, width: 1),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.all(LoopRadius.sm),
        borderSide: const BorderSide(color: LoopColors.inkBlack, width: 1),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.all(LoopRadius.sm),
        borderSide: const BorderSide(color: LoopColors.action, width: 2),
      ),
      hintStyle: LoopType.ui.copyWith(color: LoopColors.inkMuted),
    ),
    snackBarTheme: const SnackBarThemeData(behavior: SnackBarBehavior.floating),
  );
}
