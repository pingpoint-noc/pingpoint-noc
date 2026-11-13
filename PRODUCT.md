# PRODUCT.md

register: product

## What it is
PingPoint NOC: a web dashboard for network operations (SNMP, syslog, IPFIX), built for the small team that sets it up
and maintains it. Every number it shows must be verifiable. It is a working tool, not a marketing site.

## Users and scenes of use
- The operator: in front of the dashboard on a desktop screen, sometimes with another window on top, looking within
  seconds for anything broken and since when. It may be a bright room or the middle of the night (dark mode).
- The reviewer: walks through the tabs checking data freshness, time zone and the explanations on empty charts.
- Mobile: occasional look-ups only; not the main use.

## Product purpose
A network failure must be visible at once, and every number must say where it comes from and how old it is. Active alerts
are always visible at the top; nothing depends on a CDN, external fonts or third-party browser libraries.

## Tone and personality
Sober, dense and reliable. Information first, no decoration. Interface text in English; working documents may be in Spanish.

## Principles
1. Data freshness and origin are always in view; an empty chart explains why.
2. Strict colour semantics: red = firing, amber = warning or stale data, green = ok. No other colour competes.
3. Dark and light themes with the same hierarchy; every colour comes from variables.
4. Useful density: compact tables and panels, system typography, no remote fonts.
5. Keyboard accessible and legible (AA contrast at minimum).

## Anti-references
"Template" dashboards with giant metric cards and gradients, glass effects, decorative animation, side accent borders, and
anything that loads external resources.

## Constraints that affect the interface
- Web without CDN. Timestamps stored in UTC and shown in the configured zone (`MININOC_TZ`, Europe/Madrid by default).
- Active alerts always at the top; each panel shows its freshness.
