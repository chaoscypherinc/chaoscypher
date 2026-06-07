# Fixture for cc-015-bare-type-ignore.
# Rule bans bare mypy suppressions (without a rule code).

# ruleid: cc-015-bare-type-ignore
x: int = "wrong"  # type: ignore

# ok: cc-015-bare-type-ignore
y: int = "wrong"  # type: ignore[assignment]
