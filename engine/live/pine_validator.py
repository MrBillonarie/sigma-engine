#!/usr/bin/env python3
"""
pine_validator.py — Validador estatico de Pine Script v5/v6 para SIGMA Engine.

Detecta los errores mas comunes SIN necesitar TradingView:
  CE10272 — variable usada pero no declarada
  CW10013 — variable local con = en bloque if/for (deberia ser :=)
  CW10002 — ta.lowest()/ta.highest() dentro de expresion condicional

Uso:
  python3 pine_validator.py                        # valida motor1 + motor2
  python3 pine_validator.py --file ruta/script.pine
  python3 pine_validator.py --motor 2              # solo motor 2
"""
import re, sys, json, argparse
from pathlib import Path
from datetime import datetime

PINE_DIR  = Path("/opt/sigma/results/pine_scripts")
MOTOR1_TF = ["1m","5m","15m","1h","4h"]   # Motor 1: archivos por TF
MOTOR2    = PINE_DIR / "SIGMA_v13_COMPLETO.pine"

# Colores ANSI
R = "\033[91m"; Y = "\033[93m"; G = "\033[92m"; B = "\033[94m"; RESET = "\033[0m"

BUILTIN_VARS = {
    "open","high","low","close","volume","time","bar_index","barstate",
    "barmerge","na","true","false","syminfo","strategy","ta","math",
    "request","alert","color","plot","shape","size","location","input",
    "dayofweek","session","str","array","matrix","label","line","box",
    "table","chart","currency","dividends","earnings","splits","ticker",
}

PINE_TYPES = {"string","int","float","bool","color","series","simple","label","line","box","table"}

TA_SERIES   = re.compile(r'\bta\.(lowest|highest|rsi|ema|sma|macd|atr|stoch|bb|dmi|vwap|cci|mfi|willr)\b')
# Matches: [indent][optional_type ][varname] = ...
ASSIGN_RE   = re.compile(r'^(\s*)(?:(?:var|varip)\s+)?(?:\w+\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*=(?!=)(?!>)\s*')
REASSIGN_RE = re.compile(r'^(\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:=\s*')
VAR_DECL_RE = re.compile(r'^\s*(?:var|varip)\s+(?:\w+\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*=')
COND_TA_RE  = re.compile(r'\bta\.(lowest|highest)\s*\([^)]+\)(?:\s*\[\d+\])?')
USE_RE      = re.compile(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\b')
FUNC_DEF_RE = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*\([^)]*\)\s*=>')

# Properly capture typed declarations like: string _r = "x" or int _n = 0
TYPED_ASSIGN_RE = re.compile(
    r'^(\s*)(?:var\s+|varip\s+)?'
    r'(?:(' + '|'.join(PINE_TYPES) + r')\s+)?'
    r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=(?!=)(?!>)\s*'
)


class PineIssue:
    def __init__(self, code, severity, line_no, line_text, msg):
        self.code      = code
        self.severity  = severity
        self.line_no   = line_no
        self.line_text = line_text.strip()
        self.msg       = msg

    def __str__(self):
        col = R if self.severity == "ERROR" else Y
        return (f"  {col}[{self.code}]{RESET} Linea {self.line_no:4d}: {self.msg}\n"
                f"           {B}>{RESET} {self.line_text[:100]}")


