// src/opcodes.rs — Complete EVM Opcode Table
// Covers: Frontier → Cancun (EIP-4844 / PUSH0)

use std::fmt;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Opcode {
    pub code:        u8,
    pub mnemonic:    &'static str,
    pub stack_in:    i8,   // items consumed
    pub stack_out:   i8,   // items produced
    pub imm_bytes:   u8,   // immediate data bytes after opcode
    pub category:    OpcodeCategory,
    pub description: &'static str,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum OpcodeCategory {
    Stop,
    Arithmetic,
    Comparison,
    Bitwise,
    Sha3,
    EnvInfo,
    BlockInfo,
    Stack,      // PUSH/DUP/SWAP/POP
    Memory,
    Storage,
    Flow,       // JUMP/JUMPI/PC/GAS
    Log,
    System,     // CREATE/CALL/RETURN/etc
    Invalid,
}

impl fmt::Display for OpcodeCategory {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let s = match self {
            Self::Stop       => "STOP",
            Self::Arithmetic => "ARITHMETIC",
            Self::Comparison => "COMPARISON",
            Self::Bitwise    => "BITWISE",
            Self::Sha3       => "HASH",
            Self::EnvInfo    => "ENVIRONMENT",
            Self::BlockInfo  => "BLOCK",
            Self::Stack      => "STACK",
            Self::Memory     => "MEMORY",
            Self::Storage    => "STORAGE",
            Self::Flow       => "FLOW",
            Self::Log        => "LOG",
            Self::System     => "SYSTEM",
            Self::Invalid    => "INVALID",
        };
        write!(f, "{s}")
    }
}

macro_rules! op {
    ($code:expr, $mn:expr, $sin:expr, $sout:expr, $imm:expr, $cat:expr, $desc:expr) => {
        Opcode {
            code: $code, mnemonic: $mn, stack_in: $sin,
            stack_out: $sout, imm_bytes: $imm, category: $cat,
            description: $desc,
        }
    };
}

use OpcodeCategory::*;

