# Semgrep rule fixtures (CC0xx self-tests)

This directory pairs every `cc-NNN-*.yml` rule under
`tools/semgrep/rules/` with a `cc-NNN-*.py` fixture exercising
both a positive (bad) case and at least one negative (ok) case.

Annotation grammar (from `semgrep test`):

- `# ruleid: <rule-id>` on the line **before** code that **should** match.
- `# ok: <rule-id>` on the line **before** code that **should not** match.

Runner: `packages/core/tests/unit/scripts/test_semgrep_rule_selftests.py`
walks every `cc-NNN-*.yml` and invokes `semgrep test --config <rule.yml>
<fixture.py>`. Any rule that doesn't fire on the bad input fails the
suite — the same regression class that silently broke CC044 in May 2026.

Conventions:

- Fixture files use plain `.py` extension so semgrep's Python parser
  loads them with no special config.
- Keep fixtures minimal: one positive + one or two negatives is plenty.
- Path-based filters (`paths.include` / `paths.exclude`) in the YAML are
  **ignored** by `semgrep test` — fixtures here can live anywhere on
  disk and the rule logic is exercised purely on the pattern.
- The production semgrep sweep in `make lint-claude` scans only
  `packages/`, so fixtures here never appear in the production
  violation list.
