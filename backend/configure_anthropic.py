"""Secure local key entry: python -m backend.configure_anthropic."""

from getpass import getpass
from pathlib import Path

from dotenv import set_key


def main():
    key = getpass("Anthropic API key (hidden): ").strip()
    if not key.startswith("sk-ant-") or any(character.isspace() for character in key):
        raise SystemExit("No key saved: enter an Anthropic API key beginning sk-ant-, without whitespace.")
    path = Path(__file__).with_name(".env")
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    set_key(str(path), "ANTHROPIC_API_KEY", key)
    print("Anthropic key saved locally. Restart the backend; access is not yet verified.")


if __name__ == "__main__":
    main()
