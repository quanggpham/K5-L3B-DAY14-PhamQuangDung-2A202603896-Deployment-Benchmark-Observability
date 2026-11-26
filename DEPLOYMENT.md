# Deployment Information

## Public URL

https://k5-l3b-day14-phamquangdung-2a202603896-deploymen-production.up.railway.app

## Platform

**Railway**. Deploy từ GitHub repo này, thư mục `06-lab-complete/` (agent Part 6), build bằng `Dockerfile` multi-stage.

| Thành phần | Cấu hình |
|---|---|
| Service agent | Root Directory `06-lab-complete`, builder `DOCKERFILE`, Healthcheck Path `/health` (theo [06-lab-complete/railway.toml](06-lab-complete/railway.toml)) |
| Service Redis | Railway Redis (database template), cùng project với agent |
| Kết nối | `REDIS_URL` của agent trỏ tới Redis qua mạng private của Railway (`redis.railway.internal:6379`). Hai service phải nằm cùng project và cùng environment thì tên `*.railway.internal` mới phân giải được |
| Cổng | Railway cấp biến `PORT`; `CMD` trong Dockerfile chạy `uvicorn ... --port ${PORT:-8000}` |

## Test Commands

Đặt biến trước khi chạy (key là giá trị `AGENT_API_KEY` trên Railway, không ghi trong repo):

```bash
BASE=https://k5-l3b-day14-phamquangdung-2a202603896-deploymen-production.up.railway.app
AGENT_API_KEY=<key trên Railway>
```

### 4 lệnh tự kiểm (CP6)

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả đã chạy:

```text
200
200
401
200
```

### Health check

```bash
curl "$BASE/health"
# {"status":"ok","version":"1.0.0","environment":"production","uptime_seconds":17.4}

curl "$BASE/ready"
# {"ready":true,"redis":"ok"}
```

### API test (có xác thực)

```bash
curl -X POST "$BASE/ask" \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "What is deployment?"}'
# {"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được."}
```

### Rate limit trên cloud (`RATE_LIMIT_PER_MINUTE=10`, state trong Redis)

Đã có 2 request trong cùng phút trước vòng lặp, nên request thứ 9 của vòng lặp là request thứ 11:

```text
200 200 200 200 200 200 200 200 429 429 429 429

HTTP/1.1 429 Too Many Requests
retry-after: 43
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
```

### Script chấm với URL public

```text
$ python check_production_ready.py --static-only --public-url $BASE
✅ 12. Public URL ready (Railway / Render config)
     ✅ Public URL: GET https://k5-l3b-day14-phamquangdung-2a202603896-deploymen-production.up.railway.app/health → 200
```

## Environment Variables Set

Đặt trên Railway (tab Variables của service agent). Chỉ ghi **tên** biến, không ghi giá trị:

| Biến | Ghi chú |
|---|---|
| `AGENT_API_KEY` | Secret, chuỗi ngẫu nhiên 43 ký tự (`secrets.token_urlsafe(32)`) |
| `REDIS_URL` | Secret (chứa mật khẩu Redis), trỏ tới service Redis của Railway |
| `ENVIRONMENT` | `production`: bắt buộc có `AGENT_API_KEY`, ẩn `/docs` |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `DAILY_BUDGET_USD` | `1.0` |
| `MONTHLY_BUDGET_USD` | `10.0` |
| `PORT` | Railway tự cấp, không tự đặt |

`LOG_LEVEL` không đặt nên dùng mặc định `INFO`.

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
