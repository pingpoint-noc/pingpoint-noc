"""WCAG contrast audit of the colour tokens in web/static/styles.css (both themes)."""
import os
import re
import unittest

CSS_PATH = os.path.join(os.path.dirname(__file__), "..", "web", "static", "styles.css")
MIN_RATIO = 4.5

# (foreground token, background token). Backgrounds with alpha are composited over bg-card.
PAIRS = [
    ("--text-main", "--bg-canvas"),
    ("--text-main", "--bg-surface"),
    ("--text-main", "--bg-card"),
    ("--text-main", "--bg-hover"),
    ("--text-muted", "--bg-canvas"),
    ("--text-muted", "--bg-surface"),
    ("--text-muted", "--bg-card"),
    ("--text-muted", "--bg-elevated"),
    ("--text-dim", "--bg-canvas"),
    ("--text-dim", "--bg-surface"),
    ("--text-dim", "--bg-card"),
    ("--text-strong", "--bg-active"),
    ("--color-cyan", "--bg-card"),
    ("--color-cyan", "--bg-surface"),
    ("--color-cyan", "--bg-canvas"),
    ("--color-purple", "--bg-card"),
    ("--color-ok", "--bg-card"),
    ("--color-warn", "--bg-card"),
    ("--color-danger", "--bg-card"),
    ("--color-ok", "--color-ok-bg"),
    ("--color-warn", "--color-warn-bg"),
    ("--color-danger", "--color-danger-bg"),
    ("--on-accent", "--accent-solid"),
    ("--on-accent", "--accent-solid-hover"),
]


def parse_block(css, selector):
    start = css.index(selector + " {")
    end = css.index("\n}", start)
    body = css[start:end]
    tokens = {}
    for name, value in re.findall(r"(--[a-z0-9-]+):\s*([^;]+);", body):
        tokens[name] = value.strip()
    return tokens


def parse_color(value):
    value = value.strip()
    if value.startswith("#"):
        digits = value[1:]
        return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16), 1.0)
    match = re.match(r"rgba?\(([^)]+)\)", value)
    if match is None:
        raise ValueError("unsupported colour: " + value)
    parts = [p.strip() for p in match.group(1).split(",")]
    alpha = 1.0
    if len(parts) == 4:
        alpha = float(parts[3])
    return (int(parts[0]), int(parts[1]), int(parts[2]), alpha)


def composite(fg, bg):
    alpha = fg[3]
    out = []
    for i in range(3):
        out.append(fg[i] * alpha + bg[i] * (1 - alpha))
    return (out[0], out[1], out[2], 1.0)


def luminance(rgb):
    channels = []
    for i in range(3):
        c = rgb[i] / 255.0
        if c <= 0.03928:
            channels.append(c / 12.92)
        else:
            channels.append(((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def ratio(fg, bg):
    l1 = luminance(fg)
    l2 = luminance(bg)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


class ContrastTests(unittest.TestCase):
    def setUp(self):
        with open(CSS_PATH, encoding="utf-8") as handle:
            css = handle.read()
        dark = parse_block(css, ":root")
        light = dict(dark)
        light.update(parse_block(css, ':root[data-theme="light"]'))
        self.themes = {"dark": dark, "light": light}

    def check_theme(self, name):
        tokens = self.themes[name]
        failures = []
        for fg_name, bg_name in PAIRS:
            card = parse_color(tokens["--bg-card"])
            bg = composite(parse_color(tokens[bg_name]), card)
            fg = composite(parse_color(tokens[fg_name]), bg)
            value = ratio(fg, bg)
            if value < MIN_RATIO:
                failures.append("%s on %s = %.2f" % (fg_name, bg_name, value))
        self.assertEqual(failures, [], name + " theme below 4.5:1")

    def test_dark_theme(self):
        self.check_theme("dark")

    def test_light_theme(self):
        self.check_theme("light")

    def test_ratio_known_values(self):
        self.assertAlmostEqual(ratio((0, 0, 0, 1), (255, 255, 255, 1)), 21.0, places=1)


if __name__ == "__main__":
    unittest.main()
