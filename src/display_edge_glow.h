#ifndef DISPLAY_EDGE_GLOW_H
#define DISPLAY_EDGE_GLOW_H

#include <stdint.h>

// Edge glow: animated border light announcing print complete, print failed, or
// a printer error. The colour renderer is not built into the mono image;
// display_mono.cpp keeps the API as no-ops so the core's event paths link.
enum GlowEvent : uint8_t {
  GLOW_EV_FINISH = 0,
  GLOW_EV_FAILED = 1,
  GLOW_EV_ERROR  = 2,
};

// Latch an event for a printer slot. No-op on mono.
void glowNotifyEvent(uint8_t slot, GlowEvent ev, uint16_t color = 0);

// Drop a slot's latch.
void glowClearSlot(uint8_t slot);

// Drop a slot's ERROR latch / episode only.
void glowClearError(uint8_t slot);

// Stop the animation and clear the on-screen slot's latch.
void glowDismiss();

// True while an episode is armed at all.
bool glowIsArmed();

// Web UI "Test" button: run one short preview episode.
void glowStartTest(uint8_t slot);

#endif // DISPLAY_EDGE_GLOW_H
