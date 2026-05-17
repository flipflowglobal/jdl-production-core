// src/symbolic.rs — Inter-block Symbolic Execution Engine
//
// Executes the CFG across block boundaries, propagating symbolic state.
// Performs:
//   - Constant folding (ADD/SUB/AND/SHR of known constants evaluated)
//   - Type inference from usage patterns
//   - Calldata parameter recovery (CALLDATALOAD offset -> param index)
//   - Require/revert pattern matching
//   - Access control pattern detection
//   - Return value tracking

use crate::cfg::{CFG, BasicBlock};
use crate::disasm::{Disassembly, Instruction};
use crate::types::{EvmType, TypeCtx, CalldataParam};
use rustc_hash::{FxHashMap, FxHashSet};
use serde::{Deserialize, Serialize};

// ── Symbolic Value (richer than decompiler.rs Sym) ────────────────────────

#[derive(Clone, Debug, PartialEq)]
pub enum Val {
    // Constants
    Const(u64),
    Const256(Vec<u8>),   // full 32-byte constant

    // EVM context
    MsgSender,
    MsgValue,
    MsgData,
    TxOrigin,
    BlockTimestamp,
    BlockNumber,
    BlockCoinbase,
    ChainId,
    SelfBalance,
    Gasleft,
    AddrThis,

    // Calldata
    CalldataLoad { offset: Box<Val> },
    CalldataSize,

    // Storage
    SLoad { slot: Box<Val> },

    // Memory
    MLoad { offset: Box<Val> },
    ReturnData { offset: Box<Val> },

    // Arithmetic — with constant folding
    Add(Box<Val>, Box<Val>),
    Sub(Box<Val>, Box<Val>),
    Mul(Box<Val>, Box<Val>),
    Div(Box<Val>, Box<Val>),
    Mod(Box<Val>, Box<Val>),
    Exp(Box<Val>, Box<Val>),
    AddMod(Box<Val>, Box<Val>, Box<Val>),

    // Bitwise
    And(Box<Val>, Box<Val>),
    Or(Box<Val>,  Box<Val>),
    Xor(Box<Val>, Box<Val>),
    Not(Box<Val>),
    Shl { shift: Box<Val>, value: Box<Val> },
    Shr { shift: Box<Val>, value: Box<Val> },
    Sar { shift: Box<Val>, value: Box<Val> },
    Byte { index: Box<Val>, value: Box<Val> },

    // Comparison
    Lt(Box<Val>, Box<Val>),
    Gt(Box<Val>, Box<Val>),
    Slt(Box<Val>, Box<Val>),
    Sgt(Box<Val>, Box<Val>),
    Eq(Box<Val>,  Box<Val>),
    IsZero(Box<Val>),

    // Hash
    Keccak256 { offset: Box<Val>, length: Box<Val> },

    // Call results
    CallSuccess { gas: Box<Val>, to: Box<Val>, value: Box<Val> },
    StaticCallSuccess { gas: Box<Val>, to: Box<Val> },
    CreateAddr,
    Create2Addr { salt: Box<Val> },

    // Typed param (recovered from ABI decode pattern)
    Param { index: usize, ty: EvmType },

    // Local variable (SSA-like name after assignment)
    Local(String),

    // Phi node for block-merge points
    Phi(Vec<Val>),

    Unknown(String),
}

