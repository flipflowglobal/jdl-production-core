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

#[cfg(test)]
mod tests {
    use super::{disasm, security, signatures};

    /// Disassemble PUSH1 0x01 ; PUSH1 0x02 ; STOP → 3 instructions with the
    /// expected mnemonics; PUSH immediates are decoded, and total_bytes is exact.
    #[test]
    fn disassemble_decodes_pushes_and_stop() {
        let bytes = [0x60, 0x01, 0x60, 0x02, 0x00];
        let d = disasm::disassemble(&bytes);
        assert_eq!(d.instruction_count, 3);
        assert_eq!(d.total_bytes, 5);
        let mnems: Vec<&str> = d.instructions.iter().map(|i| i.mnemonic.as_str()).collect();
        assert_eq!(mnems, ["PUSH1", "PUSH1", "STOP"]);
        assert_eq!(d.instructions[0].imm_u256, Some(1));
        assert_eq!(d.instructions[1].imm_u256, Some(2));
    }

    /// A lone SELFDESTRUCT (0xff) must set has_selfdestruct.
    #[test]
    fn analyze_security_flags_selfdestruct() {
        let d = disasm::disassemble(&[0x60, 0x00, 0xff]); // PUSH1 0x00 ; SELFDESTRUCT
        let sec = security::analyze_security(&d);
        assert!(sec.has_selfdestruct, "0xff should be detected as SELFDESTRUCT");
    }

    /// Bytecode without any dangerous opcodes must NOT flag has_selfdestruct.
    #[test]
    fn analyze_security_clean_bytecode() {
        let d = disasm::disassemble(&[0x60, 0x01, 0x60, 0x02, 0x01, 0x00]); // ADD ; STOP
        let sec = security::analyze_security(&d);
        assert!(!sec.has_selfdestruct);
        assert!(!sec.has_delegatecall);
    }

    /// A dispatcher fragment PUSH4 0xa9059cbb ; EQ ; PUSH1 0x10 ; JUMPI must
    /// surface the ERC20 transfer selector with its known name.
    #[test]
    fn recover_signatures_surfaces_transfer_selector() {
        // 63 a9059cbb  14  60 10  57
        let bytes = [0x63, 0xa9, 0x05, 0x9c, 0xbb, 0x14, 0x60, 0x10, 0x57];
        let d = disasm::disassemble(&bytes);
        let sigs = signatures::recover_signatures(&d);
        let f = sigs
            .functions
            .iter()
            .find(|f| f.selector_u32 == 0xa9059cbb)
            .expect("transfer selector should be recovered");
        assert_eq!(f.selector, "0xa9059cbb");
        assert_eq!(f.known_name.as_deref(), Some("transfer(address,uint256)"));
    }
}
