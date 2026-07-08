# Termux Deep-Dive — Setup, Run & Assured Execution

This is the **deep-dive** companion to the [Termux quick-start](../TERMUX.md) and the
[step-by-step walkthrough](TERMUX_WALKTHROUGH.md). It explains *what actually runs where*
on Termux, and — more importantly — how the repo turns "I ran the installer" into a
**proven, assured method of execution**: a layered verifier that refuses to claim success
until the engine can demonstrably run.

If you just want commands, use the [walkthrough](TERMUX_WALKTHROUGH.md). Read this when you
want to understand the machine, debug a stubborn install, or trust the result before arming
anything.

---

## The one-command assured install

This repo is **private**, so the installer is bootstrapped with `git clone` (which
authenticates with your GitHub credentials), **not** a `curl` of the raw script URL —
`raw.githubusercontent.com` returns 404 for unauthenticated requests to private repos, so
`curl … | bash` would pipe an empty stream into bash and silently do nothing:

```bash
pkg install -y git && \
git clone https://github.com/flipflowglobal/jdl-production-core.git ~/projects/jdl-production-core && \
bash ~/projects/jdl-production-core/scripts/termux-install.sh
```

`termux-install.sh` then runs these stages and **ends by verifying itself**:

| Stage | What it does |
|-------|--------------|
| 1. Guard | Aborts unless it's really Termux (before touching anything) |
| 2. Packages | `pkg install python git openssl libffi clang make` (noninteractive; no `pkg upgrade` — that prompt hangs headless) |
| 3. Clone | Clone (or `git pull`) into `$JDL_DIR` (default `~/projects/jdl-production-core`) |
| 4. Setup | `bash setup.sh` — venv, deps, `jdl` command, `.env` auto-wire |
| 5. **Verify** | `bash scripts/termux-verify.sh` — proves the engine can execute |

Stage 5 is what makes the method *assured*: the installer doesn't print "complete" and hope
— it runs the verifier and reports **execution ASSURED** only when every blocking check
passes. If anything is wrong it points you at `termux-verify.sh --fix`.

> **Why clone, not curl?** For a *public* repo a `curl … raw…/script.sh | bash` one-liner
> works, but this repo is private — raw returns 404 unauthenticated, so that one-liner
> fetches nothing. `git clone` uses the credentials you already use to pull the repo. It's
> also the safer habit: you can read `setup.sh` / `termux-install.sh` / `termux-verify.sh`
> before running anything.
>
> **Private-repo auth:** cloning needs GitHub credentials on the device — a Personal Access
> Token (repo scope) at the HTTPS password prompt, `gh auth login`, or an SSH key with the
> `git@github.com:…` URL (`JDL_REPO_URL=git@github.com:flipflowglobal/jdl-production-core.git`).

---

## The execution model — what runs where

```
┌─ Android device (Termux, Bionic libc) ─────────────────────────────┐
│                                                                     │
│  ~/projects/jdl-production-core/     ← the repo (git checkout)      │
│      python/jdl_flash/               ← the engine package           │
│      setup.sh · scripts/*.sh         ← installers / launchers       │
│                                                                     │
│  ~/.flash_venv/                      ← Python virtualenv            │
│      bin/python  bin/jdl             ← what actually executes       │
│      (--system-site-packages, so pkg-installed wheels are visible)  │
│                                                                     │
│  ~/jdl/.env                          ← config (keys), OUTSIDE repo  │
│  ~/.flash_loan_engine/               ← data dir: logs, pid, revenue │
│      daemon.log · *.pid                                             │
└─────────────────────────────────────────────────────────────────────┘
```

Key facts that make Termux different from a Linux server:

- **Bionic libc, not glibc.** Foundry ships glibc-only binaries, so the whole optional
  Node/Foundry/Rust server-side stack (`node/`, `rust/hotpath/`) is deliberately **skipped**
  on Termux — the Python engine needs none of it (see [`POLYGLOT.md`](../POLYGLOT.md)).
- **`web3` 6.x, not 5.x.** 6.x drops the `ipfshttpclient → psutil` dependency that can't
  build on Android and avoids the `parsimonious` `getargspec` crash. `setup.sh` installs the
  Android-safe line and pulls prebuilt `python-rpds-py` / `python-psutil` wheels for the two
  packages that can't compile from source on-device.
- **The venv uses `--system-site-packages`** so pip reuses those prebuilt wheels instead of
  trying (and failing) to build them.
- **Config lives outside the repo** at `~/jdl/.env`, so `git pull` / `jdl update` never
  touches your keys, and the repo never risks committing them.

---

## The layered verifier — `termux-verify.sh`

