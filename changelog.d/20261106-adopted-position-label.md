## Unreleased — Adopted positions are named in words, not by their internal marker

- The Command Center "Open positions" table printed the raw `adopted_untracked` under the symbol and called the position "Olbos tracked". It now reads "Adopted from broker", and the Tracking column says "Adopted from broker · history may be incomplete". The phone home screen and the Journal use the same wording.
- One shared helper (`utils/positionLabels.ts`) decides what counts as adopted, so the pages cannot drift apart. Ordinary strategies are displayed exactly as before.
- Display only: no change to adoption, positions, strategy statistics, risk gates or order paths. Frontend only, no migration.
