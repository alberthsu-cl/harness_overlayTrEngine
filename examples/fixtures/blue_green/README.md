# Blue/Green Fixture Pair

This fixture set is the default synthetic A/B example for the harness.

- `source_a/`: blue frames
- `source_b/`: green frames

Generate or refresh it with:

```powershell
py -3 harness/src/main.py prepare-pair --output-root harness/examples/fixtures/blue_green --color-a blue --color-b green --width 1920 --height 1080 --frame-count 30
```