## Summary

<!-- What changed and why? Keep this short and reviewer-focused. -->

## Related Issues

<!-- Link issues, e.g. Closes #123. Use N/A if none. -->

## Change Type

- [ ] Bug fix
- [ ] Feature
- [ ] Refactor
- [ ] Documentation
- [ ] Tests
- [ ] Build/CI
- [ ] Security/privacy

## Testing

<!-- List the checks you ran, e.g. make lint, make typecheck, targeted pytest, npm test, make docker-test. -->

## Screenshots / Recordings

<!-- Required for visible UI changes. Use N/A for backend-only work. -->

## Migration / Config Impact

- [ ] No database migration required
- [ ] Database migration included
- [ ] No new configuration or environment variables
- [ ] New configuration documented
- [ ] Upgrade/rollback behavior considered

## Security / Privacy Impact

- [ ] No security or privacy impact
- [ ] Handles secrets or credentials
- [ ] Changes authentication, authorization, network access, or file access
- [ ] Changes what data may leave the user's machine

## Settings Hygiene (If Applicable)

- [ ] Any new tunable value (timeout, batch size, retry count, page size, interval, threshold) lives in `chaoscypher_core.app_config` or an equivalent settings surface.
- [ ] Any new security/protocol constant lives in `chaoscypher_core.policy`.
- [ ] Pydantic `Field(max_length=N)` on user-input fields uses `policy.X` or `max_length_from_settings("path")`.
- [ ] If I added a `# nosemgrep: cc-046/047/048` suppression, the comment includes a real one-line reason.
- [ ] If this PR adds a frontend tunable, it flows through `PublicSettings` -> `useAppConfig()`.

## Public Readiness

- [ ] No secrets, local data, generated caches, or benchmark outputs committed.
- [ ] Public docs/examples remain runnable from this repository.
- [ ] User-facing behavior changes are documented.
- [ ] New behavior is covered by tests or the PR explains why not.
- [ ] CLA requirement is satisfied or maintainer follow-up is expected.
