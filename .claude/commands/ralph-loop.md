Run: bash .agent/scripts/ralph-loop.sh $ARGUMENTS
If no arguments are given, FIRST run with --dry-run and show me the prompt for
the next feature before doing any real run. Wait for my confirmation.
Report each iteration's pass/fail. If a STALL is reported, stop and summarise
the failure from .agent/ralph/last-verify.log — do not keep retrying.
