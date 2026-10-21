#include "button.h"
#include "settings.h"
#include "buzzer.h"

// The mono build only knows GPIO buttons (push-to-ground or TTP223): the
// OLED has no touch controller, so there is no touch backend to select.
// This file owns pin sanitizing and the shared debounce/hold state.

static bool lastRaw = false;
static bool stableState = false;
static unsigned long lastChangeMs = 0;
static unsigned long pressStartMs = 0;
static const unsigned long DEBOUNCE_MS = 50;

void sanitizeButtonPin() {
  // Only the GPIO-backed button types use buttonPin.
  if (buttonType != BTN_PUSH && buttonType != BTN_TOUCH) return;
  if (buttonPin == 0) return;

  auto clash = [&](const char* what) {
    Serial.printf("Button: pin %u conflicts with %s, disabling\n",
                  (unsigned)buttonPin, what);
    buttonPin = 0;
  };

  // ESP32-C3 SuperMini reserved pins: GPIO2 is a strapping pin, 8/9 carry the
  // OLED I2C bus (and are strapping pins too), 11-17 are the SPI flash, 18/19
  // are USB CDC and 20/21 are UART0. Driving any of them from a button would
  // break boot, the display bus or the serial console.
  if (buttonPin == 2 || buttonPin == 8 || buttonPin == 9 ||
      (buttonPin >= 11 && buttonPin <= 21)) {
    clash("a reserved C3 pin");
    return;
  }
#if defined(BACKLIGHT_PIN) && BACKLIGHT_PIN >= 0
  if (buttonPin == BACKLIGHT_PIN) { clash("backlight"); return; }
#endif
  if (buzzerSettings.pin != 0 && buttonPin == buzzerSettings.pin) {
    clash("buzzer");
    return;
  }
}

void initButton() {
  if (buttonType == BTN_DISABLED) return;
  sanitizeButtonPin();
  if (buttonPin == 0) return;
  if (buttonType == BTN_PUSH) {
    pinMode(buttonPin, INPUT_PULLUP);
  } else {  // BTN_TOUCH (TTP223)
    pinMode(buttonPin, INPUT);
  }
  lastRaw = false;
  stableState = false;
  lastChangeMs = 0;
  pressStartMs = 0;
}

bool wasButtonPressed() {
  if (buttonType == BTN_DISABLED) return false;

  bool raw;
  if (buttonType == BTN_PUSH) {
    if (buttonPin == 0) return false;
    raw = (digitalRead(buttonPin) == LOW);   // active LOW with pull-up
  } else {
    if (buttonPin == 0) return false;
    raw = (digitalRead(buttonPin) == HIGH);  // TTP223: active HIGH
  }

  // Debounce
  if (raw != lastRaw) {
    lastChangeMs = millis();
    lastRaw = raw;
  }
  if ((millis() - lastChangeMs) < DEBOUNCE_MS) return false;

  // Rising edge detection
  bool result = false;
  if (raw && !stableState) {
    result = true;
    pressStartMs = millis();
  } else if (!raw && stableState) {
    pressStartMs = 0;
  }
  stableState = raw;

  return result;
}

bool isButtonHeld() {
  return stableState;
}

uint32_t buttonHoldDurationMs() {
  if (!stableState || pressStartMs == 0) return 0;
  return (uint32_t)(millis() - pressStartMs);
}
