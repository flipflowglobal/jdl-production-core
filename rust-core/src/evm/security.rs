// src/security.rs — Bytecode-level security pattern detector

use crate::disasm::Disassembly;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
pub enum Severity {
    Critical,
    High,
    Medium,
    Low,
    Info,
}

impl std::fmt::Display for Severity {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let s = match self {
            Self::Critical => "CRITICAL",
            Self::High     => "HIGH",
            Self::Medium   => "MEDIUM",
            Self::Low      => "LOW",
            Self::Info     => "INFO",
        };
        write!(f, "{s}")
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Finding {
    pub severity:    Severity,
    pub title:       String,
    pub description: String,
    pub offset:      Option<usize>,
    pub pattern:     String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct SecurityReport {
    pub findings:          Vec<Finding>,
    pub has_selfdestruct:  bool,
    pub has_delegatecall:  bool,
    pub has_create2:       bool,
    pub has_staticcall:    bool,
    pub sstore_count:      usize,
    pub sload_count:       usize,
    pub call_count:        usize,
    pub risk_score:        u32,    // 0-100
}

fn finding(sev: Severity, title: &str, desc: &str, offset: Option<usize>, pattern: &str) -> Finding {
    Finding {
        severity: sev,
        title: title.to_string(),
        description: desc.to_string(),
        offset,
        pattern: pattern.to_string(),
    }
}

pub fn analyze_security(disasm: &Disassembly) -> SecurityReport {
    let instrs = &disasm.instructions;
    let mut findings: Vec<Finding> = Vec::new();

    let mut has_selfdestruct  = false;
    let mut has_delegatecall  = false;
    let mut has_create2       = false;
    let mut has_staticcall    = false;
    let mut sstore_count      = 0usize;
    let mut sload_count       = 0usize;
    let mut call_count        = 0usize;

    let n = instrs.len();

    for (i, ins) in instrs.iter().enumerate() {
        match ins.opcode {
            // ── SELFDESTRUCT ─────────────────────────────────────────────
            0xff => {
                has_selfdestruct = true;
                // Check if preceded by owner check (look for caller-based JUMPI in window)
                let protected = instrs[..i].iter().rev().take(30)
                    .any(|x| x.opcode == 0x33 || x.mnemonic.contains("owner"));
                let sev = if protected { Severity::Medium } else { Severity::Critical };
                findings.push(finding(
                    sev,
                    "SELFDESTRUCT present",
                    if protected {
                        "Contract contains SELFDESTRUCT with apparent access control upstream"
                    } else {
                        "Contract contains SELFDESTRUCT with no visible access control — funds at risk"
                    },
                    Some(ins.offset),
                    "SELFDESTRUCT",
                ));
            }

            // ── DELEGATECALL ─────────────────────────────────────────────
            0xf4 => {
                has_delegatecall = true;
                // Check if address is from storage (proxy pattern) or from calldata (dangerous)
                let addr_from_calldata = instrs[..i].iter().rev().take(10)
                    .any(|x| x.opcode == 0x35 || x.opcode == 0x36);
                let sev = if addr_from_calldata { Severity::Critical } else { Severity::High };
                findings.push(finding(
                    sev,
                    "DELEGATECALL detected",
                    if addr_from_calldata {
                        "DELEGATECALL with address derived from calldata — potential arbitrary code execution"
                    } else {
                        "DELEGATECALL present (proxy pattern or library call — verify target trust)"
                    },
                    Some(ins.offset),
                    "DELEGATECALL",
                ));
            }

            // ── CREATE2 ──────────────────────────────────────────────────
            0xf5 => {
                has_create2 = true;
                findings.push(finding(
                    Severity::Info,
                    "CREATE2 detected",
                    "Deterministic deployment — salt manipulation attacks possible if salt is user-controlled",
                    Some(ins.offset),
                    "CREATE2",
                ));
            }

            // ── STATICCALL ───────────────────────────────────────────────
            0xfa => { has_staticcall = true; }

            // ── SSTORE ───────────────────────────────────────────────────
            0x55 => {
                sstore_count += 1;
                // Check for reentrancy: SSTORE after CALL without CEI pattern
                // Look backwards for a CALL (0xf1) before a SSTORE without
                // intervening SSTORE (Checks-Effects pattern respected if
                // effect (SSTORE) comes before the CALL)
                let call_before_sstore = instrs[..i].iter().rev().take(50)
                    .any(|x| x.opcode == 0xf1 || x.opcode == 0xf4);
                let sstore_before_call = instrs[..i].iter().rev().take(50)
                    .any(|x| x.opcode == 0x55);
                if call_before_sstore && !sstore_before_call {
                    findings.push(finding(
                        Severity::High,
                        "Potential reentrancy: SSTORE after CALL",
                        "State update (SSTORE) follows external CALL without prior SSTORE — violates CEI pattern",
                        Some(ins.offset),
                        "SSTORE_AFTER_CALL",
                    ));
                }
            }

            // ── SLOAD ────────────────────────────────────────────────────
            0x54 => { sload_count += 1; }

            // ── CALL ─────────────────────────────────────────────────────
            0xf1 => {
                call_count += 1;
                // Return value unchecked: look for POP right after CALL
                if i + 1 < n && instrs[i + 1].opcode == 0x50 {
                    findings.push(finding(
                        Severity::Medium,
                        "Unchecked CALL return value",
                        "Return value of CALL immediately POP'd — failed calls silently ignored",
                        Some(ins.offset),
                        "UNCHECKED_CALL",
                    ));
                }
                // ETH transfer: check if callvalue is non-zero
                let sends_eth = instrs[..i].iter().rev().take(10)
                    .any(|x| x.opcode == 0x34 || x.imm_u256.map(|v| v > 0).unwrap_or(false));
                if sends_eth {
                    findings.push(finding(
                        Severity::Low,
                        "ETH transfer via CALL",
                        "Contract sends ETH — verify recipient is not a contract that re-enters",
                        Some(ins.offset),
                        "ETH_TRANSFER",
                    ));
                }
            }

            // ── TX.ORIGIN ────────────────────────────────────────────────
            0x32 => {
                // ORIGIN used in comparison (phishing attack vector)
                if i + 2 < n && (instrs[i+1].opcode == 0x14 || instrs[i+2].opcode == 0x14) {
                    findings.push(finding(
                        Severity::High,
                        "tx.origin used for auth",
                        "ORIGIN (tx.origin) used in equality check — phishing attack vector",
                        Some(ins.offset),
                        "TX_ORIGIN_AUTH",
                    ));
                }
            }

            // ── TIMESTAMP ────────────────────────────────────────────────
            0x42 => {
                // Timestamp used in comparison
                let next_is_cmp = i + 1 < n && matches!(
                    instrs[i+1].opcode, 0x10|0x11|0x12|0x13|0x14|0x15
                );
                if next_is_cmp {
                    findings.push(finding(
                        Severity::Low,
                        "Block timestamp dependency",
                        "TIMESTAMP used in conditional — miners can manipulate ±15s",
                        Some(ins.offset),
                        "TIMESTAMP_DEPENDENCY",
                    ));
                }
            }

            // ── Hardcoded ETH transfer ────────────────────────────────────
            // PUSH20 (address) + ... + CALL pattern
            0x73 => {
                if let Some(hex) = &ins.imm {
                    // Non-zero, non-contract-self address pushed
                    let is_zero = hex.chars().all(|c| c == '0');
                    if !is_zero {
                        let near_call = instrs[i..].iter().take(10)
                            .any(|x| x.opcode == 0xf1 || x.opcode == 0xa9059cbb_u32 as u8);
                        if near_call {
                            findings.push(finding(
                                Severity::Info,
                                "Hardcoded address",
                                &format!("Hardcoded address 0x{hex} — verify it matches deployment intent"),
                                Some(ins.offset),
                                "HARDCODED_ADDRESS",
                            ));
                        }
                    }
                }
            }

            _ => {}
        }
    }

    // ── Global checks ──────────────────────────────────────────────────────
    if sstore_count == 0 && call_count > 0 {
        findings.push(finding(
            Severity::Info,
            "Stateless contract",
            "No SSTORE instructions — contract holds no state (pure computation or proxy)",
            None,
            "STATELESS",
        ));
    }

    if disasm.jumpdests.is_empty() && disasm.total_bytes > 100 {
        findings.push(finding(
            Severity::Info,
            "No JUMPDEST — linear execution only",
            "No jump destinations found — contract may be a library or minimal proxy",
            None,
            "NO_JUMPDEST",
        ));
    }

    // ── Risk score ─────────────────────────────────────────────────────────
    let mut risk: u32 = 0;
    for f in &findings {
        risk += match f.severity {
            Severity::Critical => 30,
            Severity::High     => 15,
            Severity::Medium   => 8,
            Severity::Low      => 3,
            Severity::Info     => 1,
        };
    }
    let risk_score = risk.min(100);

    SecurityReport {
        findings,
        has_selfdestruct,
        has_delegatecall,
        has_create2,
        has_staticcall,
        sstore_count,
        sload_count,
        call_count,
        risk_score,
    }
}
