// ===========================================================================
//  display_mono.cpp - colour-display API for the mono build
// ---------------------------------------------------------------------------
//  The SH1106 renderer lives in mono_display.cpp. The colour display layer
//  (LovyanGFX draw graph, gauges, cards, clocks, edge glow) is not compiled
//  into the mono env, so this file provides the small slice of that API the
//  core still calls: screen state, brightness bookkeeping and no-op hooks.
// ===========================================================================
#include <Arduino.h>
#include <time.h>

#include "display_ui.h"
#include "display_edge_glow.h"
#include "settings.h"

#ifdef BOARD_IS_MONO

static ScreenState currentScreen = SCREEN_SPLASH;

// --- Panel lifecycle -------------------------------------------------------
void initDisplay() {}
void updateDisplay() {}
void flushFrame() {}

// --- Screen state ----------------------------------------------------------
void setScreenState(ScreenState state) { currentScreen = state; }
ScreenState getScreenState() { return currentScreen; }

// --- Brightness / night mode ----------------------------------------------
// The SH1106 has no backlight; contrast is handled in mono_display.cpp. The
// night window still gates the status LED, so isNightModeActive() keeps the
// same wall-clock logic as the colour display layer.
void setBacklight(uint8_t) {}

uint8_t getEffectiveBrightness() { return brightness; }

static bool isNightHour() {
  struct tm now;
  time_t t = time(nullptr);
  localtime_r(&t, &now);
  if (now.tm_year < (2020 - 1900)) return false;  // NTP not synced yet

  const uint8_t h = now.tm_hour;
  const uint8_t s = dpSettings.nightStartHour;
  const uint8_t e = dpSettings.nightEndHour;

  if (s == e) return false;             // same hour = disabled
  if (s < e) return (h >= s && h < e);  // e.g. 01:00-07:00
  return (h >= s || h < e);             // e.g. 22:00-07:00 (wraps midnight)
}

bool isNightModeActive() { return dpSettings.nightModeEnabled && isNightHour(); }
bool isNightBlackout() { return false; }  // no backlight to black out
void nightWake() {}
void checkNightMode() {}

// --- Settings / transitions ------------------------------------------------
void applyDisplaySettings() {}

void triggerDisplayTransition() {}

// --- Layout capabilities ---------------------------------------------------
bool displaySupportsSplit() { return false; }
bool cardOnScreen() { return false; }

// --- AMS drying screen -----------------------------------------------------
uint8_t dryingUnitCount(const AmsState&) { return 0; }
void resetDryingRotation() {}

// --- Printer error detail --------------------------------------------------
#if HAS_HMS_UI
bool hmsScreenAvailable() { return false; }
bool hmsScreenAlerting() { return false; }
#endif

// --- Edge glow -------------------------------------------------------------
void glowNotifyEvent(uint8_t, GlowEvent, uint16_t) {}
void glowClearSlot(uint8_t) {}
void glowClearError(uint8_t) {}
void glowDismiss() {}
bool glowIsArmed() { return false; }
void glowStartTest(uint8_t) {}

// --- Clock screensavers ----------------------------------------------------
void resetClock() {}
void resetPongClock() {}

#endif  // BOARD_IS_MONO