impl Val {
    /// Constant folding — evaluate if all operands are known constants
    pub fn fold(&self) -> Val {
        match self {
            Val::Add(a, b) => match (a.fold(), b.fold()) {
                (Val::Const(x), Val::Const(y)) => Val::Const(x.wrapping_add(y)),
                (fa, fb) => Val::Add(Box::new(fa), Box::new(fb)),
            },
            Val::Sub(a, b) => match (a.fold(), b.fold()) {
                (Val::Const(x), Val::Const(y)) => Val::Const(x.wrapping_sub(y)),
                (fa, fb) => Val::Sub(Box::new(fa), Box::new(fb)),
            },
            Val::Mul(a, b) => match (a.fold(), b.fold()) {
                (Val::Const(x), Val::Const(y)) => Val::Const(x.wrapping_mul(y)),
                (fa, fb) => Val::Mul(Box::new(fa), Box::new(fb)),
            },
            Val::Div(a, b) => match (a.fold(), b.fold()) {
                (Val::Const(x), Val::Const(y)) if y != 0 => Val::Const(x / y),
                (fa, fb) => Val::Div(Box::new(fa), Box::new(fb)),
            },
            Val::And(a, b) => match (a.fold(), b.fold()) {
                (Val::Const(x), Val::Const(y)) => Val::Const(x & y),
                // AND with address mask (20 bytes = 0xffffffffffffffffffffffffffffffffffffffff)
                (Val::Const(x), fb) if x == 0xffffffffffffffff => Val::And(Box::new(Val::Const(x)), Box::new(fb)),
                (fa, Val::Const(y)) if y == 0 => Val::Const(0),
                (fa, fb) => Val::And(Box::new(fa), Box::new(fb)),
            },
            Val::Or(a, b) => match (a.fold(), b.fold()) {
                (Val::Const(x), Val::Const(y)) => Val::Const(x | y),
                (Val::Const(0), fb) => fb,
                (fa, Val::Const(0)) => fa,
                (fa, fb) => Val::Or(Box::new(fa), Box::new(fb)),
            },
            Val::Shr { shift, value } => match (shift.fold(), value.fold()) {
                (Val::Const(s), Val::Const(v)) => Val::Const(v >> s.min(63)),
                (Val::Const(96), fv) => Val::And(
                    Box::new(fv),
                    Box::new(Val::Unknown("address_mask".into())),
                ), // >> 96 = extract address
                (fs, fv) => Val::Shr { shift: Box::new(fs), value: Box::new(fv) },
            },
            Val::Shl { shift, value } => match (shift.fold(), value.fold()) {
                (Val::Const(s), Val::Const(v)) => Val::Const(v.wrapping_shl(s.min(63) as u32)),
                (fs, fv) => Val::Shl { shift: Box::new(fs), value: Box::new(fv) },
            },
            Val::IsZero(v) => match v.fold() {
                Val::Const(0) => Val::Const(1),
                Val::Const(_) => Val::Const(0),
                fv => Val::IsZero(Box::new(fv)),
            },
            Val::Eq(a, b) => match (a.fold(), b.fold()) {
                (Val::Const(x), Val::Const(y)) => Val::Const(if x == y { 1 } else { 0 }),
                (fa, fb) => Val::Eq(Box::new(fa), Box::new(fb)),
            },
            Val::Lt(a, b) => match (a.fold(), b.fold()) {
                (Val::Const(x), Val::Const(y)) => Val::Const(if x < y { 1 } else { 0 }),
                (fa, fb) => Val::Lt(Box::new(fa), Box::new(fb)),
            },
            Val::Gt(a, b) => match (a.fold(), b.fold()) {
                (Val::Const(x), Val::Const(y)) => Val::Const(if x > y { 1 } else { 0 }),
                (fa, fb) => Val::Gt(Box::new(fa), Box::new(fb)),
            },
            Val::Not(v) => match v.fold() {
                Val::Const(x) => Val::Const(!x),
                fv => Val::Not(Box::new(fv)),
            },
            other => other.clone(),
        }
    }

