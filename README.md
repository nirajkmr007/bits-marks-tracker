# 🏆 BITS Marks Tracker

**Unofficial**, student-run marks leaderboard for the BITS Pilani WILP MTech AI/ML batch.
Students self-report marks per subject; the dashboard shows totals, percentages,
ranks and percentiles — per subject and overall.

> **Disclaimer:** This site has no affiliation with BITS Pilani. All marks are
> self-reported and unverified. Everything submitted (BITS ID, name, marks) is
> stored in this public repository under `data/` — submit only if you're
> comfortable with that.

## How it works

No database. The GitHub repo **is** the database:

- `data/config.json` — semesters, subjects, and mark components (open this to add a new semester).
- `data/marks/<term>.json` — one JSON file per semester with every student's marks.
- The FastAPI backend reads/writes those files through the GitHub Contents API,
  so every submission is a commit and the dataset is open source by construction.
- Locally (no `GITHUB_TOKEN` set) it just reads/writes the files on disk.

Marks per subject: Quiz 1 (5) + Assignment 1 (10) + Midsem (30) + Quiz 2 (5) +
Assignment 2 (10) + End-sem (40) = **100**. Partial entry is fine — percentages
are computed against the max of the components you've filled in, so mid-semester
comparisons stay fair. Percentile = % of students strictly below you overall.

## Local development

Two steps from a fresh clone — everything else is automated:

```bash
git clone https://github.com/nirajkmr007/bits-marks-tracker.git
cd bits-marks-tracker

./scripts/install-just.sh    # step 1 (once): install the `just` command runner
just setup-local             # step 2: uv + venv + deps + git hooks + .env + tests
```

Then:

```bash
just run                     # dev server at http://127.0.0.1:8000 (auto-reload)
just check                   # ruff + mypy + pytest
just                         # list all commands
```

Without `GITHUB_TOKEN` set, marks are stored locally in `data/marks/<term>.json` —
edit or reset that file freely while developing. API docs: http://127.0.0.1:8000/docs.

## API

| Method | Path                        | Purpose                              |
| ------ | --------------------------- | ------------------------------------ |
| GET    | `/`                         | Dashboard UI                         |
| GET    | `/api/config`               | Semesters/subjects/components        |
| GET    | `/api/leaderboard?term=`    | Ranked leaderboard + stats           |
| GET    | `/api/student?term=&bits_id=` | One student's saved marks (form pre-fill) |
| POST   | `/api/submit`               | Upsert marks (merge per component)   |
| GET    | `/api/export.csv?term=`     | Full dataset as CSV                  |

## Deploy (Vercel, free)

1. Push this repo to GitHub.
2. Generate `requirements.txt` (Vercel doesn't read `uv.lock`):
   `uv export --no-dev --no-hashes --no-emit-project -o requirements.txt`
3. Import the repo at [vercel.com/new](https://vercel.com/new) — `vercel.json`
   routes everything to the FastAPI app.
4. Set environment variables in the Vercel project:
   - `GITHUB_TOKEN` — fine-grained PAT, **Contents: read & write**, scoped to this repo only
   - `GITHUB_DATA_REPO` — e.g. `nirajkmr007/bits-marks-tracker`
   - `GITHUB_DATA_BRANCH` — `main`

Submissions then land as commits like `marks: update 2025AA05123 (2026-S1)`.

## Adding a new semester

Add a term to `data/config.json` (subjects + components), create an empty
`data/marks/<term>.json` (`{"students": []}`), set `current_term`, redeploy.

## PIN protection

Each BITS ID is claimed with a 4-digit PIN on first submission; later edits
require the same PIN. Only a salted PBKDF2 hash of the PIN is stored in the
data file — never the PIN itself.

**Admin PIN reset:** when a student emails asking for a reset, open
`data/marks/<term>.json` on the `data` branch on GitHub, delete that student's
`pin_salt` and `pin_hash` fields, and commit. Their next submission sets a
fresh PIN. (Verify it's really them — e.g. ask from their known email/WhatsApp.)

Honest limitation: since the data file is public, someone determined could
brute-force a 4-digit PIN hash offline. The PIN stops casual overwrites, not a
motivated attacker — and every change is a git commit, so nothing is ever lost.
Proper per-student auth (Microsoft Entra ID) is the phase-2 fix.

## Privacy: optional name, hideable BITS ID

The **name is optional** — leave it blank (or use a nickname) and the
leaderboard shows the BITS ID instead, or an alias if the ID is hidden too.

Students can tick **“Hide my BITS ID”** when submitting. For those rows the data
file stores **no BITS ID at all** — only `id_hash`, an HMAC of the BITS ID keyed
by the server-side `ANON_SECRET` env var, plus a friendly alias derived from it
(e.g. “Silent Falcon 42”). There is nothing to decrypt: the server finds the row
on edit by re-computing the HMAC from the typed ID. The keyed hash also can't be
reversed by enumerating BITS IDs without the secret.

Operational notes:

- Set `ANON_SECRET` (e.g. `openssl rand -hex 16`) in Vercel **before launch**
  and never rotate it — anonymous rows become unreachable if it changes.
- For anonymous rows, `/api/student` requires the student's PIN; otherwise
  anyone could type a BITS ID and link it to an anonymous row.
- Commit messages use the alias, never the BITS ID.
- Caveat: if a student was public first and goes anonymous later, their earlier
  versions remain in git history. Truly anonymous = anonymous from first submit.
- PIN reset for anonymous students: ask them for their **alias**, find that row
  in `data/marks/<term>.json`, delete `pin_salt`/`pin_hash`, commit.

## Roadmap

- **Phase 2 — auth:** Microsoft Entra ID sign-in restricted to the BITS tenant,
  so only the owner of a BITS ID can edit their row. Until then, edits are
  open and trust-based (every change is a commit, so history is auditable).
- Charts (score distribution per subject), previous-semester archive views.

## License

MIT — see [LICENSE](LICENSE). The collected data in `data/` is public domain.
