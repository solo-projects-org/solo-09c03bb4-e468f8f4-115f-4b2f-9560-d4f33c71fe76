"""服务层：登记缓存、水量守恒、缺测处理。"""

from __future__ import annotations

import pytest

from app.schemas import GridSpec
from app.service import RegistrationNotFoundError, Registry


def spec(origin_x, origin_y, w, h, cols, rows) -> GridSpec:
    return GridSpec(
        origin_x=origin_x,
        origin_y=origin_y,
        cell_width=w,
        cell_height=h,
        cols=cols,
        rows=rows,
    )


SRC = spec(0.0, 0.0, 100.0, 100.0, 4, 3)
TGT = spec(60.0, 40.0, 120.0, 120.0, 3, 2)


def test_identical_registration_reuses_overlap():
    registry = Registry()
    first = registry.register(SRC, TGT)
    second = registry.register(SRC.model_copy(deep=True), TGT.model_copy(deep=True))
    assert first.registration_id == second.registration_id
    assert first.reused is False
    assert second.reused is True
    assert registry.count() == 1


def test_geometry_change_creates_new_id_and_old_id_kept():
    registry = Registry()
    first = registry.register(SRC, TGT)

    changed_target = TGT.model_copy(update={"origin_x": 70.0})
    second = registry.register(SRC, changed_target)

    assert second.registration_id != first.registration_id
    assert second.reused is False
    assert registry.count() == 2

    # 旧登记号仍按原格网几何使用。
    old = registry.get(first.registration_id)
    assert old.target.origin_x == 60.0
    new = registry.get(second.registration_id)
    assert new.target.origin_x == 70.0


@pytest.mark.parametrize(
    "field,new_value",
    [
        ("origin_x", 1.0),
        ("cell_width", 99.0),
        ("cell_height", 99.0),
        ("cols", 5),
        ("rows", 4),
    ],
)
def test_any_geometry_field_change_invalidates_cache(field, new_value):
    registry = Registry()
    first = registry.register(SRC, TGT)
    changed = SRC.model_copy(update={field: new_value})
    second = registry.register(changed, TGT)
    assert second.registration_id != first.registration_id


def test_transfer_conservation_with_full_coverage():
    """源范围完全被目标包住时，有记录水量应 100% 转移。"""
    # 源 2×2 格、每格 100m²；目标一个大格完全包住源。
    src = spec(0.0, 0.0, 10.0, 10.0, 2, 2)
    tgt = spec(-50.0, -50.0, 200.0, 200.0, 1, 1)
    registry = Registry()
    reg = registry.register(src, tgt)

    response = registry.transfer(
        reg.registration_id,
        [[10.0, 20.0], [30.0, None]],
    )
    assert len(response.results) == 1
    cell = response.results[0]
    # 有效面积 = 3 个有记录源格 × 100 m²（缺测格不计入）。
    assert cell.effective_area_m2 == pytest.approx(300.0)
    # 有效覆盖比例只统计有效面积，缺测格不算有效覆盖。
    assert cell.effective_coverage_ratio == pytest.approx(300.0 / 40_000.0)
    # 平均雨深为有记录源格的等权（等面积）平均。
    assert cell.mean_depth_mm == pytest.approx(20.0)
    # 体积 = 面积 m² × 雨深 mm × 1e-3。
    assert cell.effective_volume_m3 == pytest.approx(300.0 * 20.0 * 0.001)

    cons = response.conservation
    assert cons.recorded_source_volume_m3 == pytest.approx(
        100.0 * (10 + 20 + 30) * 0.001
    )
    assert cons.transferred_volume_m3 == pytest.approx(
        cons.recorded_source_volume_m3
    )
    assert cons.recorded_volume_outside_target_m3 == pytest.approx(0.0)
    assert cons.conservation_residual_m3 == pytest.approx(0.0, abs=1e-12)
    assert response.missing_source_cells == 1


def test_transfer_conservation_with_partial_coverage():
    """目标只覆盖源的一部分：目标外有记录水量须可核对、总量守恒。"""
    registry = Registry()
    reg = registry.register(SRC, TGT)
    # 全部有记录，统一 10 mm，便于手算。
    matrix = [[10.0 for _ in range(4)] for _ in range(3)]
    response = registry.transfer(reg.registration_id, matrix)

    reg_data = registry.get(reg.registration_id)
    overlap_area = reg_data.overlap.total_overlap_area
    source_area = 4 * 3 * 10_000.0

    cons = response.conservation
    # 统一雨深下，转移水量 = 交叠面积 × 10mm。
    assert cons.transferred_volume_m3 == pytest.approx(overlap_area * 10.0 * 0.001)
    assert cons.recorded_source_volume_m3 == pytest.approx(
        source_area * 10.0 * 0.001
    )
    assert cons.recorded_volume_outside_target_m3 == pytest.approx(
        (source_area - overlap_area) * 10.0 * 0.001
    )
    assert cons.conservation_residual_m3 == pytest.approx(0.0, abs=1e-10)

    # 各目标格有效体积之和等于总转移水量。
    assert sum(r.effective_volume_m3 for r in response.results) == pytest.approx(
        cons.transferred_volume_m3
    )
    # 统一雨深下每个有覆盖目标格的平均雨深仍是 10 mm。
    assert all(r.mean_depth_mm == pytest.approx(10.0) for r in response.results)


