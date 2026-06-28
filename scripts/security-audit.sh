#!/bin/bash
echo "🔍 Security Audit — JDL Production Core"
echo ""

echo "❌ CHECKING: Hardcoded secrets"
grep -r "PRIVATE_KEY.*=.*0x\|PASSWORD.*=\|SECRET.*=.*dev-" . --include="*.ts" --include="*.py" --include=".env*" | grep -v "<set-in-production>" | grep -v "<set-via-secrets-manager>" && echo "  FOUND SECRETS!" || echo "  ✅ CLEAN"

echo ""
echo "❌ CHECKING: Disabled CSP"
grep "contentSecurityPolicy: false" src/app.ts && echo "  CSP DISABLED!" || echo "  ✅ CSP ENABLED"

echo ""
echo "❌ CHECKING: console.log with sensitive data"
grep -r "console\\.log.*privateKey\\|console\\.log.*password" python/ src/ && echo "  FOUND LOGS!" || echo "  ✅ SAFE"

echo ""
echo "❌ CHECKING: Redundant files"
[ -f "python/flash_loan_zero_gas.py" ] && echo "  REDUNDANT FILE FOUND!" || echo "  ✅ CLEAN"

echo ""
echo "✅ Audit Complete"
