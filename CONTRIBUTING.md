# Contributing

Thanks for contributing to Data Download Hub.

### Contributor License Agreement (CLA)

By submitting a contribution to this repository, you certify that:

1. **You have the right to submit the contribution.**
   You created the content yourself, or you have the right to submit it under the project's license.

2. **You grant us a license to use your contribution.**
   Your contribution will be licensed under the same terms as the rest of this project, and you grant the project maintainers the right to use, modify, and distribute it as part of the project.

3. **You are not submitting confidential or proprietary information.**
   Your contribution does not include anything you don't have permission to share publicly.

If you are contributing on behalf of an organization, you confirm that you have the authority to do so. You agree to confirm these terms in your pull request. Any request that does not explicitly accept the terms will be assumed to have accepted.

## Development

- Read [AGENTS.md](AGENTS.md) and [.github/copilot-instructions.md](.github/copilot-instructions.md) for the architecture and the pure-vs-I/O boundary rule.
- Run the tests before opening a PR:

  ```bash
  PYTHONPATH=src python -m pytest -q
  ```

- Keep new logic **pure** (no SDK / network) and unit-tested; put I/O only in `src/app/main.py`.
- **Never add a CDN or external URL** to authored front-end assets (`templates/`, `static/css`, `static/js`) — the air-gap guarantee is enforced by `tests/test_branding_guards.py`.
- Keep the offline install intact (committed wheels in `src/app/wheelhouse/`); refresh with `scripts/build_wheelhouse.sh`.
- Never commit secrets, tokens, or environment-specific identifiers.

## Adding a report

Reports are data-driven — add a row to the `report_config` registry table, no code change. See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) and [docs/REPORTS.md](docs/REPORTS.md).

Open a PR and request a second-party review.
