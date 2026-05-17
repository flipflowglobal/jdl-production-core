// src/disasm.rs — Linear-sweep disassembler with JUMPDEST map

use crate::opcodes::{lookup, is_push, push_size};
use rustc_hash::FxHashSet;
use serde::{Deserialize, Serialize};

/// A single decoded instruction.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Instruction {
    pub offset:   usize,
    pub opcode:   u8,
    pub mnemonic: String,
    pub imm:      Option<String>,    // hex immediate if PUSH
    pub imm_u256: Option<u64>,       // numeric value if fits in u64
    pub category: String,
    pub stack_in: i8,
    pub stack_out:i8,
}

/// Full disassembly result.
#[derive(Debug, Serialize, Deserialize)]
pub struct Disassembly {
    pub instructions:  Vec<Instruction>,
    pub jumpdests:     Vec<usize>,       // all valid JUMPDEST offsets
    pub total_bytes:   usize,
    pub instruction_count: usize,
}

/// Disassemble raw bytes (parallel-capable over chunks, merged here).
pub fn disassemble(bytecode: &[u8]) -> Disassembly {
    let mut instructions = Vec::with_capacity(bytecode.len() / 2);
    let mut jumpdests: Vec<usize> = Vec::new();
    let mut i = 0usize;

    while i < bytecode.len() {
        let byte = bytecode[i];
        let op   = lookup(byte);
        let imm_len = push_size(byte) as usize;

        let (imm_hex, imm_u64) = if imm_len > 0 && i + 1 + imm_len <= bytecode.len() {
            let raw = &bytecode[i + 1 .. i + 1 + imm_len];
            let hex = hex::encode(raw);
            // Parse into u64 if ≤ 8 bytes
            let num: Option<u64> = if imm_len <= 8 {
                let mut buf = [0u8; 8];
                buf[8 - imm_len..].copy_from_slice(raw);
                Some(u64::from_be_bytes(buf))
            } else {
                None
            };
            (Some(hex), num)
        } else {
            (None, None)
        };

        if byte == 0x5b {
            jumpdests.push(i);
        }

        instructions.push(Instruction {
            offset:    i,
            opcode:    byte,
            mnemonic:  op.mnemonic.to_string(),
            imm:       imm_hex,
            imm_u256:  imm_u64,
            category:  op.category.to_string(),
            stack_in:  op.stack_in,
            stack_out: op.stack_out,
        });

        i += 1 + imm_len;
    }

    let count = instructions.len();
    Disassembly {
        instructions,
        jumpdests,
        total_bytes: bytecode.len(),
        instruction_count: count,
    }
}

/// Build a fast set of valid jump destinations for CFG analysis.
pub fn jumpdest_set(disasm: &Disassembly) -> FxHashSet<usize> {
    disasm.jumpdests.iter().copied().collect()
}
