"""Browser test: the hosted-guests table of a Proxmox host keeps its rows short at every width. Optional (Playwright).

A container that runs Docker reports a bridge address per project; the table used to print all of them, so one row grew to ten
lines at half-screen width. Now two addresses show, the rest are behind a "+N" chip, numbers do not wrap and a phone gets cards."""

import unittest

from .support import BrowserTestCase

DOCKER = ["192.168.1.72"] + ["172.%d.0.1" % n for n in range(17, 31)] + ["fdcc:ad94:bacf:61a3::1", "fe80::1"]


def guest(vmid, name, kind, ips, running=True, matched=True):
    return {"vmid": vmid, "name": name, "guest_type": kind, "status": "running" if running else "stopped", "cpus": 4,
            "cpu_frac": 0.028 if running else None, "mem_used_bytes": 962_260_000 if running else None,
            "maxmem_bytes": 2 * 1024 ** 3, "maxdisk_bytes": 23_520_000_000, "ip_addresses": ",".join(ips),
            "matched_device_id": name if matched else None}


GUESTS = [guest(100, "infra-core", "lxc", ["192.168.1.70", "172.21.0.1"]), guest(101, "TrueNAS", "qemu", []),
          guest(102, "media", "lxc", DOCKER), guest(106, "agente", "qemu", [], running=False, matched=False)]


class GuestsTableTest(BrowserTestCase):
    WITH_USER = False

    def show(self, width):
        context = self.browser.new_context(viewport={"width": width, "height": 900})
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(10000)
        page.goto(self.server.base_url)
        page.wait_for_function("typeof renderProxmoxOverview === 'function' && STATE.cacheDeviceDetail !== undefined")
        # The block is only shown for a Proxmox host, and the page hides it again whenever it reloads a device that is not one:
        # a rule that wins over that keeps it on screen for the measurements.
        page.add_style_tag(content="#overview-proxmox-wrap.hidden { display: block !important; }")
        page.evaluate("(guests) => renderProxmoxOverview({metrics: {}, guest_summary: {}, storages: [], guests: guests})", GUESTS)
        page.wait_for_function("document.querySelector('.table-guests').getBoundingClientRect().height > 0")
        return page

    def row_heights(self, page):
        heights = page.evaluate("[...document.querySelectorAll('#overview-pve-guests-tbody tr')].map(r => r.getBoundingClientRect().height)")
        self.assertTrue(all(h > 20 for h in heights), "the rows are not on screen: %r" % heights)  # a hidden table measures 0
        return heights

    def test_only_two_addresses_show_and_the_rest_are_in_a_chip(self):
        page = self.show(1400)
        text = page.inner_text("#overview-pve-guests-tbody tr:nth-child(3) .guest-ips")
        self.assertIn("192.168.1.72, 172.17.0.1", text)
        self.assertNotIn("172.20.0.1", text)
        chip = page.locator("#overview-pve-guests-tbody tr:nth-child(3) .ip-more")
        self.assertEqual(chip.inner_text(), "+14")  # 16 shown addresses (fe80 is left out) minus the two on screen
        title = chip.get_attribute("title")
        self.assertIn("172.30.0.1", title)
        self.assertNotIn("fe80", title)
        self.assertEqual(page.locator("#overview-pve-guests-tbody tr:nth-child(1) .ip-more").count(), 0)  # two addresses: no chip

    def test_rows_stay_short_at_desktop_and_half_screen_widths(self):
        for width in (1400, 960, 800):
            page = self.show(width)
            heights = self.row_heights(page)
            self.assertEqual(len(heights), 4, width)
            self.assertLessEqual(max(heights), 90, "%d px: %r" % (width, heights))
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width, width)

    def test_numbers_and_headers_do_not_wrap(self):
        page = self.show(960)
        lines = page.evaluate("""() => [...document.querySelectorAll('.table-guests thead th')].map(th => th.getBoundingClientRect().height)""")
        self.assertTrue(min(lines) > 20, lines)
        self.assertLessEqual(max(lines), 45, lines)
        ram = page.evaluate("document.querySelector('#overview-pve-guests-tbody tr:first-child td:nth-child(6)').getBoundingClientRect().height")
        self.assertTrue(20 < ram <= 60, ram)

    def test_every_column_fits_without_sideways_scrolling_at_half_screen(self):
        """At 960 px the last column ("NOC node") used to be cut off and needed a sideways scroll."""
        for width in (1400, 960):
            page = self.show(width)
            fits = page.evaluate("""() => { const c = document.querySelector('.table-guests').closest('.table-container');
              return [c.scrollWidth, c.clientWidth]; }""")
            self.assertLessEqual(fits[0], fits[1] + 1, "%d px: table %d, box %d" % (width, fits[0], fits[1]))

    def test_a_phone_gets_one_card_per_guest_with_the_column_names(self):
        page = self.show(390)
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
        self.assertTrue(page.is_visible("#overview-pve-guests-tbody tr:first-child"))
        # the header row is clipped to 1 px (Playwright still calls that "visible"): the cards carry the names instead
        self.assertLessEqual(page.evaluate("document.querySelector('.table-guests thead').getBoundingClientRect().height"), 2)
        label = page.get_attribute("#overview-pve-guests-tbody tr:first-child td:nth-child(6)", "data-label")
        self.assertEqual(label, "RAM")


if __name__ == "__main__":
    unittest.main()
