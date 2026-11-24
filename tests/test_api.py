"""API 层端到端测试（FastAPI TestClient，不经过网络）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import REGISTER_EXAMPLE, TRANSFER_EXAMPLE


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def register_example(client: TestClient) -> str:
    resp = client.post("/registrations", json=REGISTER_EXAMPLE)
    assert resp.status_code == 200, resp.text
    return resp.json()["registration_id"]


def test_health_and_root_redirect(client: TestClient):
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    redirect = client.get("/", follow_redirects=False)
    assert redirect.status_code in (302, 307)
    assert redirect.headers["location"] == "/docs"


def test_full_flow_register_transfer_and_conservation(client: TestClient):
    reg_id = register_example(client)

    info = client.get(f"/registrations/{reg_id}")
    assert info.status_code == 200
    body = info.json()
    assert body["overlap_pair_count"] > 0
    assert body["effective_target_cell_count"] == 6  # 3 列 × 2 行全部与源交叠

    resp = client.post(
        f"/registrations/{reg_id}/transfer",
        json=TRANSFER_EXAMPLE,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # 样例矩阵仅 (1,1) 一个缺测。
    assert data["missing_source_cells"] == 1
    assert data["results"], "错位样例下应有目标格产生有效覆盖"
    for cell in data["results"]:
        assert 0.0 < cell["effective_coverage_ratio"] <= 1.0
        assert cell["effective_volume_m3"] > 0.0
        assert cell["mean_depth_mm"] > 0.0

    cons = data["conservation"]
    assert cons["conservation_residual_m3"] == pytest.approx(0.0, abs=1e-9)
    assert cons["transferred_volume_m3"] + cons[
        "recorded_volume_outside_target_m3"
    ] == pytest.approx(cons["recorded_source_volume_m3"], rel=1e-12)

    # 各目标格体积之和与汇总值一致。
    assert sum(c["effective_volume_m3"] for c in data["results"]) == pytest.approx(
        cons["transferred_volume_m3"], rel=1e-12
    )


def test_repeated_registration_is_reused(client: TestClient):
    first = client.post("/registrations", json=REGISTER_EXAMPLE).json()
    second = client.post("/registrations", json=REGISTER_EXAMPLE).json()
    assert first["registration_id"] == second["registration_id"]
    assert second["reused"] is True


def test_changed_geometry_gets_new_id_and_old_still_works(client: TestClient):
    old = client.post("/registrations", json=REGISTER_EXAMPLE).json()

    changed = {
        "source": dict(REGISTER_EXAMPLE["source"]),
        "target": {**REGISTER_EXAMPLE["target"], "origin_x": 70.0},
    }
    new = client.post("/registrations", json=changed).json()
    assert new["registration_id"] != old["registration_id"]

    old_info = client.get(f"/registrations/{old['registration_id']}").json()
    assert old_info["target"]["origin_x"] == 60.0
    new_info = client.get(f"/registrations/{new['registration_id']}").json()
    assert new_info["target"]["origin_x"] == 70.0


def test_transfer_unknown_id_returns_404(client: TestClient):
    resp = client.post(
        "/registrations/nope/transfer", json=TRANSFER_EXAMPLE
    )
    assert resp.status_code == 404


def test_invalid_grid_spec_rejected(client: TestClient):
    bad = {
        "source": {**REGISTER_EXAMPLE["source"], "cell_width": -1.0},
        "target": REGISTER_EXAMPLE["target"],
    }
    assert client.post("/registrations", json=bad).status_code == 422

    missing_field = {"source": REGISTER_EXAMPLE["source"]}
    assert client.post("/registrations", json=missing_field).status_code == 422


def test_invalid_rainfall_matrix_rejected(client: TestClient):
    reg_id = register_example(client)
    url = f"/registrations/{reg_id}/transfer"

    # 维度不符（源为 4×3）。
    bad_shape = {"depth_mm": [[1.0, 2.0], [3.0, 4.0]]}
    assert client.post(url, json=bad_shape).status_code == 422

    # 负雨深。
    negative = {
        "depth_mm": [
            [-1.0, 1.0, 1.0, 1.0],
            [1.0, 1.0, 1.0, 1.0],
            [1.0, 1.0, 1.0, 1.0],
        ]
    }
    assert client.post(url, json=negative).status_code == 422

    # 行长度不齐。
    ragged = {"depth_mm": [[1.0, 1.0], [1.0], [1.0, 1.0, 1.0, 1.0]]}
    assert client.post(url, json=ragged).status_code == 422


def test_all_null_matrix_returns_empty_results(client: TestClient):
    reg_id = register_example(client)
    resp = client.post(
        f"/registrations/{reg_id}/transfer",
        json={"depth_mm": [[None] * 4 for _ in range(3)]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"] == []
    assert data["conservation"]["recorded_source_volume_m3"] == 0.0


def test_openapi_contains_try_it_examples(client: TestClient):
    schema = client.get("/openapi.json").json()
    assert "错位格网雨量换算 API" in schema["info"]["title"]
    # 两个写接口都挂了可执行示例。
    register_body = schema["paths"]["/registrations"]["post"]["requestBody"]
    transfer_body = schema["paths"][
        "/registrations/{registration_id}/transfer"
    ]["post"]["requestBody"]
    assert "错位样例" in register_body["content"]["application/json"]["examples"]
    assert (
        "一天雨深（含缺测）"
        in transfer_body["content"]["application/json"]["examples"]
    )
