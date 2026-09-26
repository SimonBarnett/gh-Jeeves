# FR #175 / K1: chair handles !bored (blocks gate)

**Issue:** https://github.com/SimonBarnett/gh-Jeeves/issues/175

## Outcome (SoT)

**Resolved by FR #106:** Jeeves owns worker `!bored` in `#{machine}` and posts
**one assign line**. Ear OFFER / git-claim / ASSIGN multi-wake paths stay forbidden.

Tests: `tests/test_k1_fr175_chair_bored.py` lock `chair_handles_bored()`, assign
egress, and static scan for legacy chair claim handlers.
