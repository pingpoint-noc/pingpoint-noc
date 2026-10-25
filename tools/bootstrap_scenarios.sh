#!/bin/bash
# Scenarios of the host onboarding script (GET /api/bootstrap.sh). Runs as root on a disposable lab container (it rewrites
# /etc/rsyslog.d/99-mininoc.conf and restores it at the end). Prepare /tmp/mn/ with:
#   good.sh  the script for the real collector:   python -c "from api.routes.bootstrap import build_bootstrap_script as b; print(b('<noc ip>', 8080))"
#   v6.sh    the same for an IPv6 collector (fd00::1)
# then:  bash tools/bootstrap_scenarios.sh      Each scenario prints PASS/FAIL with the exit code it expected.
GOOD=/tmp/mn/good.sh
V6=/tmp/mn/v6.sh
CONF=/etc/rsyslog.d/99-mininoc.conf
cp "$CONF" /tmp/mn/conf.orig

pass=0; fail=0
check() { # name expected_rc actual_rc
    if [ "$2" = "$3" ]; then pass=$((pass+1)); echo "PASS  $1 (rc=$3)"; else fail=$((fail+1)); echo "FAIL  $1 (rc=$3, expected $2)"; fi
}
mkpath() { # dir, then the tools to leave OUT
    local d="$1"; shift
    rm -rf "$d"; mkdir -p "$d"
    for b in id dirname mkdir mktemp cp rm tr uname hostname sed head grep ip logger curl wget systemctl service pkill cat rsyslogd apt-get dnf yum apk; do
        skip=0; for x in "$@"; do [ "$x" = "$b" ] && skip=1; done
        [ $skip -eq 1 ] && continue
        p="$(command -v $b 2>/dev/null)" && [ -n "$p" ] && ln -s "$p" "$d/$b"
    done
}

# A normal
out=$(bash $GOOD 2>&1); rc=$?; check "A normal install" 0 $rc; echo "$out" | tail -2
grep -q '^\*\.\* @192.168.88.100:514$' $CONF && echo "   conf ok" || { echo "   CONF WRONG: $(cat $CONF)"; fail=$((fail+1)); }

# L unknown option
out=$(bash $GOOD --bogus 2>&1); rc=$?; check "L unknown option" 2 $rc; echo "$out" | tail -1

# B not root
chmod 644 $GOOD; out=$(su -s /bin/bash nobody -c "bash $GOOD" 2>&1); rc=$?; check "B not root" 1 $rc; echo "$out" | tail -1

# C collector down
sed 's/192.168.88.100/192.168.88.254/' $GOOD > /tmp/mn/bad.sh
out=$(bash /tmp/mn/bad.sh 2>&1); rc=$?; check "C collector unreachable" 1 $rc; echo "$out" | tail -2

# D HTTP error (a server that answers 501 to POST)
sed 's/192.168.88.100/127.0.0.1/; s/NOC_PORT="8080"/NOC_PORT="8099"/' $GOOD > /tmp/mn/http.sh
( cd /tmp && timeout 20 python3 -m http.server 8099 --bind 127.0.0.1 >/dev/null 2>&1 & ) ; sleep 1
out=$(bash /tmp/mn/http.sh 2>&1); rc=$?; check "D HTTP 501 from the collector" 1 $rc; echo "$out" | tail -2
pkill -f "http.server 8099" 2>/dev/null

# restore the good conf before the next ones
bash $GOOD >/dev/null 2>&1

# E no curl, wget present
mkpath /tmp/mn/p_nocurl curl
out=$(PATH=/tmp/mn/p_nocurl /bin/bash $GOOD 2>&1); rc=$?; check "E wget instead of curl" 0 $rc; echo "$out" | tail -1

# F neither
mkpath /tmp/mn/p_none curl wget
out=$(PATH=/tmp/mn/p_none /bin/bash $GOOD 2>&1); rc=$?; check "F neither curl nor wget" 1 $rc; echo "$out" | tail -2

# G no systemctl / service: HUP fallback
mkpath /tmp/mn/p_nosysd systemctl service
out=$(PATH=/tmp/mn/p_nosysd /bin/bash $GOOD 2>&1); rc=$?; check "G no systemd (HUP fallback)" 0 $rc; echo "$out" | tail -2

# G2 nothing to restart rsyslog with: warns but still registers
mkpath /tmp/mn/p_norestart systemctl service pkill
out=$(PATH=/tmp/mn/p_norestart /bin/bash $GOOD 2>&1); rc=$?; check "G2 no way to restart rsyslog" 0 $rc; echo "$out" | grep -c "could not be restarted" | sed 's/^/   warnings: /'

# H no rsyslogd, no package manager
mkpath /tmp/mn/p_norsys rsyslogd apt-get dnf yum apk
out=$(PATH=/tmp/mn/p_norsys /bin/bash $GOOD 2>&1); rc=$?; check "H no rsyslog, no package manager" 1 $rc; echo "$out" | tail -1

# I rsyslog rejects the file: previous conf restored
cp /tmp/mn/conf.orig $CONF
mkpath /tmp/mn/p_badrsys rsyslogd
printf '#!/bin/sh\nexit 1\n' > /tmp/mn/p_badrsys/rsyslogd; chmod +x /tmp/mn/p_badrsys/rsyslogd
out=$(PATH=/tmp/mn/p_badrsys /bin/bash $GOOD 2>&1); rc=$?; check "I rsyslog rejects the new file" 1 $rc; echo "$out" | tail -1
cmp -s $CONF /tmp/mn/conf.orig && echo "   previous conf restored" || { echo "   NOT RESTORED: $(cat $CONF)"; fail=$((fail+1)); }

# K IPv6 collector: rsyslog must accept the bracketed target
out=$(bash $V6 2>&1); rc=$?; check "K IPv6 collector (registration fails: no route, conf must be valid)" 1 $rc; echo "$out" | tail -2
echo "   conf: $(cat $CONF)"; rsyslogd -N1 >/dev/null 2>&1 && echo "   rsyslogd -N1 accepts it" || { echo "   rsyslogd -N1 REJECTS IT"; fail=$((fail+1)); }

# J uninstall, then install again
out=$(bash $GOOD --uninstall 2>&1); rc=$?; check "J uninstall" 0 $rc; echo "$out" | tail -1
[ ! -e $CONF ] && echo "   conf removed" || { echo "   CONF STILL THERE"; fail=$((fail+1)); }
out=$(bash $GOOD 2>&1); rc=$?; check "J2 install again" 0 $rc

echo "--- $pass passed, $fail failed"
cmp -s $CONF /tmp/mn/conf.orig && echo "final conf identical to the original" || echo "final conf: $(cat $CONF)"
