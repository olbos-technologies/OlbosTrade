# changelog.d — one file per change

Add your changelog entry as a **new file** here. Do not edit `CHANGELOG.md`.

## Why

Every branch used to add its entry at the top of one shared file, so any two
concurrent branches conflicted there by construction. On 2026-10-31 that
happened five times in an evening across three agents working in parallel
(Claude, Cursor, Codex), twice leaving unresolved conflict markers on `main`,
and repeatedly blocking PRs whose CI was green.

`merge=union` (see `.gitattributes`) was tried first and helps only a *local*
merge of a branch that already contains the file. GitHub's own merge button
ignores it. A new file cannot conflict with another new file, so this removes
the problem at the merge button too.

## How

Create `changelog.d/YYYYMMDD-short-slug.md`:

```markdown
## Unreleased — Short title of the change

- What changed and why it matters to someone operating the system.
- Say what did NOT change when that is the reassuring part (risk gates, order
  paths, schema).
```

- The date is the day you write it. The slug is lowercase letters, digits and
  hyphens. A date plus a slug makes collisions between different changes
  practically impossible; if you hit one, add a more specific slug.
- Keep the `## Unreleased — ` heading. It is what the old file used, so entries
  read the same in both places.
- Changes that are not user-visible (docs, internal tests) need no fragment.

## Reading it

```bash
ls -r changelog.d/*.md | grep -v README | xargs cat     # newest first
```

`CHANGELOG.md` is the frozen archive of everything up to 2026-11-01. This
repository has never cut a release, so nothing assembles fragments into it;
the directory *is* the current changelog until a release process exists.
