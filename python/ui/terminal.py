import os
from typing import List, Dict, Any

class C:
    RESET    = "\033[0m"
    BOLD     = "\033[1m"
    DIM      = "\033[2m"
    RED      = "\033[31m"
    GREEN    = "\033[32m"
    YELLOW   = "\033[33m"
    BLUE     = "\033[34m"
    MAGENTA  = "\033[35m"
    CYAN     = "\033[36m"
    WHITE    = "\033[37m"
    BGREEN   = "\033[92m"
    BYELLOW  = "\033[93m"
    BBLUE    = "\033[94m"
    BMAGENTA = "\033[95m"
    BCYAN    = "\033[96m"
    BWHITE   = "\033[97m"
    BG_BLACK = "\033[40m"
    BG_BLUE  = "\033[44m"
    BG_CYAN  = "\033[46m"
    BG_GREEN = "\033[42m"

def clear():
    os.system("clear" if os.name != "nt" else "cls")

def banner():
    print(f"""
{C.CYAN}{C.BOLD}
     ██╗██████╗ ██╗
     ██║██╔══██╗██║
     ██║██║  ██║██║
██   ██║██║  ██║██║
╚█████╔╝██████╔╝███████╗
 ╚════╝ ╚═════╝ ╚══════╝
{C.RESET}{C.BYELLOW}         Just Decentralized Liquidity v4.0
{C.DIM}         Real Cross-DEX Arbitrage | Flash Loans | Yield AI{C.RESET}
{C.CYAN}═══════════════════════════════════════════════════════{C.RESET}
""")

def print_table(headers: List[str], data: List[List[Any]], title: str = "", max_width: int = 80):
    if not data:
        print(f"\n{title}: No data to display.")
        return

    col_widths = [len(h) for h in headers]
    for row in data:
        for i, item in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(item)))

    total_width = sum(col_widths) + len(col_widths) * 3 + 1 # +3 for ' | ' and +1 for final '|'
    if total_width > max_width:
        # Simple truncation for now, can be improved
        pass

    print(f"\n{C.BOLD}{title}{C.RESET}")
    print("─" * total_width)
    print(" | ".join(C.BOLD + h.ljust(w) + C.RESET for h, w in zip(headers, col_widths)) + " |")
    print("─" * total_width)
    for row in data:
        print(" | ".join(str(item).ljust(w) for item, w in zip(row, col_widths)) + " |")
    print("─" * total_width)
