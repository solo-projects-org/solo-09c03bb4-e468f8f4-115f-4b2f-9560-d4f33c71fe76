"""FastAPI 应用入口：错位格网雨量换算服务。

启动：
    uvicorn app.main:app --host 0.0.0.0 --port 8000
Swagger：
    http://localhost:8000/docs  （示例可直接点 “Try it out” 执行）
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from . import __version__
from .schemas import (
    HealthResponse,
    REGISTER_EXAMPLE,
    RegistrationInfo,
    RegisterRequest,
    RegisterResponse,
    TRANSFER_EXAMPLE,
    TransferRequest,
    TransferResponse,
)
from .service import Registry, RegistrationNotFoundError

app = FastAPI(
    title="错位格网雨量换算 API",
    version=__version__,
    description=(
        "登记源（雨量）与目标（流域模型）两张轴对齐矩形格网，"
        "按实际交叠面积把一天的源格雨深（毫米）转移到目标格，"
        "逐目标格返回有效雨水体积（立方米）、有效覆盖比例与有效面积平均雨深。\n\n"
        "- 相同格网在本次运行内复用交叠关系；原点、尺寸或行列数变化即另算并生成新登记号，"
        "旧登记号仍按原格网使用。\n"
        "- 缺测（null）范围每天按输入判断，不做缓存。\n"
        "- 只枚举实际相交的格对，不做全量两两试算。\n"
        "- 目标范围内有记录的水量守恒，响应中附带守恒核对信息。"
    ),
)

registry = Registry()


@app.exception_handler(RegistrationNotFoundError)
async def _not_found_handler(_request, exc: RegistrationNotFoundError):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=404,
        content={
            "detail": f"登记号不存在或已失效: {exc.args[0]}",
        },
    )


@app.exception_handler(ValueError)
async def _value_error_handler(_request, exc: ValueError):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health", response_model=HealthResponse, tags=["运维"])
async def health() -> HealthResponse:
    return HealthResponse(status="ok", registrations=registry.count())


@app.post(
    "/registrations",
    response_model=RegisterResponse,
    tags=["格网登记"],
    summary="登记源/目标两张格网并预计算交叠关系",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {"examples": {"错位样例": {"value": REGISTER_EXAMPLE}}}
            }
        }
    },
)
async def register_grids(request: RegisterRequest) -> RegisterResponse:
    return registry.register(request.source, request.target)


@app.get(
    "/registrations/{registration_id}",
    response_model=RegistrationInfo,
    tags=["格网登记"],
    summary="查询登记号对应的格网几何与交叠统计",
)
async def get_registration(registration_id: str) -> RegistrationInfo:
    registration = registry.get(registration_id)
    overlap = registration.overlap
    target_cells = {
        (pair.target_row, pair.target_col) for pair in overlap.pairs
    }
    return RegistrationInfo(
        registration_id=registration_id,
        source={
            "origin_x": registration.source.origin_x,
            "origin_y": registration.source.origin_y,
            "cell_width": registration.source.cell_width,
            "cell_height": registration.source.cell_height,
            "cols": registration.source.cols,
            "rows": registration.source.rows,
        },
        target={
            "origin_x": registration.target.origin_x,
            "origin_y": registration.target.origin_y,
            "cell_width": registration.target.cell_width,
            "cell_height": registration.target.cell_height,
            "cols": registration.target.cols,
            "rows": registration.target.rows,
        },
        overlap_pair_count=len(overlap.pairs),
        overlap_area_m2=overlap.total_overlap_area,
        effective_target_cell_count=len(target_cells),
    )


@app.post(
    "/registrations/{registration_id}/transfer",
    response_model=TransferResponse,
    tags=["雨量换算"],
    summary="按登记号传入一天源格雨深矩阵，返回目标格换算结果",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {"一天雨深（含缺测）": {"value": TRANSFER_EXAMPLE}}
                }
            }
        }
    },
)
async def transfer_rainfall(
    registration_id: str, request: TransferRequest
) -> TransferResponse:
    return registry.transfer(registration_id, request.depth_mm)
