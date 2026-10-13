# Pay-by-SMS billing

Customers pay mobile money to the SIM in the router. The confirmation SMS
lands in the router's inbox; `airtel billing run` reads it, matches the payer's
phone to their registered device MAC(s), and allows those MACs until the paid
time runs out.

## Setup

```
airtel billing plan add 1h 500 60          # name, price, minutes
airtel billing plan add 1d 2000 1440
airtel billing customer 0712345678 AA:BB:CC:DD:EE:FF --name John
airtel billing parse "<paste a real payment SMS>"   # check the parser reads it
airtel billing run --dry-run               # real inbox, no router changes
airtel billing payments                    # audit trail
```

Rules enforced: only SMS from trusted sender ids count, each transaction id is
honoured once, and the amount must exactly match a plan. A payment from an
unregistered phone is held and applied when that phone registers.

## What is NOT done yet (needs one real capture each)

1. **SMS inbox shape.** `airtel probe 12` prints the raw `SMS_INFO` reply.
   `billing.extract_messages` guesses the field names; fix it to match.
2. **MAC allow/revoke.** `RouterBackend.allow/revoke` raise until the real
   `MAC_FILTER` (cmd 23) / `MAC_DEFAULT_FILTER` (cmd 30) POST is captured.
   In the router UI (Firewall → MAC Filter) open dev tools → Network → XHR,
   add one MAC, and copy the request body. Then implement the two methods.
   Put the owner's own devices on the list first or you lock yourself out.
3. **Parser patterns.** `AMOUNT_RE` / `TXN_RE` are loose guesses; confirm
   against your provider's actual SMS text and the real sender id.

Phones with randomized Wi-Fi MACs must switch it off for this network
(see `mac-whitelist.md`), or the registered MAC won't match.
