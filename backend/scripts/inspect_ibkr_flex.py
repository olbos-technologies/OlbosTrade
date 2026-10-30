"""Inspect a private Activity Flex XML file offline; print only a safe summary.

Never connects to IBKR, accepts tokens, writes a database, or grants authority.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Direct script invocation from any directory; no backend server/config import.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.ibkr_flex_intake import MAX_BYTES, FlexIntakeError, parse_activity_flex_xml


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error can echo arbitrary supplied arguments.
        self.exit(2, json.dumps({"state": "rejected", "reason": "invalid_arguments", "verified": False}) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description=__doc__)
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--expected-account", required=True)
    parser.add_argument("--organization", required=True, help="Declared staging organization UUID; not authentication")
    parser.add_argument("--timezone", required=True, help="Confirmed report IANA timezone, e.g. America/New_York")
    args = parser.parse_args(argv)
    try:
        # Bound the read too, not merely the later parse. No report is copied.
        with args.file.open("rb") as report:
            payload = report.read(MAX_BYTES + 1)
        result = parse_activity_flex_xml(payload, expected_account=args.expected_account,
                                         organization_id=args.organization, source_timezone=args.timezone)
    except OSError:
        print(json.dumps({"state": "rejected", "reason": "report_unreadable", "verified": False}))
        return 2
    except FlexIntakeError as exc:
        print(json.dumps({"state": "rejected", "reason": exc.code, "row": exc.row, "verified": False}))
        return 2
    print(json.dumps(result.summary(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
