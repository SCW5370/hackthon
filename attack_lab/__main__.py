"""Allow: python -m attack_lab <command>."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
