#include "buzzer.h"
#include "settings.h"
#include "config.h"
#include <time.h>

// The mono build drives a plain passive buzzer from one GPIO. There is no I2S
// codec, amplifier or IO-expander backend, so the tone output lives here.

void sanitizeBuzzerPin() {
  // Pin 0 = disabled (no buzzer). Pin 255 is the legacy "disabled" value and
  // must also clamp to 0.
  if (buzzerSettings.pin == 0 || buzzerSettings.pin == 255) {
    buzzerSettings.pin = 0;
    return;
  }

  // ESP32-C3 SuperMini reserved pins: GPIO2 is a strapping pin, 8/9 carry the
  // OLED I2C bus, 11-17 are the SPI flash, 18/19 are USB CDC and 20/21 are
  // UART0. Driving any of them from the tone output can break boot, the
  // display bus or the serial console. A stale NVS value from another board
  // can still land here, so this is not just a UI-level check.
  const uint8_t p = buzzerSettings.pin;
  if (p == 2 || p == 8 || p == 9 || (p >= 11 && p <= 21)) {
    Serial.printf("Buzzer: pin %d reserved on the C3, disabling\n", p);
    buzzerSettings.pin = 0;
    return;
  }
#if defined(BACKLIGHT_PIN) && BACKLIGHT_PIN >= 0
  if (p == BACKLIGHT_PIN) {
    Serial.printf("Buzzer: pin %d conflicts with backlight, disabling\n", p);
    buzzerSettings.pin = 0;
    return;
  }
#endif
  if (buttonType != BTN_DISABLED && buttonPin != 0 && p == buttonPin) {
    Serial.printf("Buzzer: pin %d conflicts with the button, disabling\n", p);
    buzzerSettings.pin = 0;
  }
}

static void buzzerApplyStep(uint16_t freq) {
  if (buzzerSettings.pin == 0) return;
  if (freq > 0) {
    tone(buzzerSettings.pin, freq);
  } else {
    noTone(buzzerSettings.pin);
    digitalWrite(buzzerSettings.pin, LOW);
  }
}

static void buzzerStop() {
  sanitizeBuzzerPin();
  if (buzzerSettings.pin == 0) return;
  noTone(buzzerSettings.pin);
  digitalWrite(buzzerSettings.pin, LOW);
}

// ---------------------------------------------------------------------------
//  Tone patterns (frequency Hz, duration ms) - 0 freq = pause
// ---------------------------------------------------------------------------
struct ToneStep { uint16_t freq; uint16_t ms; };

static const ToneStep melodyFinished[] = {
  {1047, 120}, {0, 40},   // C6
  {1319, 120}, {0, 40},   // E6
  {1568, 120}, {0, 40},   // G6
  {2093, 250},             // C7
};

static const ToneStep melodyError[] = {
  {880, 100}, {0, 80},
  {880, 100}, {0, 80},
  {880, 100},
};

static const ToneStep melodyConnected[] = {
  {1047, 80}, {0, 40},
  {1568, 120},
};

static const ToneStep melodyClick[] = {
  {4000, 8},
};

// Descending tones - bed cooled, second-stage alert (softer than finished)
static const ToneStep melodyBedCooldown[] = {
  {784, 150}, {0, 50},   // G5
  {659, 150}, {0, 50},   // E5
  {523, 200},            // C5
};

// ---------------------------------------------------------------------------
//  Non-blocking playback state
// ---------------------------------------------------------------------------
static const ToneStep* currentMelody = nullptr;
static uint8_t melodyLen = 0;
static uint8_t melodyIdx = 0;
static unsigned long stepStartMs = 0;
static bool playing = false;

void initBuzzer() {
  playing = false;
  currentMelody = nullptr;
  melodyIdx = 0;
  melodyLen = 0;
  sanitizeBuzzerPin();
  if (!buzzerSettings.enabled || buzzerSettings.pin == 0) {
    buzzerStop();
    return;
  }
  pinMode(buzzerSettings.pin, OUTPUT);
  digitalWrite(buzzerSettings.pin, LOW);
  buzzerStop();
}

bool buzzerIsPlaying() {
  return playing;
}

bool buzzerIsQuietHour() {
  uint8_t qs = buzzerSettings.quietStartHour;
  uint8_t qe = buzzerSettings.quietEndHour;
  if (qs == qe) return false;

  struct tm now;
  if (!getLocalTime(&now, 0)) return false;
  uint8_t h = now.tm_hour;

  if (qs < qe) return h >= qs && h < qe;
  else          return h >= qs || h < qe;
}

void buzzerPlay(BuzzerEvent event) {
  if (!buzzerSettings.enabled) return;
  if (buzzerIsQuietHour()) return;
  if (playing) return;

  switch (event) {
    case BUZZ_PRINT_FINISHED:
      currentMelody = melodyFinished;
      melodyLen = sizeof(melodyFinished) / sizeof(ToneStep);
      break;
    case BUZZ_ERROR:
      currentMelody = melodyError;
      melodyLen = sizeof(melodyError) / sizeof(ToneStep);
      break;
    case BUZZ_CONNECTED:
      currentMelody = melodyConnected;
      melodyLen = sizeof(melodyConnected) / sizeof(ToneStep);
      break;
    case BUZZ_CLICK:
      currentMelody = melodyClick;
      melodyLen = sizeof(melodyClick) / sizeof(ToneStep);
      break;
    case BUZZ_BED_COOLDOWN:
      currentMelody = melodyBedCooldown;
      melodyLen = sizeof(melodyBedCooldown) / sizeof(ToneStep);
      break;
    default: return;
  }

  melodyIdx = 0;
  playing = true;
  buzzerApplyStep(currentMelody[0].freq);
  stepStartMs = millis();
}

void buzzerPlayClick() {
  if (!buzzerSettings.enabled) return;
  if (!buzzerSettings.buttonClick) return;

  bool wasPlaying = playing;
  buzzerApplyStep(melodyClick[0].freq);
  unsigned long t = millis();
  while (millis() - t < melodyClick[0].ms) {
    delay(1);
  }
  buzzerStop();
  if (wasPlaying && currentMelody && melodyIdx < melodyLen) {
    stepStartMs = millis();
    buzzerApplyStep(currentMelody[melodyIdx].freq);
  }
}

void buzzerTick() {
  if (!playing || !currentMelody) return;

  if (millis() - stepStartMs < currentMelody[melodyIdx].ms) return;

  melodyIdx++;
  if (melodyIdx >= melodyLen) {
    playing = false;
    currentMelody = nullptr;
    buzzerStop();
    return;
  }

  buzzerApplyStep(currentMelody[melodyIdx].freq);
  stepStartMs = millis();
}
