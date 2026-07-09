# UserLAnd Setup & Full Test Suite

**UserLAnd** runs a real glibc Linux userspace (Ubuntu/Debian in a proot rootfs) on
Android — unlike **Termux**, which is Bionic libc. That difference matters: on UserLAnd the
**entire polyglot stack runs** (Python + Rust + Node + Solidity/Hardhat), so `setup.sh`
installs all of it and you can run the **full CI test suite** on-device. On Termux only the
Python engine runs (Foundry/Rust ship glibc-only binaries — see [`POLYGLOT.md`](../POLYGLOT.md)).

> On Termux, follow [`TERMUX.md`](../TERMUX.md) / [`docs/TERMUX_WALKTHROUGH.md`](TERMUX_WALKTHROUGH.md)
> instead — this guide is for the glibc UserLAnd environment (and applies equally to
> Ubuntu / WSL / macOS).

---

## Fastest path — one command (setup → enter .env → start)

`scripts/userland-setup.sh` does everything in one go: installs system packages, runs
`setup.sh`, **interactively prompts you to type in your `.env` values**, verifies the
wiring, and starts the engine. This repo is private, so clone first (git uses your
credentials), then run it in a real terminal (it prompts, so don't pipe it into bash):

```bash
sudo apt update && sudo apt install -y git && \
git clone https://github.com/flipflowglobal/jdl-production-core.git ~/jdl-production-core && \
bash ~/jdl-production-core/scripts/userland-setup.sh
```

It prompts only for values still missing after the auto-wire — typically **`PRIVATE_KEY`**
(input hidden) and **`ALCHEMY_ARB_KEY`**; `FLASH_CONTRACT_ADDRESS` is optional (scan-only
mode runs without it). Entered values are saved to `~/jdl/.env` at `0600` via the engine's
own writer (in place, no duplicates; pressing Enter keeps an existing value). Then it runs
`jdl integrate` and launches the engine.

Flags: `--no-apt` (skip the apt step), `--no-start` (set up but don't launch), `-y` (don't
ask before starting). Private-repo `git clone` needs GitHub auth on the device — a Personal
Access Token at the HTTPS prompt, `gh auth login`, or an SSH key.

Prefer to understand each step, run the test suite, or do it by hand? Continue below.

---

## 1. Prepare the UserLAnd session

Install **UserLAnd** from Google Play or F-Droid, create an **Ubuntu** filesystem, and open
a session (SSH or terminal). Then, inside it:

```bash
sudo apt update && sudo apt install -y git python3 python3-venv build-essential curl
```

`setup.sh` installs the rest (Node, Foundry, Rust) itself, but `git` + Python are needed to
get started, and `build-essential` gives you a compiler for any wheels/crates.

---

## 2. Clone the repo

This repo is **private**, so clone with `git` (which uses your GitHub credentials) — a plain
`curl` of a raw URL won't work on a private repo:

```bash
git clone https://github.com/flipflowglobal/jdl-production-core.git ~/jdl-production-core
cd ~/jdl-production-core
```

> Cloning a private repo needs GitHub auth: a Personal Access Token (repo scope) at the HTTPS
> password prompt, `gh auth login`, or an SSH key with the `git@github.com:…` URL.

---

## 3. Run setup (installs the FULL stack)

```bash
bash setup.sh
```

`setup.sh` auto-detects UserLAnd (`userland`/`android` in `/proc/version`) and treats it like
a Linux server — so, unlike Termux, it installs the **complete** toolchain:

- **Python** — venv at `~/.flash_venv`, web3 6.x, the `jdl`/`flashloan`/`flashpro` commands
- **Node.js** — `npm install` in `contracts/` and `node/`
- **Foundry** — `forge`/`cast` via `foundryup`
- **Rust** — `cargo`, then builds `rust/hotpath` (release)
- **`~/jdl/.env`** — auto-wired from every `.env` file on the machine

Activate the venv for your shell:

```bash
source ~/.flash_venv/bin/activate
```

---

## 4. Run the full test suite

One command runs the **entire cross-language suite** — the same four jobs CI runs:

```bash
bash scripts/run-all-tests.sh
```

| Suite | Command it runs | Gate |
|-------|-----------------|------|
| **python** | `jdl test` (all `jdl_flash` + supervisor + native suites) | blocking |
| **rust** | `cargo test` + `cargo clippy --all-targets -- -D warnings` | blocking |
| **node** | `npm test` (`node --test`) in `node/` | blocking |
| **solidity** | Hardhat compile (solc 0.8.20) in `contracts/` | advisory* |

\* Solidity is **advisory** — it mirrors CI's `continue-on-error` (Hardhat downloads solc and
resolves imports at compile time, so a network hiccup shouldn't fail the whole run).

The script prints a per-suite `✓ PASS` / `✗ FAIL` / `○ SKIP` summary and one verdict, exiting
`0` only when every **blocking** suite that ran passed.

### Options

```bash
bash scripts/run-all-tests.sh --quick    # python + rust only (skip the npm-heavy suites)
bash scripts/run-all-tests.sh --strict   # treat SKIP (missing toolchain) as a failure
ARB_RPC_URL=https://arb1.arbitrum.io/rpc bash scripts/run-all-tests.sh   # also run the mainnet-fork tests
```

With `ARB_RPC_URL` set, the contracts **mainnet-fork tests** run too (`npm run test:fork`,
and `forge test` if Foundry is installed) — the 7/7 fork suite.

### Running individual suites

You don't need the wrapper if you want just one:

```bash
jdl test                                    # Python (same as the CI python job)
( cd rust/hotpath && cargo test && cargo clippy --all-targets -- -D warnings )
( cd node && npm test )
( cd contracts && npm run compile )         # or: npm run test:fork  (needs ARB_RPC_URL)
```

---

## 5. Run the engine

Same as everywhere once the suite is green:

```bash
jdl integrate            # verify .env / RPC / contract / daemon wiring
jdl start flashloan      # launch the interactive engine
nohup jdl supervisor &   # auto-restarting daemon; watch with: jdl show flashloans
```

UserLAnd has no Termux:Boot, so for an always-on scanner across reboots use UserLAnd's own
autostart, a systemd user service (if your session has systemd), or `nohup` — see
`bash setup.sh swarm-boot`, which prints the right steps for a non-Termux platform.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `run-all-tests.sh` says `python … SKIP` | `jdl` not installed / venv not found — `source ~/.flash_venv/bin/activate` or re-run `bash setup.sh` |
| `rust … SKIP` | `cargo` missing — re-run `bash setup.sh`, or install rustup |
| `node … SKIP` | `npm` missing — `sudo apt install -y nodejs npm` (or use nvm), then re-run |
| `solidity … FAIL` | usually a network/solc-download hiccup; it's advisory and won't fail the verdict — retry with a connection |
| `test_cli.py` fails with `jdl: not found` | the venv's `bin/` isn't on PATH — `source ~/.flash_venv/bin/activate` (the wrapper handles this for you) |
| Fork tests fail / skipped | set `ARB_RPC_URL` to a working Arbitrum RPC and re-run |

---

See also: [`README.md`](../README.md) · [`TERMUX.md`](../TERMUX.md) · [`POLYGLOT.md`](../POLYGLOT.md) ·
[`contracts/README.md`](../contracts/README.md)
