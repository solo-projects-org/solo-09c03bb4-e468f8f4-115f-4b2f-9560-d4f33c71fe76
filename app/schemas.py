"""API 请求/响应模型（Pydantic v2）。"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .geometry import GridMetrics


class GridSpec(BaseModel):
    """一张轴对齐矩形格网的登记参数（坐标单位：米）。"""

    model_config = ConfigDict(extra="forbid")

    origin_x: float = Field(..., description="原点 x 坐标（第 0 列最小角），米")
    origin_y: float = Field(..., description="原点 y 坐标（第 0 行最小角），米")
    cell_width: float = Field(..., gt=0, description="单格宽度（x 方向），米，必须为正")
    cell_height: float = Field(..., gt=0, description="单格高度（y 方向），米，必须为正")
    cols: int = Field(..., gt=0, description="列数（x 方向格数），正整数")
    rows: int = Field(..., gt=0, description="行数（y 方向格数），正整数")

    @model_validator(mode="after")
    def _reject_non_finite(self) -> "GridSpec":
        for name, value in (
            ("origin_x", self.origin_x),
            ("origin_y", self.origin_y),
            ("cell_width", self.cell_width),
            ("cell_height", self.cell_height),
        ):
            if not math.isfinite(value):
                raise ValueError(f"{name} 必须是有限数值")
        return self

    def to_metrics(self) -> GridMetrics:
        return GridMetrics(
            origin_x=float(self.origin_x),
            origin_y=float(self.origin_y),
            cell_width=float(self.cell_width),
            cell_height=float(self.cell_height),
            cols=int(self.cols),
            rows=int(self.rows),
        )


class RegisterRequest(BaseModel):
    """登记一对源/目标格网。"""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": []})

    source: GridSpec = Field(..., description="雨量源格网")
    target: GridSpec = Field(..., description="流域模型目标格网")


class RegisterResponse(BaseModel):
    registration_id: str = Field(..., description="登记号，后续换算按此号引用")
    overlap_pair_count: int = Field(..., description="实际相交的（目标格, 源格）对数")
    overlap_area_m2: float = Field(..., description="两张格网整体交叠面积，平方米")
    reused: bool = Field(
        ..., description="本次是否复用了运行内已有的相同几何交叠关系"
    )


class RainfallDay(BaseModel):
    """一天的源格雨深矩阵（毫米），行列与源格网一致；null 表示缺测。"""

    model_config = ConfigDict(extra="forbid")

    depth_mm: list[list[float | None]] = Field(
        ...,
        description="源格雨深矩阵，外层为行、内层为列，单位毫米；null 为缺测",
    )

    @model_validator(mode="after")
    def _validate_matrix(self) -> "RainfallDay":
        matrix = self.depth_mm
        if not matrix:
            raise ValueError("depth_mm 至少要有一行")
        width = len(matrix[0])
        if width == 0:
            raise ValueError("depth_mm 不允许空行")
        for r, row in enumerate(matrix):
            if len(row) != width:
                raise ValueError(
                    f"depth_mm 第 {r} 行长度为 {len(row)}，与首行 {width} 不一致"
                )
            for c, value in enumerate(row):
                if value is None:
                    continue
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ValueError(f"depth_mm[{r}][{c}] 必须是数值或 null")
                if not math.isfinite(value):
                    raise ValueError(f"depth_mm[{r}][{c}] 不允许 NaN/Infinity")
                if value < 0:
                    raise ValueError(f"depth_mm[{r}][{c}] 雨深不能为负")
        return self


class TransferRequest(RainfallDay):
    """换算请求：按登记号传入一天的源格雨深矩阵。"""


class TargetCellResult(BaseModel):
    """单个目标格的换算结果（仅返回有有效覆盖的目标格）。"""

    target_row: int
    target_col: int
    effective_volume_m3: float = Field(..., description="有效雨水体积，立方米")
    effective_coverage_ratio: float = Field(
        ..., ge=0.0, le=1.0, description="有效覆盖比例：有效交叠面积 / 目标格面积"
    )
    effective_area_m2: float = Field(..., description="有效交叠面积，平方米")
    mean_depth_mm: float = Field(
        ..., description="有效面积上的面积加权平均雨深，毫米"
    )


class ConservationInfo(BaseModel):
    """水量守恒核对信息。"""

    recorded_source_volume_m3: float = Field(
        ..., description="源矩阵中有记录（非缺测）格的总水量，立方米"
    )
    transferred_volume_m3: float = Field(
        ...,
        description="实际转移到目标范围内的有记录水量（等于各目标格有效体积之和），立方米",
    )
    recorded_volume_outside_target_m3: float = Field(
        ..., description="落在目标范围之外、未被任何目标格接收的有记录水量，立方米"
    )
    conservation_residual_m3: float = Field(
        ...,
        description=(
            "守恒残差 = 有记录总水量 -（转移水量 + 目标外有记录水量）；"
            "理论为 0，仅剩浮点舍入误差"
        ),
    )


class TransferResponse(BaseModel):
    registration_id: str
    source_rows: int
    source_cols: int
    target_rows: int
    target_cols: int
    target_cell_area_m2: float = Field(..., description="单个目标格面积，平方米")
    missing_source_cells: int = Field(..., description="本次输入中缺测（null）的源格数")
    results: list[TargetCellResult] = Field(
        ..., description="有有效覆盖的目标格列表；无有效覆盖的格不在此列、不报雨深"
    )
    conservation: ConservationInfo


class RegistrationInfo(BaseModel):
    """登记信息查询结果。"""

    registration_id: str
    source: GridSpec
    target: GridSpec
    overlap_pair_count: int
    overlap_area_m2: float
    effective_target_cell_count: int = Field(
        ..., description="与源格网存在正面积交叠的目标格数量"
    )


class HealthResponse(BaseModel):
    status: str
    registrations: int


# Swagger 中“试一试”的可执行样例。
REGISTER_EXAMPLE: dict[str, Any] = {
    "source": {
        # 源雨量格网：4 列 × 3 行，每格 100m × 100m，原点 (0, 0)
        "origin_x": 0.0,
        "origin_y": 0.0,
        "cell_width": 100.0,
        "cell_height": 100.0,
        "cols": 4,
        "rows": 3,
    },
    "target": {
        # 目标流域格网：3 列 × 2 行，每格 120m × 120m，
        # 原点偏移 (60, 40)，与源格网刻意错位、不对齐
        "origin_x": 60.0,
        "origin_y": 40.0,
        "cell_width": 120.0,
        "cell_height": 120.0,
        "cols": 3,
        "rows": 2,
    },
}

# 与上面源格网 4×3 配套的一天雨深（毫米），含一个 null 缺测格。
TRANSFER_EXAMPLE: dict[str, Any] = {
    "depth_mm": [
        [10.0, 20.0, 30.0, 40.0],
        [15.0, None, 25.0, 35.0],
        [12.0, 22.0, 32.0, 42.0],
    ]
}
