# =====================================================================
# Automatic telemetry configuration for Mini-NOC
# Collector: 192.168.88.4
# =====================================================================

# 1. Remote syslog towards Mini-NOC
# Rules first: an action that rules still use cannot be removed, and /import stops at the first error.
/system logging remove [find action=mininoc]
/system logging action remove [find name=mininoc]
/system logging action add name=mininoc target=remote remote=192.168.88.4 remote-port=514
/system logging add action=mininoc topics=firewall,info
/system logging add action=mininoc topics=system,info,account
/system logging add action=mininoc topics=system,error,critical

# 2. IPFIX (Traffic-Flow)
/ip traffic-flow set enabled=yes active-flow-timeout=1m inactive-flow-timeout=15s
/ip traffic-flow target remove [find dst-address=192.168.88.4]
/ip traffic-flow target add dst-address=192.168.88.4 port=2055 version=ipfix

# 3. SNMP agent (read-only restricted to Mini-NOC)
/snmp community remove [find name=mininoc]
/snmp community add name=mininoc read-access=yes addresses=192.168.88.4/32
/snmp set enabled=yes
