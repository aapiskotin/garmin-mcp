from __future__ import annotations

import getpass
import os
from pathlib import Path

from garminconnect import Garmin


def main() -> None:
    """Interactive one-time login; stores OAuth tokens but never the password."""
    token_dir = Path(os.getenv("GARMIN_TOKEN_DIR", ".garmin-tokens")).expanduser().resolve()
    token_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    token_dir.chmod(0o700)

    email = os.getenv("GARMIN_EMAIL") or input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password (not stored): ")
    if not email or not password:
        raise SystemExit("Email and password are required.")

    client = Garmin(
        email,
        password,
        prompt_mfa=lambda: input("Garmin MFA code: ").strip(),
    )
    client.login(str(token_dir))

    token_file = token_dir / "garmin_tokens.json"
    if token_file.exists():
        token_file.chmod(0o600)
    print(f"Garmin login succeeded. Tokens saved to {token_dir}")


if __name__ == "__main__":
    main()
