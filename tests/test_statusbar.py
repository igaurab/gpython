import asyncio
from pathlib import Path

from rich.text import Text

from gpython import config
from gpython.app import GPython
from gpython.statusbar import DEFAULT_LAYOUT, StatusBar, enabled_modules


def test_modules_follow_the_layout_and_the_switches():
    assert enabled_modules({}) == DEFAULT_LAYOUT.split()
    assert enabled_modules({"status.cpu": False, "status.gpu": False}) == ["mode", "clock", "host", "ram"]
    assert enabled_modules({"status.layout": "host mode bogus clock"}) == ["host", "mode", "clock"]


def test_mode_sits_left_and_the_modules_sit_right():
    bar = StatusBar({"status.layout": "mode clock host"})
    bar.values.update({"clock": "Fri 06 Nov 01:31", "host": "box"})
    bar.mode = Text("NORMAL")
    text = bar.compose_text(60)
    assert text.plain.startswith("NORMAL ") and text.plain.endswith("Fri 06 Nov 01:31 · box")
    assert len(text.plain) == 59


def ui(tmp_path, test):
    async def main():
        path = tmp_path / "nb.py"
        path.write_text("# %%\n1\n")
        app = GPython(path)
        async with app.run_test(size=(100, 12)) as pilot:
            await app.loaded.wait()
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(asyncio.wait_for(main(), 60))


def test_bar_shows_the_mode_the_clock_and_the_host_by_default(tmp_path):
    async def test(app, pilot):
        bar = app.query_one("#status", StatusBar)
        for _ in range(50):
            if "ram" in bar.values:
                break
            await pilot.pause(0.1)
        shown = str(bar.render())
        assert shown.startswith(" NORMAL ") and bar.values["host"] in shown and bar.values["clock"] in shown
        assert "ram" in bar.values and bar._started

    ui(tmp_path, test)


def test_config_switches_a_module_off_and_orders_the_rest(tmp_path):
    config.save({"status.cpu": False, "status.ram": False, "status.gpu": False, "status.layout": "mode host clock"})

    async def test(app, pilot):
        bar = app.query_one("#status", StatusBar)
        assert bar.modules == ["mode", "host", "clock"]
        assert str(bar.render()).endswith(bar.values["clock"])

    ui(tmp_path, test)


def test_hidden_bar_does_not_start_its_timers(tmp_path):
    config.save({"status_bar": False})

    async def test(app, pilot):
        bar = app.query_one("#status", StatusBar)
        assert not bar.display and not bar._started
        await pilot.press(":", *"set statusbar", "enter")
        await pilot.pause()
        assert bar.display and bar._started

    ui(tmp_path, test)


def test_gauge_colours_by_load():
    from gpython.statusbar import gauge

    low, mid, high = gauge("cpu", 12), gauge("ram", 51, "13.5G/32.0G"), gauge("gpu", 94)
    assert low.plain == "cpu [▁] 12%" and mid.plain == "ram [▅] 51% 13.5G/32.0G" and high.plain == "gpu [█] 94%"
    colour = lambda text: next(span.style for span in text.spans if span.style.startswith("bold"))
    assert colour(low) == "bold green" and colour(mid) == "bold yellow" and colour(high) == "bold red"


def test_btop_sparkline_and_meter():
    from gpython.statusbar import HISTORY, meter, sparkline

    spark = sparkline("cpu", [10, 50, 95])
    assert spark.plain == "cpu " + " " * (HISTORY - 3) + "▁▅█ 95%"
    bar_styles = [span.style for span in spark.spans if span.style.startswith("bold")]
    assert bar_styles == ["bold green", "bold yellow", "bold red", "bold red"]
    ram = meter("ram", 55, "14.1G/32.0G")
    assert ram.plain == "ram [▪▪▪▪▪▪    ] 55% 14.1G/32.0G"
    assert sparkline("gpu", []).plain.endswith(" 0%")


def test_samples_feed_the_history_and_the_style_switch(tmp_path):
    from gpython.statusbar import StatusBar

    bar = StatusBar({"status.style": "tmux"})
    bar.repaint = lambda: None
    bar._apply({"cpu": (40.0, ""), "ram": (55.0, "14G/32G")})
    assert bar.values["cpu"].plain == "cpu [▄] 40%" and list(bar.history["cpu"]) == [40.0]
    bar = StatusBar({})
    bar.repaint = lambda: None
    bar._apply({"cpu": (40.0, "")})
    bar._apply({"cpu": (90.0, "")})
    assert bar.values["cpu"].plain.endswith("▄█ 90%") and bar.style_name == "btop"
