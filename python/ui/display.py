from typing import List, Any

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

def print_status(msg: str, status: str, color: str = C.GREEN):
    print(f"[{color}{status}{C.RESET}] {msg}")

def print_error(msg: str):
    print(f"[{C.RED}ERROR{C.RESET}] {msg}")

def print_warning(msg: str):
    print(f"[{C.YELLOW}WARN{C.RESET}] {msg}")

def print_info(msg: str):
    print(f"[{C.CYAN}INFO{C.RESET}] {msg}")

def print_success(msg: str):
    print(f"[{C.GREEN}SUCCESS{C.RESET}] {msg}")

def print_header(msg: str):
    print(f"\n{C.BOLD}{C.BLUE}--- {msg} ---{C.RESET}")

def print_subheader(msg: str):
    print(f"\n{C.BOLD}{C.CYAN}--- {msg} ---{C.RESET}")

def print_item(label: str, value: Any, color: str = C.WHITE):
    print(f"{C.BOLD}{label}:{C.RESET} {color}{value}{C.RESET}")

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
