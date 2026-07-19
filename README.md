# 🏆 BITS Marks Tracker

**Unofficial**, student-run marks leaderboard for the BITS Pilani WILP MTech AI/ML batch.
Students self-report marks per subject; the dashboard shows totals, percentages,
ranks and percentiles — per subject and overall.

> **Disclaimer:** This site has no affiliation with BITS Pilani. All marks are
> self-reported and unverified. Submitted data is stored in this public
> repository under `data/` — but you can stay anonymous: tick “Hide my BITS ID”
> and leave the name blank to appear as an alias (your ID is then never stored,
> only an untraceable code).

## How it works

No database. The GitHub repo **is** the database:

- `data/config.json` — semesters, subjects, and mark components (open this to add a new semester).
- `data/marks/<term>.json` — one JSON file per semester with every student's marks.
- The FastAPI backend reads/writes those files through the GitHub Contents API,
  so every submission is a commit and the dataset is open source by construction.
- Locally (no `GITHUB_TOKEN` set) it just reads/writes the files on disk.

Marks per subject: Quiz 1 (5) + Assignment 1 (10) + Midsem (30) + Quiz 2 (5) +
Assignment 2 (10) + End-sem (40) = **100**. Exception: **ML** has Quiz 1 = 10 and
Assignment 1 = 5 — any subject can override the component structure via a
`components` array on its entry in `data/config.json`. Partial entry is fine —
percentages are computed against the max of the components you've filled in, so
mid-semester comparisons stay fair. Percentile = % of students strictly below
you overall. Concurrent submissions are conflict-safe: a clashing write is
retried on fresh data, never overwritten.

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
| GET    | `/api/student?term=&bits_id=` | One student's saved marks (form pre-fill). Hidden-ID rows require the PIN via `X-Pin` header |
| POST   | `/api/submit`               | Upsert marks (merge per component). Body: `term`, `bits_id`, `pin`, optional `name`, `hide_id`, `marks` |
| GET    | `/api/export.csv?term=`     | Full dataset as CSV                  |

## Deploy (Vercel, free)

1. Push this repo to GitHub, plus a `data` branch for submissions:
   `git branch data && git push origin main data`. Keeping marks commits on
   `data` stops every student submission from triggering a Vercel redeploy.
2. Import the repo at [vercel.com/new](https://vercel.com/new) — `vercel.json`
   routes everything to the FastAPI app.
3. Set environment variables in the Vercel project:
   - `GITHUB_TOKEN` — fine-grained PAT, **Contents: read & write**, scoped to this repo only
   - `GITHUB_DATA_REPO` — e.g. `nirajkmr007/bits-marks-tracker`
   - `GITHUB_DATA_BRANCH` — `data`
   - `ANON_SECRET` — `openssl rand -hex 16`; set once before launch, never rotate

Submissions then land on the `data` branch as commits like
`marks: update 2025AA05123 (2026-S1)` (alias instead of ID for hidden rows).
Code pushes to `main` auto-deploy; CI (ruff + mypy + pytest) runs on every
push and PR. If dependencies ever change, regenerate the file Vercel installs
from: `uv export --no-dev --no-hashes --no-emit-project -o requirements.txt`.

## Adding a new semester

Add a term to `data/config.json` (subjects + components + `id_prefix` for the
new batch), set `current_term`, and push to `main` (config ships with the app,
so this needs a deploy). Create an empty `data/marks/<term>.json`
(`{"students": []}`) on the `data` branch.

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

## Contributing

Contributions are welcome — bug fixes, UI polish, charts, phase-2 auth, anything
on the roadmap, or your own ideas (open an issue first for big changes).

**1. Fork & set up** (one-time, ~2 minutes):

```bash
# fork on GitHub first, then:
git clone https://github.com/<your-username>/bits-marks-tracker.git
cd bits-marks-tracker
./scripts/install-just.sh    # installs the `just` command runner
just setup-local             # uv + venv + deps + git hooks + .env + smoke test
```

**2. Branch** — direct commits to `main` are blocked by a git hook, so always:

```bash
git checkout -b feat/my-change
```

**3. Develop** — `just run` serves the app at http://127.0.0.1:8000 with
auto-reload; marks are stored in local files (no tokens needed). Add or update
tests in `tests/` for any behavior change.

**4. Verify before pushing** — the same gate CI runs:

```bash
just check                   # ruff lint + format + mypy (strict) + pytest
```

**5. Open a Pull Request** against `nirajkmr007/bits-marks-tracker:main` with a
short description of what and why. CI must pass; a screenshot helps for UI
changes.

Ground rules: don't edit `data/marks/*.json` in PRs (that's the live database —
marks go through the website), don't add a real database or paid services (the
no-DB, free-hosting design is the point), and keep the single-page frontend
dependency-free.

## License

MIT — see [LICENSE](LICENSE). The collected data in `data/` is public domain.
