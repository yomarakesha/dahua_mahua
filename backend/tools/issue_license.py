#!/usr/bin/env python3
"""Offline license issuer — VENDOR-ONLY tool (needs the private key).

Usage
-----
One-time: generate the vendor keypair (do this ONCE, keep the private key safe)::

    python backend/tools/issue_license.py genkey --out-dir ./vendor-keys
    # -> vendor-keys/private_key.pem  (KEEP OFFLINE / in a secret manager)
    # -> vendor-keys/public_key.pem   (ship: copy to backend/licensing/public_key.pem)

Per customer: sign a license bound to the machine fingerprint they sent you::

    python backend/tools/issue_license.py issue \\
        --private-key ./vendor-keys/private_key.pem \\
        --fingerprint a1b2c3... \\
        --customer "ACME LLC" --site-id acme-01 \\
        --expires 2027-07-07 \\
        --max-cameras 64 --max-nvrs 8 \\
        --feature playback --feature hikvision \\
        --out acme-01.lic

Omit --expires (or pass --expires "") for a perpetual license.
The private key may also come from the LICENSE_PRIVATE_KEY env var (PEM contents).
This tool does NOT touch the network.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

# Make `import app` work when run from the repo root or elsewhere.
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app import licensing  # noqa: E402


def _load_private_key(args: argparse.Namespace) -> "licensing.Ed25519PrivateKey":
    pem: bytes | str | None = None
    if args.private_key:
        pem = Path(args.private_key).read_bytes()
    elif os.environ.get("LICENSE_PRIVATE_KEY"):
        pem = os.environ["LICENSE_PRIVATE_KEY"]
    else:
        print(
            "error: no private key (pass --private-key PATH or set LICENSE_PRIVATE_KEY)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return licensing.load_private_key(pem)


def cmd_genkey(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    priv, pub = licensing.generate_keypair()
    priv_path = out_dir / "private_key.pem"
    pub_path = out_dir / "public_key.pem"
    priv_path.write_bytes(licensing.private_key_pem(priv))
    try:
        os.chmod(priv_path, 0o600)
    except OSError:
        pass
    pub_path.write_bytes(licensing.public_key_pem(pub))
    print(f"private key -> {priv_path}  (KEEP OFFLINE — never commit)")
    print(f"public  key -> {pub_path}   (ship: backend/licensing/public_key.pem)")
    return 0


def cmd_issue(args: argparse.Namespace) -> int:
    priv = _load_private_key(args)

    expires = args.expires
    if expires == "":
        expires = None  # explicit perpetual

    fields = {
        "customer": args.customer,
        "site_id": args.site_id,
        "issued": args.issued or date.today().isoformat(),
        "expires": expires,
        "features": list(args.feature or []),
        "max_cameras": args.max_cameras,
        "max_nvrs": args.max_nvrs,
        "hardware_id": args.fingerprint,
    }
    signed = licensing.sign_license(fields, priv)

    import json

    blob = json.dumps(signed, indent=2, sort_keys=True)

    # Sanity check: verify what we just signed against the matching public key.
    status = licensing.verify_license(blob, pubkey=licensing.public_key_pem(priv.public_key()), fingerprint=args.fingerprint)
    if not status.valid:
        print(f"error: self-verification failed: {status.reason}", file=sys.stderr)
        return 1

    if args.out:
        Path(args.out).write_text(blob + "\n", encoding="utf-8")
        print(f"license -> {args.out}  (customer={args.customer}, expires={expires or 'perpetual'})")
    else:
        print(blob)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Offline Ed25519 license issuer (vendor tool).")
    sub = p.add_subparsers(dest="command", required=True)

    g = sub.add_parser("genkey", help="generate the vendor keypair (run once)")
    g.add_argument("--out-dir", default="./vendor-keys")
    g.set_defaults(func=cmd_genkey)

    i = sub.add_parser("issue", help="sign a license for a customer")
    i.add_argument("--private-key", help="path to private_key.pem (or set LICENSE_PRIVATE_KEY)")
    i.add_argument("--fingerprint", required=True, help="machine fingerprint from the customer")
    i.add_argument("--customer", required=True)
    i.add_argument("--site-id", default="")
    i.add_argument("--issued", help="ISO date; defaults to today")
    i.add_argument("--expires", default=None, help="ISO date; omit or '' for perpetual")
    i.add_argument("--max-cameras", type=int, default=0)
    i.add_argument("--max-nvrs", type=int, default=0)
    i.add_argument("--feature", action="append", help="repeatable, e.g. --feature playback")
    i.add_argument("--out", help="write to this .lic path (else print to stdout)")
    i.set_defaults(func=cmd_issue)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