`jdl integrate` already answers *"is my config wired?"* (`.env`, RPC, contract, daemon). But
it assumes the package already imports — it can't catch the failures that actually break most
Termux installs: no venv, `jdl` not on PATH, `import web3` crashing. `termux-verify.sh` checks
that lower layer first, then defers to `jdl integrate`, and prints one verdict.

```bash
bash scripts/termux-verify.sh          # verify only  (exit 0 = assured, 1 = not ready)
bash scripts/termux-verify.sh --fix    # on failure, re-run setup.sh once, then re-verify
bash scripts/termux-verify.sh --run    # after a PASS, launch the engine
bash scripts/termux-verify.sh --quick  # skip the (slow) network RPC probe
```

### What it checks, and why each matters

| Layer | Check | Blocking? | Why |
|-------|-------|-----------|-----|
| 1. Environment | Platform is Termux | advisory | doctor is Termux-focused; still runs elsewhere |
| 1. Environment | Python ≥ 3.9 | **blocking** | engine + deps require it |
| 2. Install | venv present at `~/.flash_venv` | **blocking** | nothing runs without it |
| 2. Install | `import web3` succeeds | **blocking** | the #1 Termux failure |
| 2. Install | `jdl_flash` + engine module import | **blocking** | the code must load |
| 2. Install | `jdl` command on PATH | **blocking** | the entry point must exist |
| 3. Smoke | `jdl --help` dispatches | **blocking** | the CLI must actually route |
| 3. Smoke | `jdl status` runs non-interactively | advisory | state layer sanity (no data dir yet is fine) |
| 4. Live-readiness | `jdl integrate` (`.env`/RPC/contract/daemon) | advisory | scan-only mode runs without these |

**Blocking vs advisory is the whole point.** The verdict — *execution ASSURED* — means the
software will run. It does **not** mean you're wired for live trading: an undeployed contract
or an unset key is a valid *scan-only* state, so those are advisory and never fail the verdict.
This keeps "can it execute?" honestly separate from "is it armed to trade?".

### Reading the verdict

- `✓ ASSURED — the engine can execute on this device.` (exit `0`) — you're good; launch with
  `source ~/.flash_venv/bin/activate && jdl start flashloan`.
- `✗ NOT READY — N blocking check(s) failed.` (exit `1`) — the script lists the exact fix for
  each failure (deduplicated). Fastest path: `bash scripts/termux-verify.sh --fix`.

Because it exits non-zero on failure, you can also gate automation on it, e.g. only start the
always-on swarm if the install verifies:

```bash
bash scripts/termux-verify.sh --quick && bash scripts/start-swarm-daemon.sh
```

---

## From verified install → assured run

1. **Install & verify** — the `git clone … && bash scripts/termux-install.sh` one-liner above
   (or clone + `bash setup.sh` + `bash scripts/termux-verify.sh` by hand).
2. **Wire config** — `jdl integrate` shows what's unset; `nano ~/jdl/.env` to add `PRIVATE_KEY`
   / `ALCHEMY_ARB_KEY`. Re-run the verifier to confirm live-readiness turns green.
3. **Run** — `jdl start flashloan` (interactive), or `nohup jdl supervisor &` for an
   auto-restarting daemon; watch with `jdl show flashloans` from a second shell.
4. **Keep alive** — `termux-wake-lock` (needs Termux:API) so Android doesn't suspend it.
5. **Survive reboots** — `bash setup.sh swarm-boot` installs a Termux:Boot hook (needs the
   Termux:Boot app). See the [walkthrough](TERMUX_WALKTHROUGH.md#step-8--optional-always-on-across-reboots).
6. **Update** — `jdl update` (git pull + reinstall); re-run `termux-verify.sh` afterward to
   confirm the update didn't break execution.

---

## Recovery cheat-sheet

| Symptom | Assured fix |
|---------|-------------|
| Any doubt the install works | `bash scripts/termux-verify.sh` — it tells you exactly what's wrong |
| Verifier reports failures | `bash scripts/termux-verify.sh --fix` (re-runs setup, re-verifies) |
| `No module named 'web3'` | You forgot the venv: `source ~/.flash_venv/bin/activate` |
| `import web3` crashes | `bash setup.sh` reinstalls the Android-safe web3 6.x |
| `jdl: command not found` | venv not active, or reinstall: `pip install -e ~/projects/jdl-production-core/python` |
| Everything looks installed but won't scan | `jdl integrate` — it's a config/RPC/key issue, not an install issue |
| Want proof before arming the swarm | `bash scripts/termux-verify.sh && bash scripts/start-swarm-daemon.sh` |

---

See also: [`TERMUX.md`](../TERMUX.md) · [`docs/TERMUX_WALKTHROUGH.md`](TERMUX_WALKTHROUGH.md) ·
[`POLYGLOT.md`](../POLYGLOT.md) · [`README.md`](../README.md)
