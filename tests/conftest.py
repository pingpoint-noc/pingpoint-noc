"""Test configuration shared by every test.

The tests were written against the original lab's network (router 192.168.88.1, two internal /24 networks, NOC 192.168.88.4). Those are
still the defaults of `scenario.py`, but they must not stay a hidden assumption of the tests: they are set here, in the environment,
before anything is imported, so the suite gives the same result when the defaults become empty (ROADMAP F3).
A test that checks the defaults themselves (tests/test_scenario.py) clears these variables in its own setup.
"""

import os

LAB_SCENARIO = {
    "MININOC_ROUTER_IP": "192.168.88.1",
    "MININOC_DEVICE_ID": "edge-router-1",
    "MININOC_DEVICE_NAME": "edge-router",
    "MININOC_INTERNAL_NETS": "192.168.88.0/24,192.168.89.0/24",
    "MININOC_ROUTER_OWN_IPS": "192.168.100.2",
    "MININOC_NOC_IP": "192.168.88.4",
    "MININOC_TZ": "Europe/Madrid",
}

for _name, _value in LAB_SCENARIO.items():
    os.environ.setdefault(_name, _value)
