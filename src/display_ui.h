#ifndef DISPLAY_UI_H
#define DISPLAY_UI_H

#include <stdint.h>

#include "config.h"   // HAS_HMS_UI

// Screen state machine shared by the mono renderer and the core. The colour
// display layer that used to implement most of it is no longer part of the
// build; display_mono.cpp provides the API the core still calls.
enum ScreenState {
  SCREEN_SPLASH,
  SCREEN_AP_MODE,
  SCREEN_CONNECTING_WIFI,
  SCREEN_WIFI_CONNECTED,
  SCREEN_CONNECTING_MQTT,
  SCREEN_IDLE,
  SCREEN_PRINTING,
  SCREEN_FINISHED,
  SCREEN_CLOCK,
  SCREEN_OFF,
  SCREEN_OTA_UPDATE,
  SCREEN_SPLIT,         // reserved: split view is not available on mono
  SCREEN_POWER_CONFIRM, // fullscreen plug on/off confirmation (#136); hold to confirm
  SCREEN_DRY_PEEK,      // AMS drying view tapped up during a print (#150); auto-closes
  SCREEN_HMS            // printer error detail; tap to open while one is active
};

// How long a tapped-up error screen stays before it drops back on its own.
static const uint32_t HMS_SCREEN_MS = 30000;

// Auto-present window for "show briefly, then go back".
static const uint32_t HMS_AUTO_PRESENT_MS = 10000;

#if HAS_HMS_UI
bool hmsScreenAvailable();
bool hmsScreenAlerting();
#else
inline bool hmsScreenAvailable() { return false; }
inline bool hmsScreenAlerting() { return false; }
#endif

// Read-only snapshot of the plug power-confirm modal, filled by main.cpp so the
// renderer never reaches into main's statics. phase mirrors the
// PowerConfirmPhase enum in main.cpp: 0=wait-release, 1=armed, 2=sending, 3=result.
struct PowerConfirmView {
  const char* name;    // target printer name
  bool  desiredOn;     // true = turning plug ON, false = OFF
  bool  warn;          // printer is currently printing -> red screen
  bool  offline;       // plug reading is stale/offline (live)
  float progress;      // 0..1 hold-to-confirm ring fill
  int   phase;         // see above
  bool  resultOk;      // valid in phase 3: command succeeded
};
// Returns false when the modal is not active. Defined in main.cpp.
bool powerConfirmGetView(PowerConfirmView* out);
// Called by the renderer once it has drawn the "Sending..." frame, so main.cpp
// only fires the (blocking) relay command after that frame is on screen.
void powerConfirmMarkSendingDrawn();

struct AmsState;

// --- AMS drying screen: unit rotation ---------------------------------------
// The drying screen shows one unit at a time. main.cpp sizes the peek window
// from the unit count and restarts the rotation when it opens a peek.
static const uint32_t DRY_PEEK_DWELL_MS = 5000;

// Number of AMS units actively drying (dryRemainMin > 0).
uint8_t dryingUnitCount(const AmsState& ams);
// Restart the rotation at the first drying unit.
void resetDryingRotation();

void initDisplay();
void updateDisplay();

// Commit a pending frame to the panel. No-op on mono (draws go straight to the
// OLED); kept for the loop's once-per-tick call.
void flushFrame();

void setScreenState(ScreenState state);
ScreenState getScreenState();
void setBacklight(uint8_t level);
void applyDisplaySettings();  // re-apply display settings
void triggerDisplayTransition();
void checkNightMode();        // apply scheduled brightness dimming
uint8_t getEffectiveBrightness(); // current brightness (night or normal)
bool isNightModeActive();     // night mode enabled AND inside the night window
bool isNightBlackout();       // night window with brightness 0, no wake override running
void nightWake();             // button press during a blackout: light the panel briefly

// Mono never supports the split (dual-printer) screen.
bool displaySupportsSplit();

// The last drawn frame was a Cards screen. Always false on mono.
bool cardOnScreen();

// Clock screensaver state reset (the clocks themselves are not built on mono).
void resetClock();
void resetPongClock();

#endif // DISPLAY_UI_H