    /// Render as readable Solidity expression
    pub fn render(&self) -> String {
        match self {
            Val::Const(0)     => "0".into(),
            Val::Const(v)     => {
                // Try to detect common constants
                match v {
                    1 => "1".into(),
                    0xffffffff  => "type(uint32).max".into(),
                    0xffffffffffffffff => "type(uint64).max".into(),
                    _ if *v < 10000 => format!("{v}"),
                    _ => format!("0x{v:x}"),
                }
            }
            Val::Const256(b)  => format!("0x{}", hex::encode(b)),
            Val::MsgSender    => "msg.sender".into(),
            Val::MsgValue     => "msg.value".into(),
            Val::MsgData      => "msg.data".into(),
            Val::TxOrigin     => "tx.origin".into(),
            Val::BlockTimestamp => "block.timestamp".into(),
            Val::BlockNumber  => "block.number".into(),
            Val::BlockCoinbase => "block.coinbase".into(),
            Val::ChainId      => "block.chainid".into(),
            Val::SelfBalance  => "address(this).balance".into(),
            Val::Gasleft      => "gasleft()".into(),
            Val::AddrThis     => "address(this)".into(),

            Val::CalldataLoad { offset } => {
                match offset.as_ref() {
                    Val::Const(0)  => "msg.data[0:32]".into(),
                    Val::Const(4)  => "_param0".into(),
                    Val::Const(36) => "_param1".into(),
                    Val::Const(68) => "_param2".into(),
                    Val::Const(100) => "_param3".into(),
                    Val::Const(n)  => {
                        let idx = (n.saturating_sub(4)) / 32;
                        format!("_param{idx}")
                    }
                    o => format!("calldataload({})", o.render()),
                }
            }
            Val::CalldataSize => "msg.data.length".into(),

            Val::SLoad { slot } => match slot.as_ref() {
                Val::Const(0) => "owner".into(),
                Val::Const(1) => "profitWallet".into(),
                Val::Const(2) => "totalSupply".into(),
                Val::Const(5) => "paused".into(),
                Val::Const(n) => format!("_slot{n}"),
                s => format!("sload({})", s.render()),
            },

            Val::MLoad { offset } => match offset.as_ref() {
                Val::Const(0x40) => "mload(0x40)".into(), // free memory pointer
                o => format!("mload({})", o.render()),
            },

            Val::ReturnData { offset } => format!("returndata[{}]", offset.render()),

            Val::Add(a, b) => {
                // Detect mload(0x40) + n = memory allocation
                if let Val::MLoad { offset } = a.as_ref() {
                    if let Val::Const(0x40) = offset.as_ref() {
                        return format!("(freePtr + {})", b.render());
                    }
                }
                format!("({} + {})", a.render(), b.render())
            }
            Val::Sub(a, b)  => format!("({} - {})", a.render(), b.render()),
            Val::Mul(a, b)  => format!("({} * {})", a.render(), b.render()),
            Val::Div(a, b)  => format!("({} / {})", a.render(), b.render()),
            Val::Mod(a, b)  => format!("({} % {})", a.render(), b.render()),
            Val::Exp(a, b)  => format!("({} ** {})", a.render(), b.render()),
            Val::AddMod(a,b,n) => format!("addmod({},{},{})", a.render(), b.render(), n.render()),

            Val::And(a, b) => {
                // Detect address masking: AND with 0x...ffffffffffffffffffffffffffffffffffffffff
                match (a.as_ref(), b.as_ref()) {
                    (Val::Unknown(s), inner) | (inner, Val::Unknown(s)) if s == "address_mask" =>
                        format!("address({})", inner.render()),
                    _ => format!("({} & {})", a.render(), b.render()),
                }
            }
            Val::Or(a, b)   => format!("({} | {})", a.render(), b.render()),
            Val::Xor(a, b)  => format!("({} ^ {})", a.render(), b.render()),
            Val::Not(v)     => format!("~{}", v.render()),

            Val::Shr { shift, value } => {
                // >> 224 on first calldata word = selector extraction
                if let Val::Const(224) = shift.as_ref() {
                    return "msg.sig".into();
                }
                // >> 96 = address extraction from packed slot
                if let Val::Const(96) = shift.as_ref() {
                    return format!("address({})", value.render());
                }
                format!("({} >> {})", value.render(), shift.render())
            }
            Val::Shl { shift, value } => format!("({} << {})", value.render(), shift.render()),
            Val::Sar { shift, value } => format!("(int256({}) >> {})", value.render(), shift.render()),
            Val::Byte { index, value } => format!("byte({}, {})", index.render(), value.render()),

            Val::Lt(a, b) => format!("({} < {})", a.render(), b.render()),
            Val::Gt(a, b) => format!("({} > {})", a.render(), b.render()),
            Val::Slt(a, b) => format!("(int256({}) < int256({}))", a.render(), b.render()),
            Val::Sgt(a, b) => format!("(int256({}) > int256({}))", a.render(), b.render()),
            Val::Eq(a, b)  => format!("({} == {})", a.render(), b.render()),
            Val::IsZero(v) => {
                // iszero(iszero(x)) = bool(x)
                if let Val::IsZero(inner) = v.as_ref() {
                    return format!("bool({})", inner.render());
                }
                format!("({} == 0)", v.render())
            }

            Val::Keccak256 { offset, length } => {
                match (offset.as_ref(), length.as_ref()) {
                    (Val::Const(0x00), Val::Const(64)) =>
                        "keccak256(abi.encode(_key, _slot))".into(),
                    (o, l) => format!("keccak256(memory[{}..{}+{}])", o.render(), o.render(), l.render()),
                }
            }

            Val::CallSuccess { gas, to, value } => {
                let value_part = match value.as_ref() {
                    Val::Const(0) => String::new(),
                    v => format!(", value: {}", v.render()),
                };
                format!("{}{{gas: {}{}}}.call(abi.encodeWithSelector(...))",
                    to.render(), gas.render(), value_part)
            }
            Val::StaticCallSuccess { gas, to } =>
                format!("{}.staticcall(...)  /* gas: {} */", to.render(), gas.render()),

            Val::CreateAddr  => "new Contract(...)".into(),
            Val::Create2Addr { salt } => format!("new Contract{{salt: {}}}(...)", salt.render()),

            Val::Param { index, ty } =>
                format!("{}", param_name(*index, ty)),

            Val::Local(name)  => name.clone(),
            Val::Phi(vals) => {
                if vals.len() == 1 { return vals[0].render(); }
                format!("φ({})", vals.iter().map(|v| v.render()).collect::<Vec<_>>().join(", "))
            }
            Val::Unknown(s) => s.clone(),
        }
    }