/// Returns the opcode definition for a given byte, or INVALID.
pub fn lookup(byte: u8) -> Opcode {
    match byte {
        // ── Stop & Arithmetic ───────────────────────────────────────────────
        0x00 => op!(0x00,"STOP",      0,0,0, Stop,       "Halt execution"),
        0x01 => op!(0x01,"ADD",       2,1,0, Arithmetic, "a + b"),
        0x02 => op!(0x02,"MUL",       2,1,0, Arithmetic, "a * b"),
        0x03 => op!(0x03,"SUB",       2,1,0, Arithmetic, "a - b"),
        0x04 => op!(0x04,"DIV",       2,1,0, Arithmetic, "a / b (integer)"),
        0x05 => op!(0x05,"SDIV",      2,1,0, Arithmetic, "signed div"),
        0x06 => op!(0x06,"MOD",       2,1,0, Arithmetic, "a % b"),
        0x07 => op!(0x07,"SMOD",      2,1,0, Arithmetic, "signed mod"),
        0x08 => op!(0x08,"ADDMOD",    3,1,0, Arithmetic, "(a+b) % N"),
        0x09 => op!(0x09,"MULMOD",    3,1,0, Arithmetic, "(a*b) % N"),
        0x0a => op!(0x0a,"EXP",       2,1,0, Arithmetic, "a ** b"),
        0x0b => op!(0x0b,"SIGNEXTEND",2,1,0, Arithmetic, "sign extend"),
        // ── Comparison ─────────────────────────────────────────────────────
        0x10 => op!(0x10,"LT",        2,1,0, Comparison, "a < b"),
        0x11 => op!(0x11,"GT",        2,1,0, Comparison, "a > b"),
        0x12 => op!(0x12,"SLT",       2,1,0, Comparison, "signed lt"),
        0x13 => op!(0x13,"SGT",       2,1,0, Comparison, "signed gt"),
        0x14 => op!(0x14,"EQ",        2,1,0, Comparison, "a == b"),
        0x15 => op!(0x15,"ISZERO",    1,1,0, Comparison, "a == 0"),
        // ── Bitwise ────────────────────────────────────────────────────────
        0x16 => op!(0x16,"AND",       2,1,0, Bitwise,    "bitwise and"),
        0x17 => op!(0x17,"OR",        2,1,0, Bitwise,    "bitwise or"),
        0x18 => op!(0x18,"XOR",       2,1,0, Bitwise,    "bitwise xor"),
        0x19 => op!(0x19,"NOT",       1,1,0, Bitwise,    "bitwise not"),
        0x1a => op!(0x1a,"BYTE",      2,1,0, Bitwise,    "byte i of x"),
        0x1b => op!(0x1b,"SHL",       2,1,0, Bitwise,    "shift left"),
        0x1c => op!(0x1c,"SHR",       2,1,0, Bitwise,    "shift right"),
        0x1d => op!(0x1d,"SAR",       2,1,0, Bitwise,    "arithmetic shift right"),
        // ── SHA3 ───────────────────────────────────────────────────────────
        0x20 => op!(0x20,"KECCAK256", 2,1,0, Sha3,       "keccak256(mem[ofs..ofs+len])"),
        // ── Environment ────────────────────────────────────────────────────
        0x30 => op!(0x30,"ADDRESS",   0,1,0, EnvInfo,    "address of current contract"),
        0x31 => op!(0x31,"BALANCE",   1,1,0, EnvInfo,    "balance of addr"),
        0x32 => op!(0x32,"ORIGIN",    0,1,0, EnvInfo,    "tx origin"),
        0x33 => op!(0x33,"CALLER",    0,1,0, EnvInfo,    "msg.sender"),
        0x34 => op!(0x34,"CALLVALUE", 0,1,0, EnvInfo,    "msg.value"),
        0x35 => op!(0x35,"CALLDATALOAD",1,1,0,EnvInfo,   "calldata[i..i+32]"),
        0x36 => op!(0x36,"CALLDATASIZE",0,1,0,EnvInfo,   "len(calldata)"),
        0x37 => op!(0x37,"CALLDATACOPY",3,0,0,EnvInfo,   "copy calldata to mem"),
        0x38 => op!(0x38,"CODESIZE",  0,1,0, EnvInfo,    "len(code)"),
        0x39 => op!(0x39,"CODECOPY",  3,0,0, EnvInfo,    "copy code to mem"),
        0x3a => op!(0x3a,"GASPRICE",  0,1,0, EnvInfo,    "tx.gasprice"),
        0x3b => op!(0x3b,"EXTCODESIZE",1,1,0,EnvInfo,    "len(code[addr])"),
        0x3c => op!(0x3c,"EXTCODECOPY",4,0,0,EnvInfo,    "copy ext code to mem"),
        0x3d => op!(0x3d,"RETURNDATASIZE",0,1,0,EnvInfo, "len(returndata)"),
        0x3e => op!(0x3e,"RETURNDATACOPY",3,0,0,EnvInfo, "copy returndata to mem"),
        0x3f => op!(0x3f,"EXTCODEHASH",1,1,0,EnvInfo,    "keccak256(code[addr])"),
        // ── Block ──────────────────────────────────────────────────────────
        0x40 => op!(0x40,"BLOCKHASH", 1,1,0, BlockInfo,  "hash of block N"),
        0x41 => op!(0x41,"COINBASE",  0,1,0, BlockInfo,  "block.coinbase"),
        0x42 => op!(0x42,"TIMESTAMP", 0,1,0, BlockInfo,  "block.timestamp"),
        0x43 => op!(0x43,"NUMBER",    0,1,0, BlockInfo,  "block.number"),
        0x44 => op!(0x44,"PREVRANDAO",0,1,0, BlockInfo,  "block difficulty / randao"),
        0x45 => op!(0x45,"GASLIMIT",  0,1,0, BlockInfo,  "block.gaslimit"),
        0x46 => op!(0x46,"CHAINID",   0,1,0, BlockInfo,  "chain id"),
        0x47 => op!(0x47,"SELFBALANCE",0,1,0,BlockInfo,  "balance of self"),
        0x48 => op!(0x48,"BASEFEE",   0,1,0, BlockInfo,  "block.basefee (EIP-1559)"),
        0x49 => op!(0x49,"BLOBHASH",  1,1,0, BlockInfo,  "blobhash(i) EIP-4844"),
        0x4a => op!(0x4a,"BLOBBASEFEE",0,1,0,BlockInfo,  "blob base fee EIP-4844"),
        // ── Stack ──────────────────────────────────────────────────────────
        0x50 => op!(0x50,"POP",       1,0,0, Stack,      "discard top of stack"),
        0x5f => op!(0x5f,"PUSH0",     0,1,0, Stack,      "push 0 (EIP-3855)"),
        // ── Memory ─────────────────────────────────────────────────────────
        0x51 => op!(0x51,"MLOAD",     1,1,0, Memory,     "mem[ofs..ofs+32]"),
        0x52 => op!(0x52,"MSTORE",    2,0,0, Memory,     "mem[ofs..ofs+32] = val"),
        0x53 => op!(0x53,"MSTORE8",   2,0,0, Memory,     "mem[ofs] = val & 0xff"),
        0x59 => op!(0x59,"MSIZE",     0,1,0, Memory,     "memory size in bytes"),
        0x5a => op!(0x5a,"GAS",       0,1,0, Flow,       "remaining gas"),
        // ── Storage ────────────────────────────────────────────────────────
        0x54 => op!(0x54,"SLOAD",     1,1,0, Storage,    "storage[key]"),
        0x55 => op!(0x55,"SSTORE",    2,0,0, Storage,    "storage[key] = val"),
        0x5c => op!(0x5c,"TLOAD",     1,1,0, Storage,    "transient storage load (EIP-1153)"),
        0x5d => op!(0x5d,"TSTORE",    2,0,0, Storage,    "transient storage store"),
        // ── Flow ───────────────────────────────────────────────────────────
        0x56 => op!(0x56,"JUMP",      1,0,0, Flow,       "unconditional jump"),
        0x57 => op!(0x57,"JUMPI",     2,0,0, Flow,       "conditional jump"),
        0x58 => op!(0x58,"PC",        0,1,0, Flow,       "program counter"),
        0x5b => op!(0x5b,"JUMPDEST",  0,0,0, Flow,       "valid jump destination"),
        0x5e => op!(0x5e,"MCOPY",     3,0,0, Memory,     "memory copy EIP-5656"),
        // ── PUSH1-PUSH32 ───────────────────────────────────────────────────
        0x60 => op!(0x60,"PUSH1",     0,1,1,  Stack, "push 1 byte"),
        0x61 => op!(0x61,"PUSH2",     0,1,2,  Stack, "push 2 bytes"),
        0x62 => op!(0x62,"PUSH3",     0,1,3,  Stack, "push 3 bytes"),
        0x63 => op!(0x63,"PUSH4",     0,1,4,  Stack, "push 4 bytes"),
        0x64 => op!(0x64,"PUSH5",     0,1,5,  Stack, "push 5 bytes"),
        0x65 => op!(0x65,"PUSH6",     0,1,6,  Stack, "push 6 bytes"),
        0x66 => op!(0x66,"PUSH7",     0,1,7,  Stack, "push 7 bytes"),
        0x67 => op!(0x67,"PUSH8",     0,1,8,  Stack, "push 8 bytes"),
        0x68 => op!(0x68,"PUSH9",     0,1,9,  Stack, "push 9 bytes"),
        0x69 => op!(0x69,"PUSH10",    0,1,10, Stack, "push 10 bytes"),
        0x6a => op!(0x6a,"PUSH11",    0,1,11, Stack, "push 11 bytes"),
        0x6b => op!(0x6b,"PUSH12",    0,1,12, Stack, "push 12 bytes"),
        0x6c => op!(0x6c,"PUSH13",    0,1,13, Stack, "push 13 bytes"),
        0x6d => op!(0x6d,"PUSH14",    0,1,14, Stack, "push 14 bytes"),
        0x6e => op!(0x6e,"PUSH15",    0,1,15, Stack, "push 15 bytes"),
        0x6f => op!(0x6f,"PUSH16",    0,1,16, Stack, "push 16 bytes"),
        0x70 => op!(0x70,"PUSH17",    0,1,17, Stack, "push 17 bytes"),
        0x71 => op!(0x71,"PUSH18",    0,1,18, Stack, "push 18 bytes"),
        0x72 => op!(0x72,"PUSH19",    0,1,19, Stack, "push 19 bytes"),
        0x73 => op!(0x73,"PUSH20",    0,1,20, Stack, "push 20 bytes (address)"),
        0x74 => op!(0x74,"PUSH21",    0,1,21, Stack, "push 21 bytes"),
        0x75 => op!(0x75,"PUSH22",    0,1,22, Stack, "push 22 bytes"),
        0x76 => op!(0x76,"PUSH23",    0,1,23, Stack, "push 23 bytes"),
        0x77 => op!(0x77,"PUSH24",    0,1,24, Stack, "push 24 bytes"),
        0x78 => op!(0x78,"PUSH25",    0,1,25, Stack, "push 25 bytes"),
        0x79 => op!(0x79,"PUSH26",    0,1,26, Stack, "push 26 bytes"),
        0x7a => op!(0x7a,"PUSH27",    0,1,27, Stack, "push 27 bytes"),
        0x7b => op!(0x7b,"PUSH28",    0,1,28, Stack, "push 28 bytes"),
        0x7c => op!(0x7c,"PUSH29",    0,1,29, Stack, "push 29 bytes"),
        0x7d => op!(0x7d,"PUSH30",    0,1,30, Stack, "push 30 bytes"),
        0x7e => op!(0x7e,"PUSH31",    0,1,31, Stack, "push 31 bytes"),
        0x7f => op!(0x7f,"PUSH32",    0,1,32, Stack, "push 32 bytes"),
        // ── DUP1-DUP16 ─────────────────────────────────────────────────────
        0x80 => op!(0x80,"DUP1",  1,2,0, Stack, "dup stack[0]"),
        0x81 => op!(0x81,"DUP2",  2,3,0, Stack, "dup stack[1]"),
        0x82 => op!(0x82,"DUP3",  3,4,0, Stack, "dup stack[2]"),
        0x83 => op!(0x83,"DUP4",  4,5,0, Stack, "dup stack[3]"),
        0x84 => op!(0x84,"DUP5",  5,6,0, Stack, "dup stack[4]"),
        0x85 => op!(0x85,"DUP6",  6,7,0, Stack, "dup stack[5]"),
        0x86 => op!(0x86,"DUP7",  7,8,0, Stack, "dup stack[6]"),
        0x87 => op!(0x87,"DUP8",  8,9,0, Stack, "dup stack[7]"),
        0x88 => op!(0x88,"DUP9",  9,10,0,Stack, "dup stack[8]"),
        0x89 => op!(0x89,"DUP10",10,11,0,Stack, "dup stack[9]"),
        0x8a => op!(0x8a,"DUP11",11,12,0,Stack, "dup stack[10]"),
        0x8b => op!(0x8b,"DUP12",12,13,0,Stack, "dup stack[11]"),
        0x8c => op!(0x8c,"DUP13",13,14,0,Stack, "dup stack[12]"),
        0x8d => op!(0x8d,"DUP14",14,15,0,Stack, "dup stack[13]"),
        0x8e => op!(0x8e,"DUP15",15,16,0,Stack, "dup stack[14]"),
        0x8f => op!(0x8f,"DUP16",16,17,0,Stack, "dup stack[15]"),
        // ── SWAP1-SWAP16 ───────────────────────────────────────────────────
        0x90 => op!(0x90,"SWAP1",  2,2,0, Stack, "swap stack[0] <-> stack[1]"),
        0x91 => op!(0x91,"SWAP2",  3,3,0, Stack, "swap stack[0] <-> stack[2]"),
        0x92 => op!(0x92,"SWAP3",  4,4,0, Stack, "swap stack[0] <-> stack[3]"),
        0x93 => op!(0x93,"SWAP4",  5,5,0, Stack, "swap stack[0] <-> stack[4]"),
        0x94 => op!(0x94,"SWAP5",  6,6,0, Stack, "swap stack[0] <-> stack[5]"),
        0x95 => op!(0x95,"SWAP6",  7,7,0, Stack, "swap stack[0] <-> stack[6]"),
        0x96 => op!(0x96,"SWAP7",  8,8,0, Stack, "swap stack[0] <-> stack[7]"),
        0x97 => op!(0x97,"SWAP8",  9,9,0, Stack, "swap stack[0] <-> stack[8]"),
        0x98 => op!(0x98,"SWAP9", 10,10,0,Stack, "swap stack[0] <-> stack[9]"),
        0x99 => op!(0x99,"SWAP10",11,11,0,Stack, "swap stack[0] <-> stack[10]"),
        0x9a => op!(0x9a,"SWAP11",12,12,0,Stack, "swap stack[0] <-> stack[11]"),
        0x9b => op!(0x9b,"SWAP12",13,13,0,Stack, "swap stack[0] <-> stack[12]"),
        0x9c => op!(0x9c,"SWAP13",14,14,0,Stack, "swap stack[0] <-> stack[13]"),
        0x9d => op!(0x9d,"SWAP14",15,15,0,Stack, "swap stack[0] <-> stack[14]"),
        0x9e => op!(0x9e,"SWAP15",16,16,0,Stack, "swap stack[0] <-> stack[15]"),
        0x9f => op!(0x9f,"SWAP16",17,17,0,Stack, "swap stack[0] <-> stack[16]"),
        // ── LOG0-LOG4 ──────────────────────────────────────────────────────
        0xa0 => op!(0xa0,"LOG0",2,0,0, Log, "emit log0(ofs,len)"),
        0xa1 => op!(0xa1,"LOG1",3,0,0, Log, "emit log1(ofs,len,t0)"),
        0xa2 => op!(0xa2,"LOG2",4,0,0, Log, "emit log2(ofs,len,t0,t1)"),
        0xa3 => op!(0xa3,"LOG3",5,0,0, Log, "emit log3(ofs,len,t0,t1,t2)"),
        0xa4 => op!(0xa4,"LOG4",6,0,0, Log, "emit log4(ofs,len,t0,t1,t2,t3)"),
        // ── System ─────────────────────────────────────────────────────────
        0xf0 => op!(0xf0,"CREATE",      3,1,0, System, "create(value,ofs,len)"),
        0xf1 => op!(0xf1,"CALL",        7,1,0, System, "call(gas,addr,val,…)"),
        0xf2 => op!(0xf2,"CALLCODE",    7,1,0, System, "callcode (deprecated)"),
        0xf3 => op!(0xf3,"RETURN",      2,0,0, System, "return mem[ofs..ofs+len]"),
        0xf4 => op!(0xf4,"DELEGATECALL",6,1,0, System, "delegatecall"),
        0xf5 => op!(0xf5,"CREATE2",     4,1,0, System, "create2 deterministic"),
        0xfa => op!(0xfa,"STATICCALL",  6,1,0, System, "staticcall"),
        0xfd => op!(0xfd,"REVERT",      2,0,0, System, "revert mem[ofs..ofs+len]"),
        0xfe => op!(0xfe,"INVALID",     0,0,0, Invalid,"designated invalid"),
        0xff => op!(0xff,"SELFDESTRUCT",1,0,0, System, "selfdestruct(addr)"),
        // ── EOF (EIP-3540) placeholders ────────────────────────────────────
        0xe0 => op!(0xe0,"RJUMP",       0,0,2, Flow,   "relative jump EOF"),
        0xe1 => op!(0xe1,"RJUMPI",      1,0,2, Flow,   "relative cond jump EOF"),
        0xe2 => op!(0xe2,"RJUMPV",      1,0,1, Flow,   "relative jump table EOF"),
        // ── Catch-all ──────────────────────────────────────────────────────
        _ => op!(byte, "UNKNOWN", 0,0,0, Invalid, "unknown opcode"),
    }
}

/// Returns true if this byte is a PUSH opcode (0x60–0x7f)
pub fn is_push(byte: u8) -> bool {
    (0x60..=0x7f).contains(&byte)
}

/// Immediate byte count for a PUSH opcode
pub fn push_size(byte: u8) -> u8 {
    if is_push(byte) { byte - 0x5f } else { 0 }
}
