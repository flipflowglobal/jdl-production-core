// Recovered EVM analysis engine. The full decompiler + symbolic-execution pipeline
// is restored; the high-level `analyze_bytecode` currently wires disasm + security +
// signatures, so some deeper helpers are not yet called from the library API. They
// are kept intact (not dead) for the decompiler/symbolic path — allow dead_code so
// the recovered code stays warning-clean until it's fully surfaced.
#![allow(dead_code)]

pub mod opcodes;
pub mod disasm;
pub mod cfg;
pub mod types;
pub mod symbolic;
pub mod security;
pub mod signatures;
pub mod decompiler;