    /// Is this value known to be zero?
    pub fn is_zero(&self) -> bool {
        matches!(self, Val::Const(0))
    }

    /// Is this a constant?
    pub fn as_const(&self) -> Option<u64> {
        if let Val::Const(v) = self { Some(*v) } else { None }
    }
}

fn param_name(index: usize, ty: &EvmType) -> String {
    let prefix = match ty {
        EvmType::Address    => "addr",
        EvmType::Uint(_)    => "amount",
        EvmType::Bool       => "flag",
        EvmType::Bytes(_)   => "data",
        _                   => "param",
    };
    format!("{prefix}{index}")
}

// ── Symbolic Stack ─────────────────────────────────────────────────────────

#[derive(Clone, Debug)]
pub struct SymStack {
    items: Vec<Val>,
}

impl SymStack {
    pub fn new() -> Self { Self { items: Vec::with_capacity(64) } }

    pub fn push(&mut self, v: Val) {
        self.items.push(v.fold());
    }

    pub fn pop(&mut self) -> Val {
        self.items.pop().unwrap_or(Val::Unknown("underflow".into()))
    }

    pub fn peek(&self) -> Option<&Val> { self.items.last() }

    pub fn depth(&self) -> usize { self.items.len() }

    pub fn dup(&mut self, n: usize) {
        let len = self.items.len();
        if len >= n {
            let v = self.items[len - n].clone();
            self.items.push(v);
        } else {
            self.items.push(Val::Unknown(format!("dup{n}_underflow")));
        }
    }

    pub fn swap(&mut self, n: usize) {
        let len = self.items.len();
        if len > n { self.items.swap(len - 1, len - 1 - n); }
    }
}

// ── Statement IR ──────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum Stmt {
    Assign      { lhs: String, rhs: String },
    SStore      { slot: String, value: String },
    MStore      { offset: String, value: String },
    Return      { value: String },
    Revert      { reason: String },
    Stop,
    Require     { cond: String, msg: Option<String> },
    IfGoto      { cond: String, target: usize },
    Goto        { target: usize },
    Emit        { name: String, args: Vec<String> },
    Call        { success_var: String, to: String, value: String, data: String },
    DelegateCall{ success_var: String, to: String, data: String },
    StaticCall  { success_var: String, to: String, data: String },
    SelfDestruct{ recipient: String },
    Comment     (String),
    Label       (usize),   // block id marker
}

impl Stmt {
    pub fn render(&self, indent: &str) -> String {
        match self {
            Stmt::Assign { lhs, rhs }        => format!("{indent}{lhs} = {rhs};"),
            Stmt::SStore { slot, value }      => format!("{indent}// SSTORE: {slot} = {value};"),
            Stmt::MStore { offset, value }    => format!("{indent}// mstore({offset}, {value});"),
            Stmt::Return { value }            => format!("{indent}return {value};"),
            Stmt::Revert { reason }           => format!("{indent}revert({reason});"),
            Stmt::Stop                        => format!("{indent}// STOP"),
            Stmt::Require { cond, msg }       => {
                match msg {
                    Some(m) => format!("{indent}require({cond}, \"{m}\");"),
                    None    => format!("{indent}require({cond});"),
                }
            }
            Stmt::IfGoto { cond, target }     => format!("{indent}if ({cond}) goto block_{target};"),
            Stmt::Goto { target }             => format!("{indent}goto block_{target};"),
            Stmt::Emit { name, args }         => format!("{indent}emit {name}({});", args.join(", ")),
            Stmt::Call { success_var, to, value, data } => {
                if data.is_empty() || data == "0x" {
                    format!("{indent}(bool {success_var},) = {to}.call{{value: {value}}}(\"\");")
                } else {
                    format!("{indent}(bool {success_var},) = {to}.call{{value: {value}}}({data});")
                }
            }
            Stmt::DelegateCall { success_var, to, data } =>
                format!("{indent}(bool {success_var},) = {to}.delegatecall({data});"),
            Stmt::StaticCall { success_var, to, data } =>
                format!("{indent}(bool {success_var},) = {to}.staticcall({data});"),
            Stmt::SelfDestruct { recipient } =>
                format!("{indent}selfdestruct(payable({recipient}));"),
            Stmt::Comment(s)                  => format!("{indent}// {s}"),
            Stmt::Label(id)                   => format!("/* block_{id}: */"),
        }
    }
}

