#ifndef DISPLAY_CARD_H
#define DISPLAY_CARD_H

#include <stdint.h>

// The colour "Card" print-screen style is not built on mono. These inline
// no-ops keep the gesture code in main.cpp free of board-specific branches.
struct BambuState;

inline bool cardSkinActive() { return false; }
inline bool cardAmsPeekToggle(const BambuState&) { return false; }

#endif // DISPLAY_CARD_H
