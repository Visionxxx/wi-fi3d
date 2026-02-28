# Test Routes (Ground Truth Collection)

## Route R1 - Baseline Loop
1. Start in LivingRoom for 30s (stationary).
2. Walk to Hallway in 10-15s.
3. Stay in Hallway for 30s.
4. Walk to Office in 10-15s.
5. Stay in Office for 30s.
6. Return to LivingRoom and hold for 30s.

## Route R2 - Floor Transition
1. Start on Floor 1 (LivingRoom) for 30s.
2. Move to stairs and transition to Floor 2 in 20-30s.
3. Stay on Floor 2 for 45s.
4. Return to Floor 1 and hold for 30s.

## Route R3 - Change Detection
1. Run baseline for 60s.
2. Turn off one known AP for 30s.
3. Turn AP back on and wait 60s.
4. Optionally move one AP and repeat 60s.

## Notes
- Keep timestamps for route boundaries in the run log.
- Use same observer speed in repeated runs.
- Capture at least 3 full runs per route before acceptance.
