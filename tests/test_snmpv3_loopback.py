"""SnmpSession against a real SNMPv3 agent on 127.0.0.1 (PySNMP's own agent, SHA + AES-128).

No router needed: it proves that the credentials built by collectors.snmp.credentials negotiate
authPriv for real, and that a wrong key becomes a label that says what to check.
"""

import asyncio
import importlib.util
import socket
import threading
import time
import unittest
import warnings

from collectors.snmp.credentials import load_v3_config
from collectors.snmp.poller import SnmpPollError, SnmpSession

USER = "mininoc"
AUTH_KEY = "auth-secret-123"
PRIV_KEY = "priv-secret-456"


def free_udp_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class AgentThread(threading.Thread):
    """Runs a minimal SNMPv3 agent until stop() is called."""

    def __init__(self, port, auth_name="SHA", priv_name="AES"):
        super().__init__(daemon=True)
        self.port = port
        self.auth_name = auth_name
        self.priv_name = priv_name
        self.ready = threading.Event()
        self.failure = None
        self.engine = None
        self.loop = None

    def run(self):
        try:
            from pysnmp.carrier.asyncio.dgram import udp
            from pysnmp.entity import config, engine
            from pysnmp.entity.rfc3413 import cmdrsp, context

            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.engine = engine.SnmpEngine()
            config.add_transport(
                self.engine, udp.DOMAIN_NAME, udp.UdpTransport().open_server_mode(("127.0.0.1", self.port))
            )
            auth_oid = {
                "SHA": config.USM_AUTH_HMAC96_SHA,
                "SHA224": config.USM_AUTH_HMAC128_SHA224,
                "SHA256": config.USM_AUTH_HMAC192_SHA256,
                "SHA384": config.USM_AUTH_HMAC256_SHA384,
                "SHA512": config.USM_AUTH_HMAC384_SHA512,
                "MD5": config.USM_AUTH_HMAC96_MD5,
            }[self.auth_name]
            priv_oid = {"AES": config.USM_PRIV_CFB128_AES, "DES": config.USM_PRIV_CBC56_DES}[self.priv_name]
            config.add_v3_user(self.engine, USER, auth_oid, AUTH_KEY, priv_oid, PRIV_KEY)
            config.add_vacm_user(self.engine, 3, USER, "authPriv", (1, 3, 6, 1, 2, 1), (1, 3, 6, 1, 2, 1))
            snmp_context = context.SnmpContext(self.engine)
            cmdrsp.GetCommandResponder(self.engine, snmp_context)
            cmdrsp.NextCommandResponder(self.engine, snmp_context)
            cmdrsp.BulkCommandResponder(self.engine, snmp_context)
            self.engine.transport_dispatcher.job_started(1)
            self.ready.set()
            self.engine.open_dispatcher()
        except Exception as exc:
            self.failure = exc
            self.ready.set()

    def stop(self):
        if self.engine is not None:
            try:
                self.engine.transport_dispatcher.job_finished(1)
                self.engine.close_dispatcher()
            except Exception:
                pass


def v3_env(auth_key=AUTH_KEY, priv_key=PRIV_KEY):
    return {
        "MININOC_SNMP_V3_USER": USER,
        "MININOC_SNMP_V3_AUTH_KEY": auth_key,
        "MININOC_SNMP_V3_PRIV_KEY": priv_key,
    }


class TestSnmpV3Loopback(unittest.TestCase):
    def setUp(self):
        warnings.simplefilter("ignore")
        if importlib.util.find_spec("cryptography") is None:
            self.skipTest("the 'cryptography' package is not installed (pip install -r requirements.txt)")
        self.port = free_udp_port()
        self.agent = AgentThread(self.port)
        self.agent.start()
        self.agent.ready.wait(5)
        if self.agent.failure is not None:
            self.skipTest("cannot start a local SNMPv3 agent: %s" % self.agent.failure)
        time.sleep(0.3)

    def tearDown(self):
        self.agent.stop()

    def fetch(self, env):
        session = SnmpSession("127.0.0.1", self.port, "unused", timeout=5.0, retries=1, v3_config=load_v3_config(env))

        async def run():
            # The engine belongs to this event loop: it must be closed before the loop ends.
            try:
                return await session.fetch()
            finally:
                session.close()

        return asyncio.run(run())

    def test_authpriv_negotiates_and_returns_sysuptime(self):
        pairs = self.fetch(v3_env())
        oids = []
        for oid, _value in pairs:
            oids.append(tuple(oid))
        self.assertIn((1, 3, 6, 1, 2, 1, 1, 3, 0), oids)

    def test_a_wrong_authentication_key_names_what_to_check(self):
        with self.assertRaises(SnmpPollError) as caught:
            self.fetch(v3_env(auth_key="not-the-auth-key"))
        message = str(caught.exception)
        self.assertIn("authentication", message)
        self.assertNotIn("not-the-auth-key", message)
        self.assertNotIn(AUTH_KEY, message)


class TestEveryProtocolCombination(unittest.TestCase):
    """Every authentication and encryption protocol the poller offers negotiates against a real agent."""

    AUTH = ("SHA", "SHA224", "SHA256", "SHA384", "SHA512", "MD5")
    PRIV = ("AES", "DES")

    def setUp(self):
        warnings.simplefilter("ignore")
        if importlib.util.find_spec("cryptography") is None:
            self.skipTest("the 'cryptography' package is not installed (pip install -r requirements.txt)")

    def poll(self, auth_name, priv_name):
        port = free_udp_port()
        agent = AgentThread(port, auth_name, priv_name)
        agent.start()
        agent.ready.wait(5)
        if agent.failure is not None:
            self.skipTest("cannot start a local SNMPv3 agent: %s" % agent.failure)
        time.sleep(0.3)
        env = v3_env()
        env["MININOC_SNMP_V3_AUTH_PROTOCOL"] = auth_name
        env["MININOC_SNMP_V3_PRIV_PROTOCOL"] = priv_name
        env["MININOC_SNMP_V3_ALLOW_WEAK"] = "yes"
        session = SnmpSession("127.0.0.1", port, "unused", timeout=5.0, retries=1, v3_config=load_v3_config(env))

        async def run():
            try:
                return await session.fetch()
            finally:
                session.close()

        try:
            return asyncio.run(run())
        finally:
            agent.stop()

    def test_all_twelve_combinations_return_sysuptime(self):
        for auth_name in self.AUTH:
            for priv_name in self.PRIV:
                with self.subTest(auth=auth_name, priv=priv_name):
                    oids = [tuple(oid) for oid, _value in self.poll(auth_name, priv_name)]
                    self.assertIn((1, 3, 6, 1, 2, 1, 1, 3, 0), oids)


if __name__ == "__main__":
    unittest.main()