def validate_pine(path: Path) -> list:
    issues = []
    try:
        code = path.read_text(encoding="utf-8")
    except Exception as e:
        return [PineIssue("IO_ERR","ERROR",0,"",str(e))]

    lines = code.split("\n")

    # Global declarations: index 0-indent assignments + var/varip
    global_decl = set(BUILTIN_VARS)
    # All declarations (global + local) — used to suppress false-positive CE10272
    all_decl = set(BUILTIN_VARS)

    # First pass: collect all declarations
    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped.startswith("//") or not stripped:
            continue

        m_var = VAR_DECL_RE.match(raw_line)
        if m_var:
            global_decl.add(m_var.group(1))
            all_decl.add(m_var.group(1))
            continue

        m = TYPED_ASSIGN_RE.match(raw_line)
        if m:
            var_name = m.group(3)
            indent   = len(m.group(1))
            if indent == 0 and var_name not in PINE_TYPES:
                global_decl.add(var_name)
            all_decl.add(var_name)

    # Second pass: detect issues
    for i, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip()
        stripped = line.lstrip()
        if stripped.startswith("//") or not stripped:
            continue

        # CW10002: ta.lowest/ta.highest inside boolean expression
        if COND_TA_RE.search(line):
            rhs_match = TYPED_ASSIGN_RE.match(line) or REASSIGN_RE.match(line)
            if rhs_match:
                rhs = line[rhs_match.end():]
                if re.search(r'\b(and|or)\b', rhs) and COND_TA_RE.search(rhs):
                    issues.append(PineIssue(
                        "CW10002","WARN", i, line,
                        "ta.lowest()/ta.highest() dentro de expresion booleana — "
                        "asignar primero a variable global para consistencia"
                    ))

        # CW10013: using = in a local block for a variable already declared globally
        m_asgn = TYPED_ASSIGN_RE.match(line)
        if m_asgn:
            var_name   = m_asgn.group(3)
            var_indent = len(m_asgn.group(1))
            if var_indent > 0 and var_name in global_decl and var_name not in PINE_TYPES:
                issues.append(PineIssue(
                    "CW10013","WARN", i, line,
                    f"'{var_name}' ya existe en scope global — "
                    "usar ':=' para reasignar, no '=' (crea variable local que sombrea la exterior)"
                ))

        # CE10272: := on a variable never declared anywhere
        m_reasgn = REASSIGN_RE.match(line)
        if m_reasgn:
            var_name = m_reasgn.group(2)
            if var_name not in all_decl:
                issues.append(PineIssue(
                    "CE10272","ERROR", i, line,
                    f"'{var_name}' reasignado con := pero nunca declarado — "
                    "declarar con 'var nombre = valor' o 'nombre = valor' primero"
                ))

    # CE10272 pass: undeclared in smart_long / smart_short entry expressions
    for i, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if line.startswith("//") or not line:
            continue
        if re.match(r'^(smart_long|smart_short|entry_long|entry_short)\s*(=|:=)', line):
            for use in USE_RE.findall(line):
                if (use not in all_decl and
                        not use[0].isdigit() and
                        len(use) > 2 and
                        use not in {'and','or','not','true','false','na'}):
                    issues.append(PineIssue(
                        "CE10272","ERROR", i, raw_line,
                        f"'{use}' referenciado en señal principal pero no declarado"
                    ))

    return issues


def validate_motor(motor_id: int) -> dict:
    paths = []
    if motor_id == 1:
        for tf in MOTOR1_TF:
            p = PINE_DIR / f"SIGMA_{tf.upper()}_PRODUCTION.pine"
            if p.exists():
                paths.append(p)
    else:
        if MOTOR2.exists():
            paths.append(MOTOR2)

    results = []
    for p in paths:
        issues = validate_pine(p)
        errors = [x for x in issues if x.severity == "ERROR"]
        warns  = [x for x in issues if x.severity == "WARN"]
        results.append({
            "file":   p.name,
            "errors": len(errors),
            "warns":  len(warns),
            "issues": [{"code":x.code,"severity":x.severity,"line":x.line_no,"msg":x.msg} for x in issues],
        })
    return {"motor": motor_id, "files": results,
            "total_errors": sum(r["errors"] for r in results),
            "total_warns":  sum(r["warns"]  for r in results),
            "checked_at":   datetime.now().isoformat()}


def print_report(result: dict):
    m  = result["motor"]
    te = result["total_errors"]
    col = G if te == 0 else R
    print(f"\n{B}{'='*60}{RESET}")
    print(f"{B}  MOTOR {m} — Validacion Pine Script{RESET}")
    print(f"{B}{'='*60}{RESET}")
    for fr in result["files"]:
        status = f"{G}OK{RESET}" if fr["errors"] == 0 and fr["warns"] == 0 else \
                 (f"{R}{fr['errors']} ERROR(S){RESET}" if fr["errors"] else f"{Y}{fr['warns']} WARN(S){RESET}")
        print(f"\n  {fr['file']}: {status}")
        for iss in fr["issues"]:
            col2 = R if iss["severity"] == "ERROR" else Y
            print(f"    {col2}[{iss['code']}]{RESET} L{iss['line']}: {iss['msg']}")
    print(f"\n{col}  Total: {te} errores, {result['total_warns']} advertencias{RESET}")
    print(f"{B}{'='*60}{RESET}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file",  help="Validar un Pine especifico")
    ap.add_argument("--motor", type=int, choices=[1,2], help="Solo motor 1 o 2")
    ap.add_argument("--json",  action="store_true", help="Salida en JSON")
    args = ap.parse_args()

    if args.file:
        p = Path(args.file)
        issues = validate_pine(p)
        if args.json:
            print(json.dumps([{"code":x.code,"sev":x.severity,"line":x.line_no,"msg":x.msg} for x in issues]))
        else:
            print(f"\n{B}Validando: {p.name}{RESET}")
            for iss in issues:
                print(str(iss))
            col = G if not issues else R
            print(f"\n{col}  {len(issues)} problemas encontrados{RESET}\n")
        return 1 if any(x.severity=="ERROR" for x in issues) else 0

    motors = [1,2] if not args.motor else [args.motor]
    all_results = [validate_motor(m) for m in motors]
    total_err = sum(r["total_errors"] for r in all_results)

    if args.json:
        print(json.dumps(all_results))
    else:
        for r in all_results:
            print_report(r)

    return 1 if total_err > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