def test_missing_pattern_changes_day_to_day_without_reregistration():
    """同一登记号连续两天，缺测范围不同，结果随输入变化且无需重新登记。"""
    registry = Registry()
    reg = registry.register(SRC, TGT)

    day1 = [[10.0 for _ in range(4)] for _ in range(3)]
    day2 = [[10.0 for _ in range(4)] for _ in range(3)]
    day2[0][0] = None  # 仅第二天把一个源格标缺测

    r1 = registry.transfer(reg.registration_id, day1)
    r2 = registry.transfer(reg.registration_id, day2)

    assert r1.missing_source_cells == 0
    assert r2.missing_source_cells == 1

    # 缺测格若与某目标格交叠，第二天的有效面积与体积都应下降。
    reg_data = registry.get(reg.registration_id)
    missing_contrib_area = sum(
        p.area_m2
        for p in reg_data.overlap.pairs
        if p.source_row == 0 and p.source_col == 0
    )
    area_diff = sum(r.effective_area_m2 for r in r1.results) - sum(
        r.effective_area_m2 for r in r2.results
    )
    assert area_diff == pytest.approx(missing_contrib_area)

    vol_diff = r1.conservation.transferred_volume_m3 - (
        r2.conservation.transferred_volume_m3
    )
    assert vol_diff == pytest.approx(missing_contrib_area * 10.0 * 0.001)

    # 第二天的守恒仍然成立（缺测格直接不进入有记录水量）。
    assert r2.conservation.conservation_residual_m3 == pytest.approx(0.0, abs=1e-12)
    assert r2.conservation.recorded_source_volume_m3 == pytest.approx(
        r1.conservation.recorded_source_volume_m3 - 10_000.0 * 10.0 * 0.001
    )


def test_no_effective_cells_omits_depth_report():
    """目标格无有效覆盖时不报雨深；整张表无交叠时结果为空列表。"""
    disjoint_target = spec(900.0, 900.0, 120.0, 120.0, 3, 2)
    registry = Registry()
    reg = registry.register(SRC, disjoint_target)
    response = registry.transfer(
        reg.registration_id, [[10.0] * 4 for _ in range(3)]
    )
    assert response.results == []
    # 全部有记录水量都落在目标范围之外，守恒依旧成立。
    cons = response.conservation
    assert cons.transferred_volume_m3 == 0.0
    assert cons.recorded_volume_outside_target_m3 == pytest.approx(
        cons.recorded_source_volume_m3
    )

    # 全部缺测时即便几何上有交叠，也没有任何有效格。
    reg2 = registry.register(SRC, TGT)
    all_missing = registry.transfer(
        reg2.registration_id, [[None] * 4 for _ in range(3)]
    )
    assert all_missing.results == []
    assert all_missing.missing_source_cells == 12
    assert all_missing.conservation.recorded_source_volume_m3 == 0.0


def test_wrong_matrix_dimensions_rejected():
    registry = Registry()
    reg = registry.register(SRC, TGT)
    with pytest.raises(ValueError, match="维度应为"):
        registry.transfer(reg.registration_id, [[1.0, 2.0], [3.0, 4.0]])


def test_unknown_registration_raises_not_found():
    registry = Registry()
    with pytest.raises(RegistrationNotFoundError):
        registry.get("does-not-exist")


def test_area_weighted_mean_depth_with_varied_depths():
    """非均匀雨深：平均雨深必须按交叠面积加权，而非简单平均。"""
    # 一个目标格被两个等面积源格各覆盖一半（各 50 m²），雨深分别 10、30 mm。
    src = spec(0.0, 0.0, 10.0, 10.0, 2, 1)  # 两个 100 m² 源格
    tgt = spec(0.0, 0.0, 10.0, 10.0, 1, 1)  # 目标格只与源格 (0,0) 同尺寸
    registry = Registry()
    reg = registry.register(src, tgt)
    response = registry.transfer(reg.registration_id, [[10.0, 30.0]])
    cell = response.results[0]
    assert cell.effective_area_m2 == pytest.approx(100.0)
    assert cell.mean_depth_mm == pytest.approx(10.0)
    assert cell.effective_volume_m3 == pytest.approx(1.0)
