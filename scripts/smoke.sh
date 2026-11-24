#!/usr/bin/env bash
# 对运行中的降雨换算服务做端到端冒烟。
# 用法：
#   1) uvicorn app.main:app --port 8000   （或 docker compose up）
#   2) ./scripts/smoke.sh [BASE_URL]
set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:8000}"
PY="${PYTHON:-python3}"

echo "== 健康检查 =="
curl -fsS "$BASE_URL/health" | "$PY" -m json.tool

echo
echo "== 1. 登记错位格网（源 4x3/100m，目标 3x2/120m，原点偏移 60,40） =="
REG_JSON=$(curl -fsS -X POST "$BASE_URL/registrations" \
  -H 'Content-Type: application/json' \
  -d '{
    "source": {"origin_x": 0, "origin_y": 0, "cell_width": 100, "cell_height": 100, "cols": 4, "rows": 3},
    "target": {"origin_x": 60, "origin_y": 40, "cell_width": 120, "cell_height": 120, "cols": 3, "rows": 2}
  }')
echo "$REG_JSON" | "$PY" -m json.tool
REG_ID=$(echo "$REG_JSON" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["registration_id"])')

echo
echo "== 2. 同一天雨深再次登记，应复用同一登记号（reused=true） =="
curl -fsS -X POST "$BASE_URL/registrations" \
  -H 'Content-Type: application/json' \
  -d '{
    "source": {"origin_x": 0, "origin_y": 0, "cell_width": 100, "cell_height": 100, "cols": 4, "rows": 3},
    "target": {"origin_x": 60, "origin_y": 40, "cell_width": 120, "cell_height": 120, "cols": 3, "rows": 2}
  }' | "$PY" -m json.tool

echo
echo "== 3. 第一天：全部有记录，统一 10mm =="
DAY1='{"depth_mm": [[10,10,10,10],[10,10,10,10],[10,10,10,10]]}'
R1=$(curl -fsS -X POST "$BASE_URL/registrations/$REG_ID/transfer" \
  -H 'Content-Type: application/json' -d "$DAY1")
echo "$R1" | "$PY" -m json.tool

echo
echo "== 4. 第二天：两个源格缺测，同一登记号直接换算（缺测按当天输入判断） =="
DAY2='{"depth_mm": [[null,10,10,10],[10,null,10,10],[10,10,10,10]]}'
R2=$(curl -fsS -X POST "$BASE_URL/registrations/$REG_ID/transfer" \
  -H 'Content-Type: application/json' -d "$DAY2")
echo "$R2" | "$PY" -m json.tool

echo
echo "== 5. 守恒核对与逐日变化断言 =="
ASSERT_SCRIPT=$(mktemp)
cat > "$ASSERT_SCRIPT" <<'EOF'
import json, sys
lines = sys.stdin.read().strip().split("\n")
r1, r2 = json.loads(lines[0]), json.loads(lines[1])
for tag, r in (("day1", r1), ("day2", r2)):
    c = r["conservation"]
    assert abs(c["conservation_residual_m3"]) < 1e-9, c
    total = sum(x["effective_volume_m3"] for x in r["results"])
    assert abs(total - c["transferred_volume_m3"]) < 1e-9
    print(f"{tag}: 转移={c['transferred_volume_m3']:.4f} m3, "
          f"目标外={c['recorded_volume_outside_target_m3']:.4f} m3, "
          f"有记录总量={c['recorded_source_volume_m3']:.4f} m3, "
          f"缺测格={r['missing_source_cells']}")
assert r2["missing_source_cells"] == 2
assert r2["conservation"]["recorded_source_volume_m3"] < r1["conservation"]["recorded_source_volume_m3"]
print("OK: 守恒成立，缺测变化已被第二天输入正确反映")
EOF
printf '%s\n%s\n' "$R1" "$R2" | "$PY" "$ASSERT_SCRIPT"
rm -f "$ASSERT_SCRIPT"

echo
echo "== 6. 目标原点变更后重新登记，应生成新登记号；旧号几何不变 =="
NEW_REG=$(curl -fsS -X POST "$BASE_URL/registrations" \
  -H 'Content-Type: application/json' \
  -d '{
    "source": {"origin_x": 0, "origin_y": 0, "cell_width": 100, "cell_height": 100, "cols": 4, "rows": 3},
    "target": {"origin_x": 70, "origin_y": 40, "cell_width": 120, "cell_height": 120, "cols": 3, "rows": 2}
  }')
echo "$NEW_REG" | "$PY" -m json.tool
NEW_ID=$(echo "$NEW_REG" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["registration_id"])')
test "$NEW_ID" != "$REG_ID"
OLD_TARGET_X=$(curl -fsS "$BASE_URL/registrations/$REG_ID" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["target"]["origin_x"])')
test "$OLD_TARGET_X" = "60.0"
echo "OK: 旧登记号仍使用原格网（origin_x=$OLD_TARGET_X）"

echo
echo "== 7. 未知登记号返回 404 =="
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  "$BASE_URL/registrations/unknown/transfer" \
  -H 'Content-Type: application/json' -d "$DAY1")
test "$code" = "404"
echo "OK: 404"

echo
echo "冒烟全部通过 ✔"
