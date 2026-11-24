"""轴对齐矩形格网的交叠几何计算。

两张格网处于同一个平面米制坐标系中，均为轴对齐的规则矩形格网。
本模块只做纯几何计算，不涉及雨量与 HTTP 语义。

约定：格网原点 (origin_x, origin_y) 是第 (0, 0) 格的最小角坐标，
行沿 y 正方向递增，列沿 x 正方向递增。第 (r, c) 格覆盖：

    [origin_x + c*cell_width,  origin_x + (c+1)*cell_width]
    [origin_y + r*cell_height, origin_y + (r+1)*cell_height]
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import NamedTuple


class GridMetrics(NamedTuple):
    """不可变的格网几何定义（坐标单位：米）。"""

    origin_x: float
    origin_y: float
    cell_width: float
    cell_height: float
    cols: int
    rows: int

    @property
    def max_x(self) -> float:
        return self.origin_x + self.cols * self.cell_width

    @property
    def max_y(self) -> float:
        return self.origin_y + self.rows * self.cell_height

    @property
    def cell_area(self) -> float:
        return self.cell_width * self.cell_height


class OverlapPair(NamedTuple):
    """一个实际相交的（目标格, 源格）对及其交叠面积（平方米）。"""

    target_row: int
    target_col: int
    source_row: int
    source_col: int
    area_m2: float


@dataclass(frozen=True)
class OverlapTable:
    """两张格网之间全部实际相交格对的不可变交叠表。"""

    source: GridMetrics
    target: GridMetrics
    pairs: tuple[OverlapPair, ...]

    @property
    def total_overlap_area(self) -> float:
        """所有格对交叠面积之和，即两张格网整体的交叠面积。"""
        return sum(pair.area_m2 for pair in self.pairs)


def _axis_index_span(
    *,
    origin: float,
    cell_size: float,
    count: int,
    lo: float,
    hi: float,
) -> tuple[int, int] | None:
    """返回本轴上与区间 [lo, hi) 有正长度交叠的格索引半开区间 [first, last)。

    只纳入内部真正相交的格；仅在边界上相切（交叠长度为 0）的格不计入。
    无交叠时返回 None。先按公式求索引，再用格线不等式修正浮点误差，
    最后裁剪到合法范围 [0, count]。
    """
    if lo >= hi:
        return None

    # first = 包含 lo 的格索引（floor）；last = 首个起点不小于 hi 的格索引（ceil，排他）。
    first = math.floor((lo - origin) / cell_size)
    last = math.ceil((hi - origin) / cell_size)

    # 不依赖除法的浮点误差方向，直接用格线不等式做双侧修正。
    # 左侧：first 格必须满足其末端 > lo，否则右移；其左侧格若末端 > lo 则左移补回。
    while origin + (first + 1) * cell_size <= lo:
        first += 1
    while first > 0 and origin + first * cell_size > lo:
        first -= 1
    # 右侧：last 前一格必须满足其起点 < hi，否则左移；last 格若起点 < hi 则右移补入。
    while last > 0 and origin + (last - 1) * cell_size >= hi:
        last -= 1
    while origin + last * cell_size < hi:
        last += 1

    if first < 0:
        first = 0
    if last > count:
        last = count
    if first >= last:
        return None
    return first, last


def build_overlap(source: GridMetrics, target: GridMetrics) -> OverlapTable:
    """构造源、目标两张格网的交叠表。

    实现策略（避免全量两两试算）：

    1. 先求两张格网整体 AABB 的交集 [lo_x, hi_x) × [lo_y, hi_y)；
    2. 把整体交集映射到两张格网各自的行、列索引范围；
    3. 仅在这些索引范围内部枚举格对，并用实际几何求交叠面积。

    因此时间复杂度只与“实际相交的格对数量”及其边界条带相关，
    与两张格网总格数的乘积无关。
    """
    lo_x = max(source.origin_x, target.origin_x)
    hi_x = min(source.max_x, target.max_x)
    lo_y = max(source.origin_y, target.origin_y)
    hi_y = min(source.max_y, target.max_y)

    if lo_x >= hi_x or lo_y >= hi_y:
        return OverlapTable(source=source, target=target, pairs=())

    source_cols = _axis_index_span(
        origin=source.origin_x,
        cell_size=source.cell_width,
        count=source.cols,
        lo=lo_x,
        hi=hi_x,
    )
    target_cols = _axis_index_span(
        origin=target.origin_x,
        cell_size=target.cell_width,
        count=target.cols,
        lo=lo_x,
        hi=hi_x,
    )
    source_rows = _axis_index_span(
        origin=source.origin_y,
        cell_size=source.cell_height,
        count=source.rows,
        lo=lo_y,
        hi=hi_y,
    )
    target_rows = _axis_index_span(
        origin=target.origin_y,
        cell_size=target.cell_height,
        count=target.rows,
        lo=lo_y,
        hi=hi_y,
    )

    if source_cols is None or target_cols is None:
        return OverlapTable(source=source, target=target, pairs=())
    if source_rows is None or target_rows is None:
        return OverlapTable(source=source, target=target, pairs=())

    t_col_lo, t_col_hi = target_cols
    t_row_lo, t_row_hi = target_rows

    pairs: list[OverlapPair] = []

    for tr in range(t_row_lo, t_row_hi):
        t_y0 = target.origin_y + tr * target.cell_height
        t_y1 = t_y0 + target.cell_height

        # 该目标行与整体交集的公共段，以及对应的源行范围。
        row_lo = max(t_y0, lo_y)
        row_hi = min(t_y1, hi_y)
        row_span = _axis_index_span(
            origin=source.origin_y,
            cell_size=source.cell_height,
            count=source.rows,
            lo=row_lo,
            hi=row_hi,
        )
        if row_span is None:
            continue

        for tc in range(t_col_lo, t_col_hi):
            t_x0 = target.origin_x + tc * target.cell_width
            t_x1 = t_x0 + target.cell_width

            col_lo = max(t_x0, lo_x)
            col_hi = min(t_x1, hi_x)
            col_span = _axis_index_span(
                origin=source.origin_x,
                cell_size=source.cell_width,
                count=source.cols,
                lo=col_lo,
                hi=col_hi,
            )
            if col_span is None:
                continue

            for sr in range(row_span[0], row_span[1]):
                s_y0 = source.origin_y + sr * source.cell_height
                overlap_h = min(t_y1, s_y0 + source.cell_height) - max(t_y0, s_y0)
                if overlap_h <= 0:
                    continue
                for sc in range(col_span[0], col_span[1]):
                    s_x0 = source.origin_x + sc * source.cell_width
                    overlap_w = min(t_x1, s_x0 + source.cell_width) - max(t_x0, s_x0)
                    if overlap_w <= 0:
                        continue
                    pairs.append(
                        OverlapPair(
                            target_row=tr,
                            target_col=tc,
                            source_row=sr,
                            source_col=sc,
                            area_m2=overlap_w * overlap_h,
                        )
                    )

    return OverlapTable(
        source=source,
        target=target,
        pairs=tuple(pairs),
    )
