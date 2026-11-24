"""几何交叠计算的单元测试。"""

from __future__ import annotations

import math

import pytest

from app.geometry import GridMetrics, build_overlap


def make_grid(origin_x, origin_y, w, h, cols, rows) -> GridMetrics:
    return GridMetrics(origin_x, origin_y, float(w), float(h), cols, rows)


def test_aligned_grids_one_to_one():
    """完全对齐的两张等大格网：每格与对侧同位置格一一交叠。"""
    src = make_grid(0, 0, 100, 100, 3, 2)
    tgt = make_grid(0, 0, 100, 100, 3, 2)
    table = build_overlap(src, tgt)
    assert len(table.pairs) == 6
    assert all(p.area_m2 == pytest.approx(10_000.0) for p in table.pairs)
    assert table.total_overlap_area == pytest.approx(60_000.0)
    assert {
        (p.target_row, p.target_col, p.source_row, p.source_col) for p in table.pairs
    } == {(r, c, r, c) for r in range(2) for c in range(3)}


def test_misaligned_partial_overlap_pair_areas_sum_correctly():
    """错位格网：逐对面积之和等于整体 AABB 交叠面积。"""
    src = make_grid(0, 0, 100, 100, 4, 3)
    tgt = make_grid(60, 40, 120, 120, 3, 2)
    table = build_overlap(src, tgt)

    # 整体交叠范围 x: [60, 400) 长 340；y: [40, 280) 高 240。
    assert table.total_overlap_area == pytest.approx(340.0 * 240.0)

    # 每个目标格被源格切分出的面积之和，应等于该目标格落在源范围内的面积。
    by_target: dict[tuple[int, int], float] = {}
    for p in table.pairs:
        key = (p.target_row, p.target_col)
        by_target[key] = by_target.get(key, 0.0) + p.area_m2

    # 目标格 (0,0) 覆盖 x[60,180) y[40,160)，全部落在源范围内 → 120×120。
    assert by_target[(0, 0)] == pytest.approx(14_400.0)
    # 目标格 (1,2) 覆盖 x[300,420) y[160,280)，源在 x 方向只到 400 → 100×120。
    assert by_target[(1, 2)] == pytest.approx(12_000.0)

    # 所有 6 个目标格都与源有交叠。
    assert len(by_target) == 6


def test_no_overlap_returns_empty_table():
    src = make_grid(0, 0, 100, 100, 2, 2)
    tgt = make_grid(500, 500, 100, 100, 2, 2)
    table = build_overlap(src, tgt)
    assert table.pairs == ()
    assert table.total_overlap_area == 0.0


def test_touching_boundary_is_not_overlap():
    """仅在边界线上相切不算相交（交叠面积为 0）。"""
    src = make_grid(0, 0, 100, 100, 2, 2)
    tgt = make_grid(200, 0, 100, 100, 1, 1)  # 左边界 x=200 与源右边界相切
    table = build_overlap(src, tgt)
    assert table.pairs == ()


def test_target_only_partially_covers_source():
    """目标格网只覆盖源格网的一角。"""
    src = make_grid(0, 0, 10, 10, 10, 10)
    tgt = make_grid(25, 35, 20, 20, 1, 1)
    table = build_overlap(src, tgt)
    # 目标格 x[25,45) 跨源列 2,3,4；y[35,55) 跨源行 3,4,5 → 9 对。
    assert len(table.pairs) == 9
    assert table.total_overlap_area == pytest.approx(400.0)
    # 角上的源格 (3,2) 只贡献 5×5。
    corner = next(
        p
        for p in table.pairs
        if p.source_row == 3 and p.source_col == 2
    )
    assert corner.area_m2 == pytest.approx(25.0)


def test_nontrivial_float_boundaries():
    """带小数的格宽与错位，浮点误差不应产生 0 面积或越界格对。"""
    src = make_grid(0.0, 0.0, 0.3, 0.7, 10, 10)
    tgt = make_grid(0.1, 0.2, 0.3, 0.7, 8, 8)
    table = build_overlap(src, tgt)
    assert table.pairs
    assert all(p.area_m2 > 0.0 for p in table.pairs)
    # 面积守恒：逐对面积和 = 整体交叠面积。
    lo_x, hi_x = 0.1, min(3.0, 0.1 + 2.4)
    lo_y, hi_y = 0.2, min(7.0, 0.2 + 5.6)
    expected = (hi_x - lo_x) * (hi_y - lo_y)
    assert table.total_overlap_area == pytest.approx(expected, rel=1e-12)


def test_offset_that_is_integer_multiple_of_cell_size():
    """整格偏移：每个目标格恰好落在一个源格内。"""
    src = make_grid(0, 0, 50, 50, 6, 6)
    tgt = make_grid(100, 100, 50, 50, 2, 2)
    table = build_overlap(src, tgt)
    assert len(table.pairs) == 4
    assert all(p.area_m2 == pytest.approx(2500.0) for p in table.pairs)
    assert {
        (p.source_row, p.source_col) for p in table.pairs
    } == {(2, 2), (2, 3), (3, 2), (3, 3)}


def test_grid_metrics_extent():
    g = make_grid(10, -5, 2, 3, 4, 5)
    assert g.max_x == 18
    assert g.max_y == 10
    assert g.cell_area == 6
