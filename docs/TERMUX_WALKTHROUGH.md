# Termux Full Setup & Run — Step-by-Step Walkthrough

A complete, narrated walkthrough for running the **JDL Flash-Loan Engine** on
**Termux (Android)** — from installing the Termux app to a running, self-restarting
engine. Every step is copy-paste. No ETH in your wallet is needed to start scanning.

> Prefer the one-page version? See [`TERMUX.md`](../TERMUX.md). This document is the
> long-form walkthrough with explanations, verification, and troubleshooting for each step.
> Want the execution model and the assured-execution verifier? See
> [`TERMUX_DEEPDIVE.md`](TERMUX_DEEPDIVE.md).

**What you'll end up with:** the `jdl` command on your PATH, a Python virtualenv at
`~/.flash_venv`, an auto-wired config at `~/jdl/.env`, and the interactive engine
scanning Arbitrum One for flash-loan arbitrage (optionally always-on across reboots).

---

## TL;DR — one-command install

Already have the Termux app installed? Steps 1–3 below collapse into a single line:

```bash
curl -fsSL https://raw.githubusercontent.com/flipflowglobal/jdl-production-core/main/scripts/termux-install.sh | bash
```

This runs [`scripts/termux-install.sh`](../scripts/termux-install.sh): it verifies
Termux, installs the system packages, clones the repo, and runs `setup.sh` — then
prints the next steps. It's idempotent (re-run any time; it `git pull`s if the repo is
already there). Override the clone dir with `JDL_DIR=~/somewhere`. After it finishes,
skip to **Step 4** (wire your `.env`). The rest of this document explains each stage the
one-liner automates, for when you want to understand or do it by hand.

---

## Step 0 — Install the Termux app (and optional companions)

Install **Termux** from **F-Droid** or **GitHub**, *not* the Google Play version
(that build is deprecated and its packages are frozen):

- Termux — https://f-droid.org/packages/com.termux/ · https://github.com/termux/termux-app
- **Termux:Boot** (optional) — runs the scanner automatically after a device reboot
  · https://github.com/termux/termux-boot
- **Termux:API** (optional) — enables `termux-wake-lock` so Android doesn't suspend
  the engine to save battery · https://github.com/termux/termux-api

Open Termux and grant it storage access when prompted. The optional apps are only
needed for the "always-on across reboots" section near the end.

---

## Step 1 — Update Termux and install system packages

```bash
pkg update -y && pkg upgrade -y
pkg install -y python git openssl libffi clang make
```

**Why these?** `python` + `git` are the essentials; `openssl`/`libffi` back the
crypto libraries; `clang`/`make` let pip build the few wheels that have no Android
binary. `setup.sh` also pulls two prebuilt wheels automatically (`python-rpds-py`,
`python-psutil`) that *cannot* compile from source on Android — you don't need to
install those by hand.

Verify:

```bash
python --version      # expect Python 3.9+ (3.8 is the hard minimum)
git --version
```

---

## Step 2 — Clone the repository

```bash
mkdir -p ~/projects
git clone https://github.com/flipflowglobal/jdl-production-core.git \
    ~/projects/jdl-production-core \
    --branch main --single-branch --depth 1
cd ~/projects/jdl-production-core
```

