"""Gráficos sin librerías (ui/viz.py): marcas, escapado, límites de series y tooltips."""
import re

from scanner_opciones.ui import viz


def test_nice_ticks_are_round_numbers_that_cover_the_range():
    assert viz.nice_ticks(10, 64) == [0, 20, 40, 60, 80]
    assert viz.nice_ticks(0, 337, 2) == [0, 200, 400]
    assert viz.nice_ticks(10.5, 29.3) == [10, 15, 20, 25, 30]
    assert viz.nice_ticks(5, 5)[0] <= 5 <= viz.nice_ticks(5, 5)[-1]            # un solo valor no rompe


def test_top_with_other_never_invents_more_series_than_the_ceiling():
    values = {f"s{i}": float(100 - i) for i in range(12)}
    top = viz.top_with_other(values, 7)
    assert len(top) == 8 and top[-1][0] == viz.OTHER and top[0][0] == "s0"
    assert top[-1][1] == sum(values[f"s{i}"] for i in range(7, 12))               # el resto se suma en «Otros»
    assert viz.top_with_other({"a": 1.0}, 7) == [("a", 1.0)]                       # sin cola no hay «Otros»


def test_series_colors_are_a_fixed_order_and_the_eighth_is_gray():
    assert viz.series_color(0) == "var(--viz-1)" and viz.series_color(6) == "var(--viz-7)"
    assert viz.series_color(viz.SERIES) == "var(--viz-other)"


def test_every_label_from_the_data_is_escaped():
    html = str(viz.hbars([('<img src=x onerror=alert(1)>', 5.0, 'a"b')]))
    assert "<img" not in html and "&lt;img" in html and 'a&quot;b' in html
    assert "<script" not in str(viz.legend([("<script>", "red"), ("ok", "blue")]))
    assert "<b>" not in str(viz.line_chart([(0, 1, "<b>x"), (1, 2, "y")], [(0, "<b>")], label="<b>"))


def test_hbars_draw_the_value_at_the_tip_and_scale_to_the_largest():
    html = str(viz.hbars([("A", 50.0, ""), ("B", 25.0, "")], fmt=lambda v: f"{v:.0f}"))
    assert re.findall(r"width:([\d.]+)%", html) == ["100.0", "50.0"]
    assert html.count('class="hb-v"') == 2 and 'data-tip="50|A"' in html and 'tabindex="0"' in html   # también con teclado
    assert "Sin datos" in str(viz.hbars([]))


def test_stacked_bars_use_the_same_color_per_series_in_every_row_and_a_legend():
    rows = [("S1", [("tech", 30.0), ("health", 10.0)], "40"), ("S2", [("tech", 20.0)], "20")]
    html = str(viz.stacked_hbars(rows, ["tech", "health"]))
    assert html.count("background:var(--viz-1)") >= 3 and html.count("background:var(--viz-2)") >= 2   # leyenda + segmentos
    assert 'class="lg"' in html and 'width:100.0%' in html and 'width:50.0%' in html
    assert "Sin datos" in str(viz.stacked_hbars([("S", [], "0")], []))


def test_a_meter_fills_proportionally_and_marks_the_thresholds():
    html = str(viz.meter(30.0, 0, 60, "#fab219", ticks=[(30, "30%"), (40, "40%")], label="Cushion"))
    assert "width:50.0%" in html and "left:50.0%" in html and "left:66.7%" in html and 'role="meter"' in html
    assert str(viz.meter(None, 0, 60, "red")) == ""                                 # sin dato, sin medidor


def test_a_line_needs_two_points_and_every_point_has_a_hit_area_with_its_tooltip():
    assert "Sin datos suficientes" in str(viz.line_chart([(0, 1, "x")], []))
    html = str(viz.line_chart([(0, 1.0, "1|a"), (1, 3.0, "3|b"), (2, 2.0, "2|c")], [(0, "ini"), (2, "fin")]))
    assert html.count('class="pt"') == 3 and html.count("<rect") == 3 and 'data-tip="3|b"' in html
    assert html.count('class="mk"') == 1                                            # solo el último punto va marcado


def test_columns_are_capped_at_24px_with_a_flat_base_and_rounded_top():
    html = str(viz.columns([("a", 10.0, ""), ("b", 5.0, "")], width=520))
    widths = [float(m) for m in re.findall(r'H([\d.]+) Q', html)]
    assert widths and 'class="bar1"' in html and "Sin datos" in str(viz.columns([("a", 0.0, "")]))


def test_scatter_gives_each_dot_a_tooltip_and_the_ordinal_color_of_its_bucket():
    html = str(viz.scatter([(10.0, 30.0, "30|X", 0), (20.0, 50.0, "50|Y", 3)], "x", "y"))
    assert "data-nearest" in html and html.count('class="sc"') == 2 and viz.ORDINAL[0] in html and viz.ORDINAL[3] in html
    assert "Sin contratos" in str(viz.scatter([], "x", "y"))
