## Unreleased — Changelog entries are one file per change

- New changelog entries now go in `changelog.d/` as one file each instead of at the top of `CHANGELOG.md`, which every branch used to edit and which therefore conflicted between any two concurrent branches — five times on 2026-10-31 alone, twice leaving conflict markers on `main`. `merge=union` only helps a local merge; GitHub's merge button ignores it. A new file cannot conflict with another new file. `CHANGELOG.md` is frozen as the archive. Process only; no behaviour change.
