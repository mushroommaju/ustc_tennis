# USTC Tennis Assistant

Experimental tennis booking assistant with strict local configuration, availability preview,
single-attempt submission, persistent duplicate prevention and order verification.

## Requirements

Python 3.10+, Node.js 22+ and the separate WMPFDebugger project.
The debugger is not bundled: https://github.com/evi0s/WMPFDebugger
Respect its upstream license. The local adapter expects it under
`work/runtime-validation/WMPFDebugger/`, with its Node dependencies installed.
The validated runtime version was WMPF 25510; compatibility is version dependent.

This is not a ready-to-run public booking service. You must supply your own authorized
account.local.json and signing.local.json (a JSON object containing a suffix string),
and keep the authenticated target mini-program/runtime available. No account credentials,
captures, private signing values, mini-program packages or real booking records are included.
The mini-program AppID and service hostname in the source are public target identifiers,
not authentication credentials. Read upstream injection requirements before running the debugger.
The tested local debugger bound its servers to 127.0.0.1; do not expose debugging ports remotely.

## Configuration

Copy config.example.json to config.json and account.example.json to account.local.json.
Replace the example account and companion IDs with your own. Set date/start/end/courts.
Courts are displayed court numbers, ordered by preference; use 15-minute boundaries and
30-90 minute durations. Execution checks the actual booking window and server availability.

```
python booking.py check-config --config config.json
python booking.py preview --config config.json
```

Only `python booking.py submit --config config.json` creates a real reservation.
Pending companion confirmation is a temporary hold, not permanent confirmation.
Uncertain outcomes are not automatically retried. Local slot claims remain after cancellation;
do not delete the ledger to retry an unverified result.

`scheduler.py --at YYYY-MM-DDTHH:MM:SS+08:00 --config config.json` is a one-shot foreground
scheduler, dry-run by default. Only adding --submit enables an actual booking attempt.
There is no background service, automatic login recovery or guarantee of availability.

## Offline tests

```
python -m unittest discover -s tests -v
node --check runtime_bridge.cjs
```

Tests use synthetic fixtures and must not submit real reservations.
