# 错位格网雨量换算 API

流域模型格网与雨量格网不对齐时，按**实际交叠面积**把一天的源格雨深（毫米）
转移到目标格的 FastAPI 服务。两张格网均为轴对齐矩形规则格网，坐标使用
同一个平面米制坐标系。

## 换算口径

- 格网参数：原点 `(origin_x, origin_y)`（第 `(0,0)` 格的最小角）、
  单格宽高 `cell_width × cell_height`、行列数 `cols × rows`。
- 第 `(r, c)` 格覆盖
  `x ∈ [origin_x + c·w, origin_x + (c+1)·w]`、
  `y ∈ [origin_y + r·h, origin_y + (r+1)·h]`；行沿 y 正方向递增。
- 对每个实际相交的（目标格, 源格）对计算交叠面积 `A`（m²）。
- 源格雨深 `d`（mm）时，经该交叠面转移的水量为
  `V = A · d · 10⁻³`（m³）。
- 源格为 `null`（缺测）时，当天该格不参与转移：其交叠面积不计入有效面积，
  也不产生水量；缺测范围**不缓存**，每天按输入重新判断。
- 每个目标格汇总：
  - `effective_volume_m3`：所有交叠的有记录源格转移水量之和；
  - `effective_area_m2`：有记录源格与该目标格的交叠面积之和；
  - `effective_coverage_ratio = 有效面积 / 目标格面积`；
  - `mean_depth_mm = 有效体积 / (有效面积 · 10⁻³)`，即有效面积上的
    面积加权平均雨深。
- **无有效覆盖的目标格不出现在结果中、不报雨深**。
- 守恒：目标范围内有记录的水量全部转移。响应 `conservation` 给出
  有记录总水量、转移水量、目标外有记录水量与守恒残差（理论为 0，
  实测仅剩浮点舍入）。

## 交叠关系的缓存与失效

- 登记时以源、目标两张格网的几何元组
  （原点、格宽、格高、行数、列数）为键缓存交叠表。
- 完全相同的几何再次登记：复用交叠关系，返回原登记号，`reused=true`。
- 原点、尺寸或行列数任意一项变化：键不同，重新计算并生成**新登记号**；
  旧登记号保留登记时的几何快照，仍按原格网使用。
- 登记只枚举实际相交的格对：先求两张格网整体 AABB 的交集，
  映射到各自行/列索引范围，再在范围内枚举，不做全量两两试算。
  复杂度只与实际相交格对数量及其边界条带相关。

## API

启动后访问 Swagger：<http://localhost:8000/docs>（根路径 `/` 自动跳转），
两个写接口都内置“Try it out”可执行样例（错位 4×3/100 m 源格网 →
3×2/120 m 目标格网，雨深矩阵含一个 `null`）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/registrations` | 登记源/目标格网，预计算交叠关系，返回登记号 |
| `GET`  | `/registrations/{id}` | 查询登记号的几何与交叠统计 |
| `POST` | `/registrations/{id}/transfer` | 传入一天雨深矩阵，返回目标格换算结果与守恒核对 |
| `GET`  | `/health` | 健康检查 |

### 请求示例

```bash
# 1. 登记
curl -X POST http://localhost:8000/registrations \
  -H 'Content-Type: application/json' \
  -d '{
    "source": {"origin_x": 0, "origin_y": 0, "cell_width": 100,
               "cell_height": 100, "cols": 4, "rows": 3},
    "target": {"origin_x": 60, "origin_y": 40, "cell_width": 120,
               "cell_height": 120, "cols": 3, "rows": 2}
  }'

# 2. 换算（null = 缺测）
curl -X POST http://localhost:8000/registrations/<登记号>/transfer \
  -H 'Content-Type: application/json' \
  -d '{"depth_mm": [
    [10, 20, 30, 40],
    [15, null, 25, 35],
    [12, 22, 32, 42]
  ]}'
```

校验规则：格宽/高与行列数必须为正且坐标有限；雨深矩阵行列必须与源格网
一致，元素为非负有限数或 `null`；非法请求返回 `422`，未知登记号返回 `404`。

## 运行（Docker Compose）

```bash
docker compose up --build
# 服务监听 http://localhost:8000 ，容器带 HEALTHCHECK
```

## 本地开发与测试

```bash
python3 -m venv --without-pip .venv          # 无 ensurepip 的环境可用 get-pip 引导
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -q         # 单元 + API 测试
.venv/bin/python -m uvicorn app.main:app --port 8000
./scripts/smoke.sh                           # 对运行中的服务做端到端冒烟
```

## 目录结构

```
app/
  geometry.py   # 纯几何：格网定义、交叠索引与交叠面积（只枚举相交格对）
  schemas.py    # Pydantic 请求/响应模型与 Swagger 样例
  service.py    # 登记处（几何缓存/不可变快照）与雨量转移、守恒汇总
  main.py       # FastAPI 路由与异常处理
tests/          # pytest：几何、服务层守恒/缺测/缓存、API 端到端
scripts/smoke.sh
Dockerfile, docker-compose.yml
```

## 范围与限制

- 登记状态保存在进程内存中，服务重启后失效（本次运行内有效）。
- 未做登记数量的容量淘汰；超大矩阵请求体未设额外大小限制，
  可按部署需要在反向代理层限制。
- 假设两张格网处于同一平面米制坐标系，不做投影/坐标变换。
