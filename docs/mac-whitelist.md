# Wi-Fi MAC whitelist (Allow List)

The X25 supports MAC-based access control in Allow-list mode: only MACs you
add can associate, even with the correct Wi-Fi password.

## Steps

1. Log in at `http://192.168.1.1` (see `.env` for credentials).
2. Go to **Wi-Fi Settings / Security / Advanced → MAC Filter** (naming
   varies by firmware build; on this unit it's under **Firewall → MAC Filter**
   in the menu map reverse-engineered in `router_client.py`'s docstring —
   cmd `MAC_FILTER` / `MAC_DEFAULT_FILTER`).
3. Switch mode from **Blacklist (Deny)** to **Whitelist (Allow)**.
4. **Add the device you're configuring from FIRST**, before enabling the
   filter — otherwise you lock yourself out immediately.
5. Add each other device's MAC (format `AA:BB:CC:11:22:33`), then save/apply.

## Before adding a phone/tablet: turn off MAC randomization

Modern phones rotate their Wi-Fi MAC per network by default, which breaks a
whitelist unless you pin the real hardware MAC for this network:

- **iPhone/iPad:** Settings → Wi-Fi → (i) next to the network → turn off
  **Private Wi-Fi Address** (set to Off/Fixed).
- **Android:** Settings → Network & Internet → Wi-Fi → network gear icon →
  Privacy → **Use device MAC** instead of randomized MAC.

## Why this isn't scripted

Unlike reconnect/reboot/status, the ACL endpoints weren't fully mapped this
session (field names/payload shape for `MAC_FILTER` aren't confirmed). Doing
this via the API blind risks locking out the whitelist owner's own device.
Do it once through the web UI; if it's worth automating later, capture the
exact request via the browser's dev tools (Network tab → the `MAC_FILTER`
POST) and add a `set_mac_whitelist()` method to `router_client.py` following
the same pattern as `reconnect()`/`reboot()`.
