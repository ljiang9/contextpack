"""Enables `python -m contextpack`."""
if __package__:
    from .contextpack import main
else:
    from contextpack import main

if __name__ == "__main__":
    raise SystemExit(main())
