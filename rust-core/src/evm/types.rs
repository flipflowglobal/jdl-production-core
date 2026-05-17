// src/types.rs — EVM Type Inference
//
// Infers Solidity types from how values are used:
//   - Compared with known selector constants → bytes4
//   - Used as CALL target → address
//   - Compared with msg.sender → address
//   - SHR 96 applied → address (from bytes32 packing)
//   - Passed to ERC-20 transfer → (address, uint256)
//   - Boolean: result of EQ/LT/GT/ISZERO used in JUMPI
//   - SSTORE slot 0 of known layout → named field

use serde::{Deserialize, Serialize};
use rustc_hash::FxHashMap;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum EvmType {
    Address,
    Uint(u16),       // uint8..uint256
    Int(u16),        // int8..int256
    Bool,
    Bytes(u8),       // bytes1..bytes32
    BytesDynamic,
    StringType,
    Mapping(Box<EvmType>, Box<EvmType>),
    Array(Box<EvmType>),
    Unknown,
}

impl EvmType {
    pub fn solidity_name(&self) -> String {
        match self {
            Self::Address          => "address".into(),
            Self::Uint(n)          => format!("uint{n}"),
            Self::Int(n)           => format!("int{n}"),
            Self::Bool             => "bool".into(),
            Self::Bytes(n)         => format!("bytes{n}"),
            Self::BytesDynamic     => "bytes".into(),
            Self::StringType       => "string".into(),
            Self::Mapping(k, v)    => format!("mapping({} => {})", k.solidity_name(), v.solidity_name()),
            Self::Array(t)         => format!("{}[]", t.solidity_name()),
            Self::Unknown          => "uint256".into(), // safest default
        }
    }

    /// Infer type from a SHR shift amount (common Solidity packing patterns)
    pub fn from_shr_shift(shift: u64) -> Option<Self> {
        match shift {
            96  => Some(Self::Address),   // >> 96 extracts address from bytes32
            224 => Some(Self::Bytes(4)),  // >> 224 extracts selector
            248 => Some(Self::Bytes(1)),
            240 => Some(Self::Bytes(2)),
            232 => Some(Self::Bytes(3)),
            _   => None,
        }
    }

    /// Infer type from AND mask (common Solidity ABI decoding)
    pub fn from_and_mask(mask: &str) -> Option<Self> {
        // Count leading zeros in mask to determine type width
        let mask_clean = mask.trim_start_matches('0');
        let nibbles = mask_clean.len();
        match nibbles {
            0       => Some(Self::Bool),   // mask 0x0
            2       => Some(Self::Uint(8)),
            4       => Some(Self::Uint(16)),
            8       => Some(Self::Uint(32)),
            10      => Some(Self::Uint(40)),
            40      => Some(Self::Address), // 20 bytes
            64      => Some(Self::Uint(256)),
            _       => None,
        }
    }
}

/// Named storage variable recovered from slot analysis
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StorageVar {
    pub slot:    u64,
    pub name:    String,
    pub ty:      EvmType,
    pub reads:   usize,
    pub writes:  usize,
}

/// Calldata parameter recovered from ABI decoding pattern
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub struct CalldataParam {
    pub index:   usize,    // 0-based parameter index
    pub offset:  u64,      // calldata byte offset (4 + index*32)
    pub name:    String,
    pub ty:      EvmType,
}

/// Per-function type context built during symbolic execution
#[derive(Debug, Default)]
pub struct TypeCtx {
    /// slot -> (type, reads, writes)
    pub storage: FxHashMap<u64, (EvmType, usize, usize)>,
    /// param index -> type
    pub params:  FxHashMap<usize, EvmType>,
    /// local variable counter for naming
    pub var_ctr: usize,
}

impl TypeCtx {
    pub fn new() -> Self { Self::default() }

    pub fn record_sload(&mut self, slot: u64) {
        let e = self.storage.entry(slot).or_insert((EvmType::Unknown, 0, 0));
        e.1 += 1;
    }

    pub fn record_sstore(&mut self, slot: u64, ty: EvmType) {
        let e = self.storage.entry(slot).or_insert((EvmType::Unknown, 0, 0));
        if e.0 == EvmType::Unknown { e.0 = ty; }
        e.2 += 1;
    }

    pub fn record_param(&mut self, index: usize, ty: EvmType) {
        self.params.entry(index).or_insert(ty);
    }

    pub fn fresh_var(&mut self, prefix: &str) -> String {
        let n = self.var_ctr;
        self.var_ctr += 1;
        format!("{prefix}{n}")
    }

    pub fn to_storage_vars(&self) -> Vec<StorageVar> {
        // Common storage layout for Ownable/ERC-20/Aave contracts
        let slot_names: FxHashMap<u64, (&str, EvmType)> = [
            (0, ("owner",        EvmType::Address)),
            (1, ("profitWallet", EvmType::Address)),
            (2, ("totalSupply",  EvmType::Uint(256))),
            (3, ("balances",     EvmType::Mapping(Box::new(EvmType::Address), Box::new(EvmType::Uint(256))))),
            (4, ("allowances",   EvmType::Mapping(Box::new(EvmType::Address), Box::new(EvmType::Mapping(Box::new(EvmType::Address), Box::new(EvmType::Uint(256))))))),
            (5, ("paused",       EvmType::Bool)),
            (6, ("name",         EvmType::StringType)),
            (7, ("symbol",       EvmType::StringType)),
            (8, ("decimals",     EvmType::Uint(8))),
        ].iter().cloned().collect();

        let mut vars: Vec<StorageVar> = self.storage.iter().map(|(&slot, (ty, r, w))| {
            let (name, inferred_ty) = slot_names.get(&slot)
                .map(|(n, t)| (n.to_string(), t.clone()))
                .unwrap_or_else(|| (format!("_slot{slot}"), ty.clone()));
            StorageVar {
                slot,
                name,
                ty: if *ty == EvmType::Unknown { inferred_ty } else { ty.clone() },
                reads:  *r,
                writes: *w,
            }
        }).collect();
        vars.sort_by_key(|v| v.slot);
        vars
    }
}
