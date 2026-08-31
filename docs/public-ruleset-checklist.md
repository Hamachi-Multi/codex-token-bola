# Public Ruleset Checklist

Use this checklist when applying `release/public-bootstrap` to the sandbox or production public repository

The names below are recommended labels for operators. Automation does not identify rulesets by name

## Rulesets

| Recommended name | Target | Rules | Always-bypass App |
| --- | --- | --- | --- |
| `public-branch-catch-all` | All branches except `main`, `release-candidate/*`, and `public-ops/*` | Create, update, delete, and non-fast-forward update | None |
| `public-main-automated-updates` | `main` | Update, delete, non-fast-forward update, linear history, and configured status checks | Promotion and Ops Apply |
| `public-release-candidate-branches` | `release-candidate/*` | Create, update, and delete | Snapshot |
| `public-ops-candidate-branches` | `public-ops/*` | Create, update, and delete | Ops Stage |
| `public-release-tags` | `v*` tags | Create, update, and delete | Release Tag |

Set every ruleset to `active` and confirm each bypass entry points to the intended GitHub App integration

## Security Controls

- Enable secret scanning push protection before the first production candidate push
- Keep snapshot, promotion, release-tag, Ops Stage, and Ops Apply App identities separate
- Restrict each App installation and token to its documented branch role
- Treat `Always bypass` as bypassing every rule in that ruleset, including status checks and non-fast-forward restrictions
- Let the private promotion and Ops Apply workflows verify the exact candidate SHA, base SHA, and required check conclusions before updating `main`
- Run candidate checks after the Snapshot or Ops Stage App pushes the candidate branch; the candidate branch rulesets do not require those checks
- Allow the Release Tag App to create release tags only. Block tag updates and deletions; the protected orphan recovery workflow preserves the tag and creates only the missing GitHub Release

## Initial Bootstrap And Release

- Run private workflow `private-public-bootstrap` against an empty public repository
- Bootstrap creates only the parentless public-ops `main` commit; it creates no tag or GitHub Release
- The first product snapshot must use a non-breaking `feat:` subject
- The first release job creates runner-local `v0.0.0` only while calculating the release, then publishes `v0.1.0`
- The public repository must never contain a remote `v0.0.0` tag
