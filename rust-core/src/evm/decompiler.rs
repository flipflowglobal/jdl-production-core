// src/decompiler.rs — Full Decompiler (upgraded)
//
// Pipeline:
//   1. SymExec::run()         — inter-block symbolic execution + constant folding
//   2. lift_functions()       — group blocks into functions via dispatcher + DFS
//   3. structurize()          — convert IfGoto chains to if/else/require
//   4. emit_solidity()        — render clean pseudo-Solidity

use crate::cfg::{CFG, BlockType};
use crate::disasm::Disassembly;
use crate::signatures::SignatureReport;
use crate::symbolic::{SymExec, SymStack, Stmt, BlockResult};
use crate::types::{TypeCtx, StorageVar, CalldataParam, EvmType};
use rustc_hash::FxHashMap;
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
pub struct DecompiledOutput {
    pub pseudo_source:   String,
    pub functions:       Vec<DecompiledFn>,
    pub storage_slots:   Vec<StorageSlotOut>,
    pub total_params:    usize,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct DecompiledFn {
    pub selector:     Option<String>,
    pub name:         String,
    pub params:       Vec<ParamOut>,
    pub body:         Vec<String>,
    pub is_view:      bool,
    pub is_payable:   bool,
    pub start_block:  usize,
    pub block_ids:    Vec<usize>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ParamOut {
    pub name: String,
    pub ty:   String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StorageSlotOut {
    pub slot:   u64,
    pub name:   String,
    pub ty:     String,
    pub reads:  usize,
    pub writes: usize,
}

// ── Function grouping ─────────────────────────────────────────────────────

struct FnGroup {
    selector:    Option<String>,
    name:        String,
    entry_block: usize,
    block_ids:   Vec<usize>,
    params:      Vec<CalldataParam>,
    is_payable:  bool,
}

fn collect_reachable(entry: usize, cfg: &CFG, visited: &mut rustc_hash::FxHashSet<usize>) {
    if visited.contains(&entry) { return; }
    visited.insert(entry);
    for &succ in &cfg.blocks[entry].successors {
        collect_reachable(succ, cfg, visited);
    }
}

fn lift_functions(
    cfg:     &CFG,
    sigs:    &SignatureReport,
    results: &FxHashMap<usize, BlockResult>,
) -> Vec<FnGroup> {
    let mut groups: Vec<FnGroup> = Vec::new();
    let mut covered: rustc_hash::FxHashSet<usize> = rustc_hash::FxHashSet::default();

    let off_to_bid: FxHashMap<usize, usize> = cfg.offset_to_block.iter()
        .map(|&(o, b)| (o, b))
        .collect();

    for sig in &sigs.functions {
        if let Some(tgt_offset) = sig.jump_target {
            if let Some(&entry_bid) = off_to_bid.get(&tgt_offset) {
                let mut block_ids_set = rustc_hash::FxHashSet::default();
                collect_reachable(entry_bid, cfg, &mut block_ids_set);
                let mut ids: Vec<usize> = block_ids_set.iter().copied().collect();
                ids.sort_unstable();

                let params: Vec<CalldataParam> = ids.iter()
                    .flat_map(|bid| results.get(bid).map(|r| r.params.clone()).unwrap_or_default())
                    .collect();

                let is_payable = ids.iter().any(|bid| {
                    results.get(bid).map(|r| {
                        r.stmts.iter().any(|s| matches!(s, Stmt::Assign { rhs, .. } if rhs.contains("msg.value")))
                    }).unwrap_or(false)
                });

                for &bid in &ids { covered.insert(bid); }

                let name = sig.known_name.clone()
                    .map(|n| n.split('(').next().unwrap_or(&n).to_string())
                    .unwrap_or_else(|| format!("fn_{}", &sig.selector[2..6]));

                groups.push(FnGroup {
                    selector: Some(sig.selector.clone()),
                    name,
                    entry_block: entry_bid,
                    block_ids: ids,
                    params,
                    is_payable,
                });
            }
        }
    }

    if !covered.contains(&0) {
        let mut ids_set = rustc_hash::FxHashSet::default();
        ids_set.insert(0);
        for &succ in &cfg.blocks[0].successors {
            if !covered.contains(&succ) { ids_set.insert(succ); }
        }
        let mut block_ids: Vec<usize> = ids_set.into_iter().collect();
        block_ids.sort_unstable();
        for &bid in &block_ids { covered.insert(bid); }
        groups.push(FnGroup {
            selector: None,
            name: "fallback".into(),
            entry_block: 0,
            block_ids,
            params: vec![],
            is_payable: false,
        });
    }

    groups
}

fn structurize(
    block_ids: &[usize],
    results:   &FxHashMap<usize, BlockResult>,
    cfg:       &CFG,
    indent:    usize,
) -> Vec<String> {
    let ind  = "    ".repeat(indent);
    let mut out: Vec<String> = Vec::new();
    let mut emitted: rustc_hash::FxHashSet<usize> = rustc_hash::FxHashSet::default();

    for &bid in block_ids {
        if emitted.contains(&bid) { continue; }
        emitted.insert(bid);

        let result = match results.get(&bid) { Some(r) => r, None => continue };

        let preds = &cfg.blocks[bid].predecessors;
        if preds.len() > 1 && bid != 0 {
            out.push(format!("{ind}/* ── block_{bid} ── */"));
        }

        for stmt in &result.stmts {
            match stmt {
                Stmt::Require { cond, msg } => {
                    match msg {
                        Some(m) => out.push(format!("{ind}require({cond}, \"{m}\");")),
                        None    => out.push(format!("{ind}require({cond});")),
                    }
                }
                Stmt::IfGoto { cond, target } => {
                    let target_bid = cfg.offset_to_block.iter()
                        .find(|(off, _)| *off == *target)
                        .map(|(_, b)| *b);

                    if let Some(tbid) = target_bid {
                        let is_revert = results.get(&tbid).map(|r|
                            r.stmts.iter().any(|s| matches!(s, Stmt::Revert{..}))
                        ).unwrap_or(false);

                        if is_revert {
                            out.push(format!("{ind}require(!({cond}));"));
                            emitted.insert(tbid);
                        } else {
                            out.push(format!("{ind}if ({cond}) {{"));
                            let inner = structurize(&[tbid], results, cfg, indent + 1);
                            out.extend(inner);
                            out.push(format!("{ind}}}"));
                            emitted.insert(tbid);
                        }
                    } else {
                        out.push(stmt.render(&ind));
                    }
                }
                Stmt::Goto { target } => {
                    let is_seq = block_ids.iter()
                        .position(|&b| b == bid)
                        .and_then(|i| block_ids.get(i + 1))
                        .map(|&next| cfg.blocks[next].start_offset == *target)
                        .unwrap_or(false);
                    if !is_seq { out.push(stmt.render(&ind)); }
                }
                Stmt::Comment(c) if c.starts_with("SLOAD") => { /* suppress */ }
                Stmt::MStore { .. } => { /* suppress low-level */ }
                other => out.push(other.render(&ind)),
            }
        }
    }

    out
}

fn reconstruct_params(group: &FnGroup) -> Vec<ParamOut> {
    // Parse types from function signature name like "transfer(address,uint256)"
    let type_list: Vec<EvmType> = group.name.find('(').map(|start| {
        let end = group.name.rfind(')').unwrap_or(group.name.len());
        let inner = &group.name[start+1..end];
        inner.split(',').map(str::trim).map(|t| {
            if t == "address" { EvmType::Address }
            else if t.starts_with("uint") { EvmType::Uint(t[4..].parse().unwrap_or(256)) }
            else if t.starts_with("int")  { EvmType::Int(t[3..].parse().unwrap_or(256)) }
            else if t == "bool"   { EvmType::Bool }
            else if t == "bytes"  { EvmType::BytesDynamic }
            else if t.starts_with("bytes") { EvmType::Bytes(t[5..].parse().unwrap_or(32)) }
            else { EvmType::Unknown }
        }).collect()
    }).unwrap_or_default();

    // Deduplicate params by index
    let mut seen = rustc_hash::FxHashSet::default();
    let mut deduped: Vec<&CalldataParam> = group.params.iter()
        .filter(|p| seen.insert(p.index))
        .collect();
    deduped.sort_by_key(|p| p.index);

    deduped.iter().enumerate().map(|(i, p)| {
        let ty = type_list.get(i).cloned().unwrap_or(EvmType::Unknown);
        let ty_name = ty.solidity_name();
        let param_name = match &ty {
            EvmType::Address => format!("addr{i}"),
            EvmType::Uint(_) => format!("amount{i}"),
            EvmType::Bool    => format!("flag{i}"),
            _                => format!("param{i}"),
        };
        ParamOut { name: param_name, ty: ty_name }
    }).collect()
}

fn is_view_fn(group: &FnGroup, results: &FxHashMap<usize, BlockResult>) -> bool {
    !group.block_ids.iter().any(|bid|
        results.get(bid).map(|r| r.stmts.iter().any(|s|
            matches!(s, Stmt::SStore{..} | Stmt::Call{..} | Stmt::DelegateCall{..})
        )).unwrap_or(false)
    )
}

pub fn decompile(disasm: &Disassembly, cfg: &CFG, sigs: &SignatureReport) -> DecompiledOutput {
    let mut exec = SymExec::new(disasm, cfg);
    let results  = exec.run();
    let type_ctx = exec.type_ctx();

    let groups = lift_functions(cfg, sigs, &results);
    let storage_vars = type_ctx.to_storage_vars();

    let mut functions: Vec<DecompiledFn> = Vec::new();
    for group in &groups {
        let params    = reconstruct_params(group);
        let view_flag = is_view_fn(group, &results);
        let body      = structurize(&group.block_ids, &results, cfg, 2);

        functions.push(DecompiledFn {
            selector:    group.selector.clone(),
            name:        group.name.clone(),
            params,
            body,
            is_view:     view_flag,
            is_payable:  group.is_payable,
            start_block: group.entry_block,
            block_ids:   group.block_ids.clone(),
        });
    }

    let source = emit_solidity(&functions, &storage_vars, sigs);
    let total_params = functions.iter().map(|f| f.params.len()).sum();
    let storage_out  = storage_vars.iter().map(|v| StorageSlotOut {
        slot: v.slot, name: v.name.clone(), ty: v.ty.solidity_name(),
        reads: v.reads, writes: v.writes,
    }).collect();

    DecompiledOutput { pseudo_source: source, functions, storage_slots: storage_out, total_params }
}

fn emit_solidity(fns: &[DecompiledFn], storage_vars: &[StorageVar], sigs: &SignatureReport) -> String {
    let mut s = String::with_capacity(8192);

    s.push_str("// ╔══════════════════════════════════════════════════════════╗\n");
    s.push_str("// ║     HINSDALE DECOMPILER — PSEUDO-SOLIDITY OUTPUT v2      ║\n");
    s.push_str("// ║  Inter-block symbolic execution + constant folding        ║\n");
    s.push_str("// ║  WARNING: Reconstructed — verify before use.             ║\n");
    s.push_str("// ╚══════════════════════════════════════════════════════════╝\n\n");
    s.push_str("// SPDX-License-Identifier: UNLICENSED\n");
    s.push_str("pragma solidity ^0.8.0;\n\n");

    // Known interfaces
    let has_erc20 = sigs.functions.iter().any(|f|
        matches!(f.selector.as_str(), "0x095ea7b3"|"0x70a08231"|"0xa9059cbb"|"0x23b872dd")
    );
    if has_erc20 {
        s.push_str("interface IERC20 {\n");
        s.push_str("    function transfer(address to, uint256 amount) external returns (bool);\n");
        s.push_str("    function approve(address spender, uint256 amount) external returns (bool);\n");
        s.push_str("    function balanceOf(address account) external view returns (uint256);\n");
        s.push_str("    function transferFrom(address from, address to, uint256 amount) external returns (bool);\n");
        s.push_str("}\n\n");
    }
    let has_flash = sigs.functions.iter().any(|f| f.selector == "0x42b0b77c");
    if has_flash {
        s.push_str("interface IPool {\n");
        s.push_str("    function flashLoanSimple(\n");
        s.push_str("        address receiverAddress,\n");
        s.push_str("        address asset,\n");
        s.push_str("        uint256 amount,\n");
        s.push_str("        bytes calldata params,\n");
        s.push_str("        uint16 referralCode\n");
        s.push_str("    ) external;\n");
        s.push_str("}\n\n");
    }

    s.push_str("contract Decompiled {\n\n");

    // Storage
    if !storage_vars.is_empty() {
        s.push_str("    // ── Storage Layout (recovered) ───────────────────────────\n");
        for v in storage_vars {
            s.push_str(&format!(
                "    {} public {}; // slot 0x{:x} | r:{} w:{}\n",
                v.ty.solidity_name(), v.name, v.slot, v.reads, v.writes
            ));
        }
        s.push('\n');
    }

    // Event stubs
    if !sigs.event_topics.is_empty() {
        s.push_str("    // ── Events (topic hashes from LOG patterns) ──────────────\n");
        for (i, t) in sigs.event_topics.iter().enumerate() {
            s.push_str(&format!("    // event Unknown{i}(...); // topic: {t}\n"));
        }
        s.push('\n');
    }

    // Functions
    for f in fns {
        if f.name == "fallback" && f.body.is_empty() { continue; }

        let sel = f.selector.as_deref().map(|s| format!("  // {s}")).unwrap_or_default();
        let mutability = if f.is_view { " view" } else if f.is_payable { " payable" } else { "" };
        let visibility = if f.name == "fallback" || f.name == "_internal" { "internal" } else { "external" };
        let params_str = f.params.iter()
            .map(|p| format!("{} {}", p.ty, p.name))
            .collect::<Vec<_>>()
            .join(", ");

        s.push_str(&format!(
            "    function {}({}) {}{}{} {{\n",
            f.name, params_str, visibility, mutability, sel
        ));

        let body: Vec<&str> = f.body.iter().map(|l| l.as_str()).filter(|l| !l.trim().is_empty()).collect();
        if body.is_empty() {
            s.push_str("        // (no meaningful statements recovered)\n");
        } else {
            for line in body { s.push_str(line); s.push('\n'); }
        }

        s.push_str("    }\n\n");
    }

    s.push_str("}\n");
    s
}