`--depth 1` keeps the download small (good on mobile data). You can clone anywhere;
`~/projects/jdl-production-core` is used throughout this guide. If you plan to use the
always-on boot hook later, cloning to `~/jdl-production-core` instead matches the hook's
default (otherwise you'll set `REPO_DIR` — noted in Step 8).

---

## Step 3 — Run setup

```bash
bash setup.sh
```

This is the same thing `jdl install` runs. It auto-detects Termux and:

1. Checks/installs the Termux packages from Step 1 (plus the two prebuilt wheels).
2. Creates a Python virtualenv at **`~/.flash_venv`** with `--system-site-packages`
   (so pip reuses the prebuilt wheels instead of recompiling them).
3. Installs the Termux-safe dependencies — **`web3` 6.x** (no `psutil`, no Rust build),
   `requests`, `python-dotenv`, `aiohttp`, `hexbytes` — then `pip install -e python/`,
   which puts the **`jdl`**, `flashloan`, and `flashpro` commands on your PATH.
4. **Skips** the optional Node.js / Foundry / Rust server-side stack entirely — Foundry
   ships glibc-only binaries that can't run under Termux's Bionic libc, and the Python
   engine doesn't need any of it (see [`POLYGLOT.md`](../POLYGLOT.md)).
5. Creates the data dir `~/.flash_loan_engine/` and **auto-wires `~/jdl/.env`** by
   scanning every `.env*` file already on your device and copying in any real value it
   finds for a still-placeholder key — no manual copy-paste.

When it finishes you'll see a "Setup Complete" banner listing the flash-loan sources
and next steps.

---

## Step 4 — Fill in whatever config is still missing

```bash
jdl integrate
```

`jdl integrate` checks every link in the chain — `.env`, wallet/RPC reachability,
deployed contract, supervised daemon — and prints one line per link, so a broken or
missing value is obvious. Anything it flags as unresolved has never been set anywhere
on your device. Almost always that's just:

```
PRIVATE_KEY=0x...          # your wallet private key
ALCHEMY_ARB_KEY=...        # free key from alchemy.com → Arbitrum One app
```

Add those by hand:

```bash
nano ~/jdl/.env
```

`FLASH_CONTRACT_ADDRESS` is **optional** — the engine runs in scan mode without it.
You only need it to execute live trades after deploying `FlashZeroGas.sol`.

> **Never commit `~/jdl/.env` or your private key anywhere.** It lives outside the repo
> (`~/jdl/`) on purpose.

---

## Step 5 — Launch the engine

```bash
source ~/.flash_venv/bin/activate     # activate the venv (once per shell)
jdl start flashloan                   # same as: jdl run
```

You get the interactive terminal dashboard. Menu options:

| Key | Action |
|-----|--------|
| `[1]` | Start automation engine |
| `[2]` | Scan for opportunities |
| `[3]` | Gas strategy status |
| `[4]` | Revenue log |
| `[5]` | Algorithm dashboard |
| `[6]` | System status |
| `[7]` | Configuration |
| `[8]` | Run tests (or from a shell: `jdl test`) |
| `[9]` | Discover flash loan protocols (live on-chain) |
| `[0]` | Exit |

Press **`[9]`** first to confirm live on-chain protocol discovery is working, then
**`[2]`** to scan for opportunities.

---

## Step 6 — Verify everything works

```bash
jdl test system
```

Runs the full test suite (the same suites CI runs). The `system` scope is special: if a
suite fails, it re-wires `.env` from every `.env` file on the device and retries the
failed suites once — so a red result means a real problem, not just a missing key.

---

## Step 7 — Keep it running in the foreground

For a session you're watching:

```bash
termux-wake-lock                      # stop Android suspending the CPU (needs Termux:API)
jdl start flashloan
```

To run it unattended with **auto-restart on crash**, from one shell:

```bash
termux-wake-lock
nohup jdl supervisor &                # auto-restart daemon, survives crashes
```

…and watch live activity from a **second** shell (swipe to a new Termux session):

```bash
source ~/.flash_venv/bin/activate
jdl show flashloans                   # streams status + logs (read-only, safe alongside the daemon)
```

Stop the supervised daemon:

```bash
pkill -f 'jdl supervisor'
```

---

## Step 8 — (Optional) Always-on across reboots

To keep the parallel scanner running unattended and survive device reboots:

```bash
bash setup.sh swarm-boot
```

This installs a **Termux:Boot** hook (symlinks `~/.termux/boot/start-flash-swarm.sh`)
so Android starts the swarm scanner after every reboot. Requirements:

- Install the **Termux:Boot** app and grant it autostart permission (otherwise Android
  never runs the hook).
- Recommended: `pkg install termux-api` + the **Termux:API** app so `termux-wake-lock`
  keeps the scanner alive instead of being suspended for battery.

Start it right now without waiting for a reboot:

```bash
nohup bash ~/projects/jdl-production-core/scripts/start-swarm-daemon.sh &
```

> **Clone-location note:** the boot hook defaults to looking for the repo at
> `~/jdl-production-core`. If you cloned to `~/projects/jdl-production-core` (as above),
> either set `REPO_DIR=~/projects/jdl-production-core` in the hook's environment, or use
> the `bash setup.sh swarm-boot` command above — it symlinks the correct absolute path
> automatically, so the clone location doesn't matter.

---

## Step 9 — Pull updates later

```bash
cd ~/projects/jdl-production-core
jdl update                            # git pull --ff-only + reinstall, one command
bash scripts/security-audit.sh        # optional: quick secret/config audit after updating
```

`jdl update` refuses to pull over uncommitted local changes unless you pass `--force`.

---

## Zero-wallet-funding, explained

The engine borrows from already-deployed protocols, so your wallet needs **zero ETH**
to start scanning:

- **Balancer V2** (`0xBA12...2C8`) — **0% fee**, best source
- **Aave V3** (`0x794a...4aD`) — 0.09% fee
- **Radiant Capital** (`0xF4B1...9E1`) — 0.09% fee
- **Uniswap V3** — multiple pools, 0.05–0.30% fee

Gas is covered by **Gelato Free Relay** (sponsors gas, deducts from output token) or
**Flashbots PEG** (builder fee embedded in the flash callback, `gasPrice=0`). To execute
live trades, set `FLASH_CONTRACT_ADDRESS` after deploying `FlashZeroGas.sol`.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `error: maturin failed` | Old `web3>=?` pulled a Rust build. Reinstall: `bash setup.sh` (installs web3 6.x, no Rust) |
| `No module named 'web3'` | Activate the venv: `source ~/.flash_venv/bin/activate` |
| `venv creation failed` | `pkg reinstall python`, then re-run `bash setup.sh` |
| `Connection refused` (RPC) | Add `ALCHEMY_ARB_KEY` to `~/jdl/.env`, then re-check with `jdl integrate` |
| `platform android is not supported` (psutil/rpds) | Re-run `bash setup.sh` — it installs the prebuilt `python-psutil` / `python-rpds-py` wheels |
| Screen goes to sleep / scan pauses | `termux-wake-lock` before launching (needs Termux:API) |
| Boot hook doesn't start after reboot | Install the Termux:Boot app + grant autostart; confirm `~/.termux/boot/start-flash-swarm.sh` exists |
| Not sure what's broken | `jdl test system` — auto-heals `.env` and retries any failed suite |

---

## Full Termux command cheat-sheet

```bash
# ── One-time setup (one-liner) ─────────────────────────────────
curl -fsSL https://raw.githubusercontent.com/flipflowglobal/jdl-production-core/main/scripts/termux-install.sh | bash

# ── …or the same thing by hand ─────────────────────────────────
pkg update -y && pkg upgrade -y
pkg install -y python git openssl libffi clang make
mkdir -p ~/projects
git clone https://github.com/flipflowglobal/jdl-production-core.git \
    ~/projects/jdl-production-core --branch main --single-branch --depth 1
cd ~/projects/jdl-production-core
bash setup.sh                         # = jdl install (installs deps, auto-wires ~/jdl/.env)

# ── Config ─────────────────────────────────────────────────────
jdl integrate                         # check every connection; shows what still needs a value
nano ~/jdl/.env                       # add PRIVATE_KEY / ALCHEMY_ARB_KEY if flagged

# ── Run ────────────────────────────────────────────────────────
source ~/.flash_venv/bin/activate     # activate venv (once per shell)
jdl start flashloan                   # launch interactive engine (= jdl run)
jdl test system                       # full test suite, auto-heals .env on failure

# ── Keep running ───────────────────────────────────────────────
termux-wake-lock                      # prevent CPU sleep (needs Termux:API)
nohup jdl supervisor &                # auto-restart daemon
jdl show flashloans                   # stream live activity (from a 2nd shell)
jdl status                            # one-shot: daemon liveness, execs, revenue
pkill -f 'jdl supervisor'             # stop the daemon

# ── Always-on across reboots ───────────────────────────────────
bash setup.sh swarm-boot              # install Termux:Boot hook (needs Termux:Boot app)
nohup bash ~/projects/jdl-production-core/scripts/start-swarm-daemon.sh &   # start now

# ── Maintenance ────────────────────────────────────────────────
cd ~/projects/jdl-production-core && jdl update   # git pull + reinstall
bash scripts/security-audit.sh                    # quick secret/config audit
jdl --help                                        # list every jdl subcommand
```
