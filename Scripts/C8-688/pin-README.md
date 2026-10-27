# C8-688 source selection

`source-pin.txt` supports:

- `auto`, `master`, `main`, `HEAD` or empty: follow the workflow branch.
- A complete 40-character commit SHA: fetch and check out that revision before
  feeds, source patches and cache identity are prepared. Fetch failure stops the build.

The old automatic removal of the firewall4 fullcone patch is retired. ImmortalWrt
fixed the upstream patch in commit 21ae803cc274b52f98f99f0f75864b114b11daab on
2026-10-27. Both auto and pinned modes preserve upstream patches; a future patch
failure is reported rather than silently disabling a feature.
