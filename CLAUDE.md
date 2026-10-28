# Paper Trade: notes for Claude

A paper trading simulator (thinkorswim / Fidelity / Robinhood style) that runs 24/7 on a
simulated market. **Read `HANDOFF.md` first**: it has the current state and what to do next.

## How we work

- Build one roadmap phase at a time (roadmap in `README.md`). Each phase goes on its own
  branch (`phase-N-short-name`) with a PR to `main` on https://github.com/picklesu/paper-trade (public).
- **Claude reviews and merges; the user does not handle merges.** For each PR: run the
  `/code-review` skill on the PR number, fix the findings on the branch, post a short review
  comment, then `gh pr merge N --merge --delete-branch`, `git checkout main && git pull`.
- After merging, tell the user to run `./server.sh restart` and test. Fix reported problems
  in a follow-up PR.
- Stop at the end of each phase so the user can test before starting the next one.
- Commit as `picklesu` with the GitHub no-reply email (already in local git config). Never
  put the user's personal email in commits; the repo is public.
- Update the README roadmap status in each phase's PR.

## Environment gotchas (sandbox + Mac share this folder)

The user runs the app on **macOS**. Claude usually runs in a **Linux sandbox** that mounts
the same folder. Installed dependencies are platform-specific:

- **Never run `npm install` in `frontend/` from the sandbox.** It replaces the Mac's
  binaries and breaks `npm test` and Vite on the Mac ("Cannot find native binding"). To test
  the frontend in the sandbox, rsync `frontend/` (excluding `node_modules`, `dist`) to the
  scratchpad, run `npm ci` there, and run tests, build and Vite from that copy.
- In the sandbox, set `UV_PROJECT_ENVIRONMENT=$HOME/.venvs/paper-trade-backend` (check
  `/etc/sandbox-persistent.sh`) so `uv run` doesn't touch the Mac's `backend/.venv`.
- Stop servers you started by PID. Don't `pkill -f` with a pattern that also appears in your
  own command line; it kills the tool's shell (exit 144).
- Don't leave servers running in the sandbox after checking.
- **Always start sandbox backends with `PAPER_TRADE_DB=<scratchpad>/sandbox.db`.** Otherwise
  they write `backend/paper_trade.db`, which is the user's real account (saved state). Tests
  use temporary databases.
- The user's Mac server (`./server.sh on`) runs uvicorn with `--reload` from this shared
  folder. Whatever branch is checked out here goes live on their machine as you edit, and it
  may create or modify their `paper_trade.db`. Keep `main` checked out when not working on a
  phase.
- For visual checks: install Playwright and Chromium in a scratchpad folder, run the app
  (backend on 8000, mirrored Vite on another port such as 5199), and take a screenshot.

- **Adding a frontend package:** run `npm install <pkg>` in the scratchpad mirror, then copy
  `package.json` and `package-lock.json` back into `frontend/`. `server.sh on/restart`
  reinstalls on the Mac when the lockfile is newer than `node_modules/.package-lock.json`.
- **New dependencies:** pin an exact version at least 2 weeks old (check `npm view <pkg> time`),
  and pin transitive packages from the same project with `overrides` if a caret range would
  pull in something newer (dockview does this).
- CI (`.github/workflows/ci.yml`) must be green before merging: `gh pr checks <N> --watch`.

## Commands

```bash
./server.sh on|off|restart|status|logs   # user's one-command control (background)
cd backend && uv run pytest              # backend tests
cd frontend && npm test                  # frontend tests (from a mirror if in the sandbox)
cd frontend && npm run build && npx oxlint --deny-warnings   # CI treats warnings as errors
```
