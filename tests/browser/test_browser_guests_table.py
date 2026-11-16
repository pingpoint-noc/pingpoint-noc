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


STORAGES = [
    {"storage_name": "local", "storage_type": "dir", "status": "active", "used_bytes": 74_550_000_000, "total_bytes": 100_090_000_000,
     "avail_bytes": 21_060_000_000, "content": "vztmpl,images,import,backup,rootdir,snippets,iso"},
    {"storage_name": "truenas-backups", "storage_type": "dir", "status": "active", "used_bytes": 54_640_000_000, "total_bytes": 297_220_000_000,
     "avail_bytes": 242_580_000_000, "content": "backup"},
]
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
        page.evaluate("([guests, storages]) => renderProxmoxOverview({metrics: {}, guest_summary: {}, storages: storages, guests: guests})", [GUESTS, STORAGES])
        page.wait_for_function("document.querySelector('.table-guests').getBoundingClientRect().height > 0")
        return page

    def row_heights(self, page):
        heights = page.evaluate("[...document.querySelectorAll('#overview-pve-guests-tbody tr')].map(r => r.getBoundingClientRect().height)")
        self.assertTrue(all(h > 20 for h in heights), "the rows are not on screen: %r" % heights)  # a hidden table measures 0
        return heights

    def test_only_two_addresses_show_and_the_rest_are_in_a_chip(self):
        page = self.show(1400)
        shown = page.evaluate("[...document.querySelectorAll('#overview-pve-guests-tbody tr:nth-child(3) .guest-ip')].map(e => e.textContent)")
        self.assertEqual(shown, ["192.168.1.72", "172.17.0.1"])
        chip = page.locator("#overview-pve-guests-tbody tr:nth-child(3) .ip-more")
        self.assertEqual(chip.inner_text(), "+14")  # 16 shown addresses (fe80 is left out) minus the two on screen
        title = chip.get_attribute("title")
        self.assertIn("172.30.0.1", title)
        self.assertNotIn("fe80", title)
        self.assertEqual(page.locator("#overview-pve-guests-tbody tr:nth-child(1) .ip-more").count(), 0)  # two addresses: no chip

    def test_the_chip_is_level_with_the_addresses_and_spaced_from_them(self):
        for width in (1400, 960, 800):
            page = self.show(width)
            box = page.evaluate("""() => { const row = document.querySelector('#overview-pve-guests-tbody tr:nth-child(3)');
              const ips = [...row.querySelectorAll('.guest-ip')].map(e => e.getBoundingClientRect());
              const chip = row.querySelector('.ip-more').getBoundingClientRect();
              const last = ips[ips.length - 1], first = ips[0];
              const below = chip.top >= last.bottom - 1;
              return {below: below, chipHeight: chip.height, chipWidth: chip.width,
                      overlaps: ips.some(r => r.right > chip.left && r.left < chip.right && r.top < chip.bottom && r.bottom > chip.top),
                      // same line: centred on it and spaced from the last address; below: under the first address, left edges level
                      lineOffset: Math.abs((chip.top + chip.height / 2) - (last.top + last.height / 2)), gap: chip.left - last.right,
                      leftOffset: Math.abs(chip.left - first.left), drop: chip.top - last.bottom}; }""")
            self.assertFalse(box["overlaps"], "%d px: %r" % (width, box))
            if box["below"]:
                self.assertLessEqual(box["leftOffset"], 2, "%d px: the chip is not under the first address: %r" % (width, box))
                self.assertTrue(0 <= box["drop"] <= 8, "%d px: %r" % (width, box))
            else:
                self.assertLessEqual(box["lineOffset"], 3, "%d px: the chip is off the line of the address: %r" % (width, box))
                self.assertGreaterEqual(box["gap"], 4, "%d px: the chip touches the address: %r" % (width, box))
            self.assertTrue(18 <= box["chipHeight"] <= 24 and box["chipWidth"] >= 24, box)

    def test_the_ram_can_be_shown_as_a_percentage_and_the_choice_is_kept(self):
        page = self.show(1400)
        cell = "#overview-pve-guests-tbody tr:first-child td:nth-child(5)"
        self.assertIn("MB / 2.00 GB", page.inner_text(cell))  # default: used / total
        self.assertEqual(page.get_attribute("[data-ram-mode='bytes']", "aria-pressed"), "true")
        page.click("[data-ram-mode='pct']")
        self.assertEqual(page.inner_text(cell).strip(), "45 %")  # 962.26 MB of 2 GiB
        self.assertIn("MB / 2.00 GB", page.get_attribute(cell + " span", "title"))
        self.assertEqual(page.get_attribute("[data-ram-mode='pct']", "aria-pressed"), "true")
        self.assertEqual(page.get_attribute("[data-ram-mode='bytes']", "aria-pressed"), "false")
        # a stopped guest has no use to show
        self.assertEqual(page.inner_text("#overview-pve-guests-tbody tr:nth-child(4) td:nth-child(5)").strip(), "--")
        self.assertEqual(page.evaluate("localStorage.getItem('mini-noc-guest-ram')"), "pct")
        # the time range is not touched by this switch
        self.assertEqual(page.evaluate("STATE.range"), "15m")
        page.click("[data-ram-mode='bytes']")
        self.assertIn("MB / 2.00 GB", page.inner_text(cell))

    def test_switching_the_ram_mode_moves_nothing_in_either_table(self):
        """The columns and the width of both tables of the panel stay put when the RAM changes from bytes to percent."""
        script = """() => { const grab = sel => { const t = document.querySelector(sel);
          return [t.closest('.table-container').getBoundingClientRect().width, t.getBoundingClientRect().width,
                  ...[...t.querySelectorAll('thead th')].map(th => th.getBoundingClientRect().left), t.getBoundingClientRect().height]; };
          return {guests: grab('.table-guests'), storage: grab('.table-storage')}; }"""
        for width in (1400, 960):
            page = self.show(width)
            before = page.evaluate(script)
            page.click("[data-ram-mode='pct']")
            after = page.evaluate(script)
            for name in ("guests", "storage"):
                for index, (a, b) in enumerate(zip(before[name], after[name])):
                    self.assertAlmostEqual(a, b, delta=1.0, msg="%d px, %s table, measure %d: %r -> %r" % (width, name, index, before[name], after[name]))

    def test_the_storage_sizes_stay_on_one_line(self):
        page = self.show(960)
        heights = page.evaluate("[...document.querySelectorAll('.table-storage tbody tr')].map(r => r.getBoundingClientRect().height)")
        self.assertTrue(all(20 < h <= 70 for h in heights), heights)
        # "74.55 GB / 100.09 GB" is one line of text (the cell itself stretches to the row, which the content types can make tall)
        lines = page.evaluate("""() => [4, 5, 6].map(n => { const r = document.createRange();
          r.selectNodeContents(document.querySelector('.table-storage tbody tr td:nth-child(' + n + ')'));
          return new Set([...r.getClientRects()].map(x => Math.round(x.top))).size; })""")
        self.assertEqual(lines, [1, 1, 1])

    def test_the_percentage_choice_is_there_after_a_reload(self):
        page = self.show(1400)
        page.click("[data-ram-mode='pct']")
        page.reload()
        page.wait_for_function("typeof renderProxmoxOverview === 'function' && STATE.cacheDeviceDetail !== undefined")
        page.add_style_tag(content="#overview-proxmox-wrap.hidden { display: block !important; }")
        page.evaluate("(guests) => renderProxmoxOverview({metrics: {}, guest_summary: {}, storages: [], guests: guests})", GUESTS)
        self.assertEqual(page.inner_text("#overview-pve-guests-tbody tr:first-child td:nth-child(5)").strip(), "45 %")
        self.assertEqual(page.get_attribute("[data-ram-mode='pct']", "aria-pressed"), "true")

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
        ram = page.evaluate("document.querySelector('#overview-pve-guests-tbody tr:first-child td:nth-child(5)').getBoundingClientRect().height")
        self.assertTrue(20 < ram <= 70, ram)

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
        label = page.get_attribute("#overview-pve-guests-tbody tr:first-child td:nth-child(5)", "data-label")
        self.assertEqual(label, "RAM")


if __name__ == "__main__":
    unittest.main()
