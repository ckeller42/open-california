#!/usr/bin/env bash
# Run the at-the-van roof test with writes temporarily enabled, then ALWAYS revert to read-only.
#
# calictl.service runs read-only by default (SAFE DEFAULT — it never actuates unless told). A roof
# test needs writes, so this wrapper drops in `CALICTL_ENABLE_WRITES=1`, restarts the daemon, runs
# tools/van_roof_test, and — via a trap that fires on any exit including Ctrl-C — removes the drop-in
# and restarts the daemon read-only again. Writes are enabled ONLY for the duration of the test.
#
# buspi only (needs sudo + systemd). Usage:  sudo -v && bash tools/roof_test_session.sh [-- <test args>]
set -euo pipefail

UNIT=calictl.service
DROPIN_DIR="/etc/systemd/system/${UNIT}.d"
DROPIN="${DROPIN_DIR}/90-enable-writes.conf"
URL="${CALICTL_URL:-http://localhost:8088}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

revert() {
  echo "» reverting calictl to read-only…"
  sudo rm -f "$DROPIN"
  sudo systemctl daemon-reload
  sudo systemctl restart "$UNIT"
  echo "» calictl is $(systemctl is-active "$UNIT") (read-only)."
}
trap revert EXIT   # fires on normal exit, error (set -e), and Ctrl-C/SIGTERM

echo "» enabling writes via ${DROPIN} …"
sudo mkdir -p "$DROPIN_DIR"
printf '[Service]\nEnvironment=CALICTL_ENABLE_WRITES=1\n' | sudo tee "$DROPIN" >/dev/null
sudo systemctl daemon-reload
sudo systemctl restart "$UNIT"

printf '» waiting for the daemon'
for _ in $(seq 1 20); do
  if curl -fsS "$URL/api/state" >/dev/null 2>&1; then printf ' up.\n'; break; fi
  printf '.'; sleep 1
done

# The harness talks HTTP only (no calictl import), so the system python3 is fine.
cd "$REPO_ROOT"
python3 -m tools.van_roof_test --url "$URL" "$@"
# revert() runs here via the EXIT trap, whatever the test's exit status.
