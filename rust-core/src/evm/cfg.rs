// src/cfg.rs — Control-Flow Graph over disassembled EVM code
//
// Algorithm:
//  1. Mark block leaders: entry (0), every JUMPDEST, every instruction
//     after JUMP/JUMPI/STOP/RETURN/REVERT/INVALID.
//  2. Slice instructions into BasicBlocks.
//  3. For each block, resolve edges:
//     - JUMP  -> target (if constant PUSH before it)
//     - JUMPI -> target + fallthrough
//     - others -> fallthrough only

use crate::disasm::{Disassembly, Instruction};
use rustc_hash::{FxHashMap, FxHashSet};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BasicBlock {
    pub id:           usize,
    pub start_offset: usize,
    pub end_offset:   usize,       // inclusive offset of last instruction
    pub instructions: Vec<usize>,  // indices into Disassembly.instructions
    pub successors:   Vec<usize>,  // block ids
    pub predecessors: Vec<usize>,
    pub block_type:   BlockType,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum BlockType {
    Entry,
    Normal,
    JumpDest,
    Terminal,  // STOP / RETURN / REVERT / SELFDESTRUCT
    Invalid,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct CFG {
    pub blocks:         Vec<BasicBlock>,
    pub entry_block_id: usize,
    /// offset -> block id
    pub offset_to_block: Vec<(usize, usize)>,
}

impl CFG {
    pub fn block_count(&self) -> usize { self.blocks.len() }
    pub fn edge_count(&self) -> usize  { self.blocks.iter().map(|b| b.successors.len()).sum() }
}

/// Terminal opcodes — control does not fall through
fn is_terminal(op: u8) -> bool {
    matches!(op, 0x00 | 0xf3 | 0xfd | 0xff | 0xfe)
}

fn is_jump(op: u8) -> bool { op == 0x56 || op == 0x57 }

pub fn build_cfg(disasm: &Disassembly) -> CFG {
    let instrs = &disasm.instructions;
    if instrs.is_empty() {
        return CFG { blocks: vec![], entry_block_id: 0, offset_to_block: vec![] };
    }

    // ── 1. Identify leaders ────────────────────────────────────────────────
    let mut leaders: FxHashSet<usize> = FxHashSet::default();
    leaders.insert(0); // entry

    for (idx, ins) in instrs.iter().enumerate() {
        if ins.opcode == 0x5b {             // JUMPDEST
            leaders.insert(idx);
        }
        if is_terminal(ins.opcode) || is_jump(ins.opcode) {
            if idx + 1 < instrs.len() {
                leaders.insert(idx + 1);    // next instr starts new block
            }
        }
    }

    // ── 2. Build blocks ────────────────────────────────────────────────────
    let mut sorted_leaders: Vec<usize> = leaders.into_iter().collect();
    sorted_leaders.sort_unstable();

    // offset -> block id map for successor resolution
    let mut offset_to_block_id: FxHashMap<usize, usize> = FxHashMap::default();
    let mut blocks: Vec<BasicBlock> = Vec::with_capacity(sorted_leaders.len());

    for (bid, &start_idx) in sorted_leaders.iter().enumerate() {
        let end_idx = sorted_leaders
            .get(bid + 1)
            .map(|&n| n - 1)
            .unwrap_or(instrs.len() - 1);

        let instr_indices: Vec<usize> = (start_idx..=end_idx).collect();
        let start_offset  = instrs[start_idx].offset;
        let end_offset    = instrs[end_idx].offset;

        let last_op = instrs[end_idx].opcode;
        let btype = if bid == 0 {
            BlockType::Entry
        } else if instrs[start_idx].opcode == 0x5b {
            BlockType::JumpDest
        } else if is_terminal(last_op) {
            BlockType::Terminal
        } else {
            BlockType::Normal
        };

        offset_to_block_id.insert(start_offset, bid);

        blocks.push(BasicBlock {
            id: bid,
            start_offset,
            end_offset,
            instructions: instr_indices,
            successors: vec![],
            predecessors: vec![],
            block_type: btype,
        });
    }

    // Build offset -> block id lookup by start_offset
    let start_off_to_bid: FxHashMap<usize, usize> = blocks
        .iter()
        .map(|b| (b.start_offset, b.id))
        .collect();

    // ── 3. Resolve edges ───────────────────────────────────────────────────
    let block_count = blocks.len();
    let mut succs: Vec<Vec<usize>> = vec![vec![]; block_count];

    for (bid, block) in blocks.iter().enumerate() {
        let last_idx = *block.instructions.last().unwrap();
        let last_ins = &instrs[last_idx];
        let last_op  = last_ins.opcode;

        // Fallthrough: every non-terminal, non-unconditional-jump block
        let has_fallthrough = !is_terminal(last_op) && last_op != 0x56;

        if has_fallthrough && bid + 1 < block_count {
            succs[bid].push(bid + 1);
        }

        // JUMP / JUMPI: try to resolve target from preceding PUSH
        if is_jump(last_op) && last_idx > 0 {
            let prev = &instrs[last_idx - 1];
            if let Some(target_offset) = prev.imm_u256.map(|v| v as usize) {
                if let Some(&target_bid) = start_off_to_bid.get(&target_offset) {
                    if !succs[bid].contains(&target_bid) {
                        succs[bid].push(target_bid);
                    }
                }
            }
            // JUMPI also has fallthrough (already added above)
        }
    }

    // Write successors back and compute predecessors
    for (bid, s) in succs.iter().enumerate() {
        blocks[bid].successors = s.clone();
    }
    for bid in 0..block_count {
        let succs_copy = blocks[bid].successors.clone();
        for &sid in &succs_copy {
            blocks[sid].predecessors.push(bid);
        }
    }

    let offset_to_block: Vec<(usize, usize)> = blocks
        .iter()
        .map(|b| (b.start_offset, b.id))
        .collect();

    CFG { blocks, entry_block_id: 0, offset_to_block }
}