// ── Block Execution Result ─────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct BlockResult {
    pub block_id:   usize,
    pub stmts:      Vec<Stmt>,
    pub stack_out:  SymStack,      // stack state at block exit
    pub params:     Vec<CalldataParam>,
}

// ── Inter-block Symbolic Executor ─────────────────────────────────────────

pub struct SymExec<'a> {
    disasm:  &'a Disassembly,
    cfg:     &'a CFG,
    type_ctx: TypeCtx,
    visited: FxHashSet<usize>,
    results: FxHashMap<usize, BlockResult>,
    var_ctr: usize,
    call_ctr: usize,
}

impl<'a> SymExec<'a> {
    pub fn new(disasm: &'a Disassembly, cfg: &'a CFG) -> Self {
        Self {
            disasm,
            cfg,
            type_ctx: TypeCtx::new(),
            visited:  FxHashSet::default(),
            results:  FxHashMap::default(),
            var_ctr:  0,
            call_ctr: 0,
        }
    }

    fn fresh_var(&mut self) -> String {
        let n = self.var_ctr;
        self.var_ctr += 1;
        format!("_v{n}")
    }

    fn fresh_call(&mut self) -> String {
        let n = self.call_ctr;
        self.call_ctr += 1;
        format!("_ok{n}")
    }

