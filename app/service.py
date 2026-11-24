"""格网登记与雨量换算的服务层。

设计要点
--------
* 登记号在本次运行内有效；每个登记号保存登记当时的格网几何快照，
  之后即便用“同名含义”的新参数再次登记，旧号仍按原几何使用。
* 交叠表以源/目标几何元组为缓存键：原点、尺寸或行列数任意一项变化，
  键即不同，会重新计算并生成新登记号；完全相同的几何才复用交叠关系。
* 缺测（null）范围不做缓存，每次换算按当天输入逐格判断。
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass

from .geometry import GridMetrics, OverlapPair, OverlapTable, build_overlap
from .schemas import (
    ConservationInfo,
    GridSpec,
    RegisterResponse,
    TargetCellResult,
    TransferResponse,
)

_MM_TO_M = 0.001  # 雨深毫米 → 米；面积 m² × 深度 m = 体积 m³


class RegistrationNotFoundError(KeyError):
    """登记号不存在。"""


@dataclass(frozen=True)
class Registration:
    registration_id: str
    source: GridMetrics
    target: GridMetrics
    overlap: OverlapTable


class Registry:
    """线程安全的运行内登记处。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_id: dict[str, Registration] = {}
        # 相同几何（源, 目标）→ 已有的登记号，用于复用交叠关系。
        self._by_geometry: dict[tuple[GridMetrics, GridMetrics], str] = {}

    def register(self, source_spec: GridSpec, target_spec: GridSpec) -> RegisterResponse:
        source = source_spec.to_metrics()
        target = target_spec.to_metrics()
        key = (source, target)

        with self._lock:
            existing_id = self._by_geometry.get(key)
            if existing_id is not None:
                registration = self._by_id[existing_id]
                return RegisterResponse(
                    registration_id=existing_id,
                    overlap_pair_count=len(registration.overlap.pairs),
                    overlap_area_m2=registration.overlap.total_overlap_area,
                    reused=True,
                )

            overlap = build_overlap(source, target)
            registration_id = uuid.uuid4().hex
            registration = Registration(
                registration_id=registration_id,
                source=source,
                target=target,
                overlap=overlap,
            )
            self._by_id[registration_id] = registration
            self._by_geometry[key] = registration_id

        return RegisterResponse(
            registration_id=registration_id,
            overlap_pair_count=len(overlap.pairs),
            overlap_area_m2=overlap.total_overlap_area,
            reused=False,
        )

    def get(self, registration_id: str) -> Registration:
        registration = self._by_id.get(registration_id)
        if registration is None:
            raise RegistrationNotFoundError(registration_id)
        return registration

    def count(self) -> int:
        return len(self._by_id)

    def transfer(
        self,
        registration_id: str,
        depth_mm: list[list[float | None]],
    ) -> TransferResponse:
        registration = self.get(registration_id)
        source = registration.source
        target = registration.target
        overlap = registration.overlap

        if len(depth_mm) != source.rows or any(
            len(row) != source.cols for row in depth_mm
        ):
            raise ValueError(
                f"雨深矩阵维度应为 {source.rows} 行 × {source.cols} 列，"
                f"实际为 {len(depth_mm)} 行 × "
                f"{len(depth_mm[0]) if depth_mm else 0} 列"
            )

        missing = sum(1 for row in depth_mm for value in row if value is None)

        # 每个目标格按源格累计有效面积与有效体积。
        effective_area: dict[tuple[int, int], float] = {}
        effective_volume: dict[tuple[int, int], float] = {}

        transferred_volume = 0.0
        # 目标范围之外的有记录水量，通过“有记录总水量 - 转移水量”得到。
        recorded_source_volume = 0.0

        # 有记录源格的总水量（独立累加，便于守恒核对）。
        for sr, row in enumerate(depth_mm):
            for sc, value in enumerate(row):
                if value is not None:
                    recorded_source_volume += (
                        source.cell_area * float(value) * _MM_TO_M
                    )

        # 交叠表仅含实际相交格对；缺测源格当天直接跳过，
        # 其交叠面积不计入任何目标格的有效面积。
        pair: OverlapPair
        for pair in overlap.pairs:
            depth = depth_mm[pair.source_row][pair.source_col]
            if depth is None:
                continue
            cell_key = (pair.target_row, pair.target_col)
            effective_area[cell_key] = effective_area.get(cell_key, 0.0) + pair.area_m2
            volume = pair.area_m2 * float(depth) * _MM_TO_M
            effective_volume[cell_key] = (
                effective_volume.get(cell_key, 0.0) + volume
            )
            transferred_volume += volume

        target_cell_area = target.cell_area
        results: list[TargetCellResult] = []
        # 排序保证输出稳定、可对照矩阵位置阅读。
        for (tr, tc) in sorted(effective_area):
            area = effective_area[(tr, tc)]
            if area <= 0.0:
                continue
            volume = effective_volume[(tr, tc)]
            results.append(
                TargetCellResult(
                    target_row=tr,
                    target_col=tc,
                    effective_volume_m3=volume,
                    mean_depth_mm=volume / (area * _MM_TO_M),
                    effective_coverage_ratio=area / target_cell_area,
                    effective_area_m2=area,
                )
            )

        outside_volume = recorded_source_volume - transferred_volume
        return TransferResponse(
            registration_id=registration_id,
            source_rows=source.rows,
            source_cols=source.cols,
            target_rows=target.rows,
            target_cols=target.cols,
            target_cell_area_m2=target_cell_area,
            missing_source_cells=missing,
            results=results,
            conservation=ConservationInfo(
                recorded_source_volume_m3=recorded_source_volume,
                transferred_volume_m3=transferred_volume,
                recorded_volume_outside_target_m3=outside_volume,
                conservation_residual_m3=(
                    recorded_source_volume - (transferred_volume + outside_volume)
                ),
            ),
        )