    /// Execute a single block with given input stack, return stmts + output stack
    pub fn exec_block(&mut self, block_id: usize, mut stack: SymStack) -> BlockResult {
        let instrs = &self.disasm.instructions;
        let block  = &self.cfg.blocks[block_id];
        let mut stmts:  Vec<Stmt> = Vec::new();
        let mut params: Vec<CalldataParam> = Vec::new();

        // Track memory writes for require/revert string recovery
        let mut mem: FxHashMap<u64, Val> = FxHashMap::default();
        let mut revert_strings: FxHashMap<usize, String> = FxHashMap::default();

        for &idx in &block.instructions {
            let ins = &instrs[idx];
            let op  = ins.opcode;

            match op {
                0x00 => { stmts.push(Stmt::Stop); break; }

                // ── PUSH ────────────────────────────────────────────────
                0x5f => stack.push(Val::Const(0)),
                0x60..=0x7f => {
                    match ins.imm_u256 {
                        Some(v) => stack.push(Val::Const(v)),
                        None    => {
                            let bytes = hex::decode(ins.imm.as_deref().unwrap_or("")).unwrap_or_default();
                            stack.push(Val::Const256(bytes));
                        }
                    }
                }

                // ── STACK OPS ────────────────────────────────────────────
                0x50 => { stack.pop(); }
                0x80..=0x8f => stack.dup((op - 0x7f) as usize),
                0x90..=0x9f => stack.swap((op - 0x8f) as usize),

                // ── ARITHMETIC ───────────────────────────────────────────
                0x01 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::Add(Box::new(a),Box::new(b))); }
                0x02 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::Mul(Box::new(a),Box::new(b))); }
                0x03 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::Sub(Box::new(a),Box::new(b))); }
                0x04 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::Div(Box::new(a),Box::new(b))); }
                0x05 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::Div(Box::new(a),Box::new(b))); } // sdiv
                0x06 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::Mod(Box::new(a),Box::new(b))); }
                0x08 => { let a=stack.pop(); let b=stack.pop(); let n=stack.pop(); stack.push(Val::AddMod(Box::new(a),Box::new(b),Box::new(n))); }
                0x0a => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::Exp(Box::new(a),Box::new(b))); }
                0x0b => { let _b=stack.pop(); let x=stack.pop(); stack.push(x); } // signextend approx

                // ── BITWISE ──────────────────────────────────────────────
                0x16 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::And(Box::new(a),Box::new(b))); }
                0x17 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::Or(Box::new(a),Box::new(b)));  }
                0x18 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::Xor(Box::new(a),Box::new(b))); }
                0x19 => { let a=stack.pop(); stack.push(Val::Not(Box::new(a))); }
                0x1a => { let i=stack.pop(); let v=stack.pop(); stack.push(Val::Byte{index:Box::new(i),value:Box::new(v)}); }
                0x1b => { let s=stack.pop(); let v=stack.pop(); stack.push(Val::Shl{shift:Box::new(s),value:Box::new(v)}); }
                0x1c => { let s=stack.pop(); let v=stack.pop(); stack.push(Val::Shr{shift:Box::new(s),value:Box::new(v)}); }
                0x1d => { let s=stack.pop(); let v=stack.pop(); stack.push(Val::Sar{shift:Box::new(s),value:Box::new(v)}); }

                // ── COMPARISON ───────────────────────────────────────────
                0x10 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::Lt(Box::new(a),Box::new(b))); }
                0x11 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::Gt(Box::new(a),Box::new(b))); }
                0x12 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::Slt(Box::new(a),Box::new(b))); }
                0x13 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::Sgt(Box::new(a),Box::new(b))); }
                0x14 => { let a=stack.pop(); let b=stack.pop(); stack.push(Val::Eq(Box::new(a),Box::new(b))); }
                0x15 => { let a=stack.pop(); stack.push(Val::IsZero(Box::new(a))); }

                // ── SHA3 ─────────────────────────────────────────────────
                0x20 => {
                    let o=stack.pop(); let l=stack.pop();
                    stack.push(Val::Keccak256{offset:Box::new(o),length:Box::new(l)});
                }

                // ── ENVIRONMENT ──────────────────────────────────────────
                0x30 => stack.push(Val::AddrThis),
                0x31 => { let _a=stack.pop(); stack.push(Val::Unknown("addr.balance".into())); }
                0x32 => stack.push(Val::TxOrigin),
                0x33 => stack.push(Val::MsgSender),
                0x34 => stack.push(Val::MsgValue),
                0x35 => {
                    let offset = stack.pop();
                    // Detect ABI parameter pattern: calldataload(4), calldataload(36), etc.
                    if let Some(off) = offset.as_const() {
                        let param_idx = if off == 0 { 0 } else { ((off.saturating_sub(4)) / 32) as usize };
                        if off >= 4 || off == 0 {
                            self.type_ctx.record_param(param_idx, EvmType::Unknown);
                            params.push(CalldataParam {
                                index: param_idx,
                                offset: off,
                                name: format!("_param{param_idx}"),
                                ty: EvmType::Unknown,
                            });
                        }
                    }
                    stack.push(Val::CalldataLoad{offset: Box::new(offset)});
                }
                0x36 => stack.push(Val::CalldataSize),
                0x37 => { let _d=stack.pop(); let _s=stack.pop(); let _l=stack.pop(); } // calldatacopy
                0x38 => stack.push(Val::Unknown("codesize".into())),
                0x39 => { stack.pop(); stack.pop(); stack.pop(); } // codecopy
                0x3a => stack.push(Val::Unknown("tx.gasprice".into())),
                0x3b => { let _a=stack.pop(); stack.push(Val::Unknown("extcodesize".into())); }
                0x3c => { stack.pop(); stack.pop(); stack.pop(); stack.pop(); } // extcodecopy
                0x3d => stack.push(Val::Unknown("returndatasize".into())),
                0x3e => { stack.pop(); stack.pop(); stack.pop(); } // returndatacopy
                0x3f => { let _a=stack.pop(); stack.push(Val::Unknown("extcodehash".into())); }

                // ── BLOCK ────────────────────────────────────────────────
                0x40 => { let _b=stack.pop(); stack.push(Val::Unknown("blockhash".into())); }
                0x41 => stack.push(Val::BlockCoinbase),
                0x42 => stack.push(Val::BlockTimestamp),
                0x43 => stack.push(Val::BlockNumber),
                0x44 => stack.push(Val::Unknown("block.prevrandao".into())),
                0x45 => stack.push(Val::Unknown("block.gaslimit".into())),
                0x46 => stack.push(Val::ChainId),
                0x47 => stack.push(Val::SelfBalance),
                0x48 => stack.push(Val::Unknown("block.basefee".into())),
                0x49 => { let _i=stack.pop(); stack.push(Val::Unknown("blobhash".into())); }
                0x4a => stack.push(Val::Unknown("block.blobbasefee".into())),

                // ── MEMORY ───────────────────────────────────────────────
                0x51 => {
                    let o = stack.pop();
                    let val = if let Some(off) = o.as_const() {
                        mem.get(&off).cloned().unwrap_or(Val::MLoad{offset:Box::new(o.clone())})
                    } else {
                        Val::MLoad{offset:Box::new(o)}
                    };
                    stack.push(val);
                }
                0x52 => {
                    let o = stack.pop();
                    let v = stack.pop();
                    if let Some(off) = o.as_const() {
                        mem.insert(off, v.clone());
                    }
                    stmts.push(Stmt::MStore { offset: o.render(), value: v.render() });
                }
                0x53 => { let o=stack.pop(); let v=stack.pop();
                    stmts.push(Stmt::MStore { offset: o.render(), value: format!("({} & 0xff)", v.render()) });
                }
                0x59 => stack.push(Val::Unknown("msize".into())),
                0x5e => { stack.pop(); stack.pop(); stack.pop(); } // mcopy

                // ── STORAGE ──────────────────────────────────────────────
                0x54 => {
                    let slot = stack.pop();
                    let slot_str = slot.render();
                    if let Some(s) = slot.as_const() {
                        self.type_ctx.record_sload(s);
                    }
                    stmts.push(Stmt::Comment(format!("SLOAD {slot_str}")));
                    stack.push(Val::SLoad{slot: Box::new(slot)});
                }
                0x55 => {
                    let slot  = stack.pop();
                    let value = stack.pop();
                    if let Some(s) = slot.as_const() {
                        self.type_ctx.record_sstore(s, EvmType::Unknown);
                    }
                    stmts.push(Stmt::SStore { slot: slot.render(), value: value.render() });
                }
                0x5c => { let _s=stack.pop(); stack.push(Val::Unknown("tload".into())); }
                0x5d => { stack.pop(); stack.pop(); } // tstore

                // ── FLOW ─────────────────────────────────────────────────
                0x5a => stack.push(Val::Gasleft),
                0x56 => {
                    let dst = stack.pop();
                    let target = dst.as_const().unwrap_or(0) as usize;
                    stmts.push(Stmt::Goto { target });
                }
                0x57 => {
                    let dst  = stack.pop();
                    let cond = stack.pop();
                    let target = dst.as_const().unwrap_or(0) as usize;
                    let cond_str = cond.render();

                    // Detect require pattern:
                    // ISZERO(condition) → JUMPI to revert block
                    // = require(condition)
                    let is_negated = matches!(cond, Val::IsZero(_));
                    if is_negated {
                        // Extract inner condition
                        if let Val::IsZero(inner) = &cond {
                            // Check if this jumps to a revert block
                            let jumps_to_revert = self.cfg.blocks.get(
                                self.cfg.offset_to_block.iter()
                                    .find(|(off,_)| *off == target)
                                    .map(|(_,bid)| *bid)
                                    .unwrap_or(usize::MAX)
                            ).map(|b| {
                                b.instructions.iter().any(|&i| {
                                    self.disasm.instructions.get(i).map(|ins| ins.opcode == 0xfd).unwrap_or(false)
                                })
                            }).unwrap_or(false);

                            if jumps_to_revert {
                                // Recover revert reason from memory if available
                                let reason = mem.get(&4).map(|v| v.render());
                                stmts.push(Stmt::Require {
                                    cond:  inner.render(),
                                    msg:   reason,
                                });
                                continue;
                            }
                        }
                    }

                    stmts.push(Stmt::IfGoto { cond: cond_str, target });
                }
                0x58 => stack.push(Val::Unknown("pc".into())),
                0x5b => { /* JUMPDEST — no-op */ }

                // ── CALL FAMILY ──────────────────────────────────────────
                0xf1 => {
                    let gas   = stack.pop();
                    let to    = stack.pop();
                    let value = stack.pop();
                    let ofs   = stack.pop();
                    let len   = stack.pop();
                    let _ro   = stack.pop();
                    let _rl   = stack.pop();
                    let var   = self.fresh_call();
                    stmts.push(Stmt::Call {
                        success_var: var.clone(),
                        to:    to.render(),
                        value: value.render(),
                        data:  format!("memory[{}..{}+{}]", ofs.render(), ofs.render(), len.render()),
                    });
                    stack.push(Val::Local(var));
                }
                0xf2 => { // CALLCODE (deprecated)
                    for _ in 0..7 { stack.pop(); }
                    stack.push(Val::Unknown("callcode_result".into()));
                }
                0xf4 => {
                    let gas = stack.pop();
                    let to  = stack.pop();
                    let ofs = stack.pop();
                    let len = stack.pop();
                    let _ro = stack.pop();
                    let _rl = stack.pop();
                    let var = self.fresh_call();
                    stmts.push(Stmt::DelegateCall {
                        success_var: var.clone(),
                        to:   to.render(),
                        data: format!("memory[{}..{}+{}]", ofs.render(), ofs.render(), len.render()),
                    });
                    stack.push(Val::Local(var));
                }
                0xfa => {
                    let gas = stack.pop();
                    let to  = stack.pop();
                    let ofs = stack.pop();
                    let len = stack.pop();
                    let _ro = stack.pop();
                    let _rl = stack.pop();
                    let var = self.fresh_call();
                    stmts.push(Stmt::StaticCall {
                        success_var: var.clone(),
                        to:   to.render(),
                        data: format!("memory[{}..{}+{}]", ofs.render(), ofs.render(), len.render()),
                    });
                    stack.push(Val::Local(var));
                }
                0xf0 => {
                    let _v = stack.pop(); let _o = stack.pop(); let _l = stack.pop();
                    stack.push(Val::CreateAddr);
                }
                0xf5 => {
                    let _v = stack.pop(); let _o = stack.pop(); let _l = stack.pop();
                    let salt = stack.pop();
                    stack.push(Val::Create2Addr { salt: Box::new(salt) });
                }

                // ── RETURN / REVERT ──────────────────────────────────────
                0xf3 => {
                    let ofs = stack.pop();
                    let len = stack.pop();
                    let value = match (ofs.as_const(), len.as_const()) {
                        (Some(0), Some(0)) => "()".into(),
                        (Some(o), _) => {
                            mem.get(&o).map(|v| v.render())
                                .unwrap_or_else(|| format!("memory[{}..{}+{}]", ofs.render(), ofs.render(), len.render()))
                        }
                        _ => format!("memory[{}..{}+{}]", ofs.render(), ofs.render(), len.render()),
                    };
                    stmts.push(Stmt::Return { value });
                    break;
                }
                0xfd => {
                    let ofs = stack.pop();
                    let len = stack.pop();
                    // Try to recover revert reason from memory
                    let reason = match (ofs.as_const(), len.as_const()) {
                        (Some(0), Some(0)) | (Some(_), Some(0)) => "\"\"".into(),
                        (Some(o), _) => {
                            // Try to find error string bytes in mem
                            mem.get(&(o + 4))
                                .map(|v| format!("\"{}\"", v.render()))
                                .unwrap_or_else(|| "/* error */".into())
                        }
                        _ => "/* error */".into(),
                    };
                    stmts.push(Stmt::Revert { reason });
                    break;
                }
                0xff => {
                    let addr = stack.pop();
                    stmts.push(Stmt::SelfDestruct { recipient: addr.render() });
                    break;
                }
                0xfe => {
                    stmts.push(Stmt::Revert { reason: "/* INVALID */".into() });
                    break;
                }

                // ── LOG ──────────────────────────────────────────────────
                0xa0..=0xa4 => {
                    let n_topics = (op - 0xa0) as usize;
                    let ofs  = stack.pop();
                    let _len = stack.pop();
                    let mut topics: Vec<Val> = Vec::new();
                    for _ in 0..n_topics { topics.push(stack.pop()); }

                    // Try to match known event topics
                    let event_name = topics.first().and_then(|t| {
                        if let Val::Const256(ref b) = t {
                            Some(format!("Event_0x{}", &hex::encode(b)[..8]))
                        } else { None }
                    }).unwrap_or_else(|| format!("Log{n_topics}"));

                    let args: Vec<String> = topics.iter()
                        .skip(1)
                        .map(|t| t.render())
                        .chain(std::iter::once(format!("memory[{}]", ofs.render())))
                        .collect();

                    stmts.push(Stmt::Emit { name: event_name, args });
                }

                // ── MISC ─────────────────────────────────────────────────
                _ => {
                    let def = crate::opcodes::lookup(op);
                    // Consume inputs
                    for _ in 0..(def.stack_in.max(0) as usize) { stack.pop(); }
                    // Produce outputs
                    for j in 0..(def.stack_out.max(0) as usize) {
                        stack.push(Val::Unknown(format!("{}_{}", def.mnemonic.to_lowercase(), j)));
                    }
                }
            }
        }

        // Collapse redundant mstore/mload comments that don't affect output
        let stmts = stmts.into_iter().filter(|s| !matches!(s, Stmt::MStore{..})).collect();

        BlockResult { block_id, stmts, stack_out: stack, params }
    }

    /// Execute entire CFG starting from entry, return all block results
    pub fn run(&mut self) -> FxHashMap<usize, BlockResult> {
        // BFS traversal
        let mut queue = std::collections::VecDeque::new();
        queue.push_back((0usize, SymStack::new()));

        while let Some((block_id, stack)) = queue.pop_front() {
            if self.visited.contains(&block_id) { continue; }
            self.visited.insert(block_id);

            let result = self.exec_block(block_id, stack.clone());
            let successors = self.cfg.blocks[block_id].successors.clone();

            // Pass output stack to each successor
            for &succ in &successors {
                if !self.visited.contains(&succ) {
                    queue.push_back((succ, result.stack_out.clone()));
                }
            }

            self.results.insert(block_id, result);
        }

        std::mem::take(&mut self.results)
    }

    pub fn type_ctx(self) -> TypeCtx { self.type_ctx }
}
