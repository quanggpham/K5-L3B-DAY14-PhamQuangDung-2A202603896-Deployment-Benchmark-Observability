# Day 14 Lab - Mission Answers

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns found

Trong `01-localhost-vs-production/develop/app.py`:

1. **Hardcode secret trong code** (dòng 17–18): `OPENAI_API_KEY = "sk-..."` và `DATABASE_URL = "postgresql://admin:password123@..."`. Push lên GitHub là lộ key và mật khẩu DB. Xoá ở commit sau cũng không đủ, vì secret vẫn nằm trong lịch sử git.
2. **Log ra secret** (dòng 34): `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")`. Lúc chạy, log server in nguyên `[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this`. Log thường được gửi sang hệ thống khác (Datadog, Loki…), nên secret lan ra cả những chỗ đó.
3. **Dùng `print()` thay cho logging** (dòng 33, 38): không có level, không có timestamp, không có cấu trúc. Không lọc được theo mức độ và log aggregator không parse được. Còn log toàn bộ câu hỏi và câu trả lời của người dùng, có thể chứa dữ liệu cá nhân.
4. **Không có config management** (dòng 21–22): `DEBUG = True`, `MAX_TOKENS = 500` ghi cứng. Muốn đổi giữa dev, staging và prod thì phải sửa code rồi deploy lại.
5. **Không có health check** (dòng 42): gọi `GET /health` trả **404**. Platform (Railway/Render/K8s) không biết agent còn sống hay đã treo, nên không tự restart, và load balancer không biết lúc nào nên ngừng gửi traffic.
6. **Bind `host="localhost"`** (dòng 51): chỉ nhận kết nối từ chính máy đó. Chạy trong container thì traffic từ ngoài không vào được.
7. **Port cố định 8000** (dòng 52): Railway/Render cấp port qua biến `PORT`, app bỏ qua biến này nên platform không route tới được.
8. **`reload=True`** (dòng 53): chế độ dev, theo dõi file và tự restart. Tốn tài nguyên và không ổn định khi chạy production.
9. **Không xử lý shutdown**: không có lifespan, không bắt SIGTERM. Khi platform tắt container (deploy bản mới, scale down), request đang chạy bị cắt ngang.
10. **Không validate input**: `question` nhận qua query string, không giới hạn độ dài, không kiểm tra rỗng.

### Exercise 1.2: Chạy bản develop

```text
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
content-type: application/json

{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 422 Unprocessable Entity
{"detail":[{"type":"missing","loc":["query","question"],"msg":"Field required","input":null}]}

$ curl -i http://localhost:8000/health
HTTP/1.1 404 Not Found
```

Log server lúc nhận request:

```text
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [2004] using StatReload
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.
```

**Nhận xét:** app chạy được và trả lời 200, nhưng chưa sẵn sàng cho production:
- log làm lộ API key;
- không có `/health` (trả 404);
- chỉ nghe ở `localhost` (log ghi `Uvicorn running on http://localhost:8000`);
- đang ở chế độ reload (`Started reloader process`).

### Exercise 1.3: Comparison table

Bản production chạy với `.env.example` (`python app.py`):

```text
GET  /         → 200 {"app":"AI Agent","version":"1.0.0","environment":"development","status":"running"}
GET  /health   → 200 {"status":"ok","uptime_seconds":6.7,"version":"1.0.0",...}
GET  /ready    → 200 {"ready":true}
GET  /metrics  → 200 {"uptime_seconds":7.3,...}
POST /ask {"question":"Hello"} → 200 {"question":"Hello","answer":"...","model":"gpt-4o-mini"}
POST /ask {}                   → 422 {"detail":"question field is required"}
Uvicorn running on http://0.0.0.0:8000
```

| Feature | Develop | Production | Tại sao quan trọng? |
|---------|---------|------------|---------------------|
| Config | Ghi cứng trong code (`OPENAI_API_KEY`, `DEBUG`, `MAX_TOKENS`) | `config.py` đọc mọi giá trị từ biến môi trường, có default an toàn (`DEBUG=false`), `validate()` dừng app ngay khi `ENVIRONMENT=production` mà thiếu `AGENT_API_KEY` | Cùng một bản build chạy được ở dev, staging, prod, chỉ đổi biến môi trường. Secret không nằm trong git. Thiếu config quan trọng thì app báo lỗi ngay lúc khởi động, không đợi tới request đầu tiên |
| Secrets trong log | In nguyên API key | Chỉ log `question_length`, `response_length`, `client_ip`, không log secret hay nội dung câu hỏi | Log được nhiều người và nhiều hệ thống đọc, lộ ở log cũng là lộ secret. Không log nội dung câu hỏi thì giảm rủi ro rò dữ liệu cá nhân |
| Health check | Không có (`/health` → 404) | `/health` (liveness, kèm uptime và version) và `/ready` (readiness, trả 503 khi chưa sẵn sàng) | Platform dùng `/health` để biết khi nào cần restart container bị treo. Load balancer dùng `/ready` để chỉ gửi traffic vào instance đã khởi động xong. Tách hai endpoint để instance đang khởi động không bị kill nhầm |
| Logging | `print()` dạng text | `logging` có level và format JSON (`time`, `level`, `msg`) | Log có cấu trúc thì lọc và tìm được theo trường (vd `event=agent_request`), tính được metric, đặt được alert. Có level thì tắt được DEBUG trên production |
| Shutdown | Đột ngột, không có hook | `lifespan` đặt `is_ready=False` rồi dọn dẹp, có handler cho SIGTERM | Khi deploy bản mới hoặc scale down, platform gửi SIGTERM. Tắt an toàn thì request đang chạy vẫn xong, và `/ready` chuyển 503 để load balancer ngừng gửi request mới |
| Host binding | `localhost` | `0.0.0.0` (đọc từ `HOST`) | Trong container, `localhost` chỉ là chính container đó. Phải nghe `0.0.0.0` thì traffic từ ngoài mới vào được |
| Port | Cố định 8000 | Đọc từ `PORT` | Railway/Render cấp port động qua `PORT` |
| Reload / debug | `reload=True` luôn bật | `reload` chỉ bật khi `DEBUG=true` | Reload theo dõi file liên tục, tốn tài nguyên và có thể restart giữa lúc đang xử lý request |
| Input | Query string, không kiểm tra | JSON body, câu hỏi rỗng → 422 có thông báo rõ | Client gửi sai nhận mã lỗi rõ ràng thay vì để lỗi xảy ra ở tầng sâu hơn |
| CORS | Không cấu hình | `ALLOWED_ORIGINS` từ biến môi trường | Chỉ những frontend được phép mới gọi API từ trình duyệt |

### Quan sát thêm khi chạy bản production

Hai lỗi chưa nằm trong bảng trên:

1. **Log JSON của app không hiện ra khi chạy mặc định.**
   - Nguyên nhân: `config.py` gọi `logging.warning("OPENAI_API_KEY not set …")` *trước* khi `app.py` gọi `logging.basicConfig(...)`. Lần gọi đó tự cấu hình root logger ở level WARNING, nên `basicConfig` sau đó không có tác dụng và mọi log INFO bị bỏ.
   - Cách kiểm chứng: chạy lại với `OPENAI_API_KEY=dummy` thì không còn warning, và các dòng `startup` / `agent_request` xuất hiện.
   - Cách sửa: cấu hình logging trước khi import `config`, hoặc gọi `basicConfig(..., force=True)`.
2. **Dòng log không phải JSON hợp lệ.** Format là `"msg":"%(message)s"`, trong khi message lại là `json.dumps(...)`. Kết quả là dấu `"` lồng nhau không được escape:
   ```text
   {"time":"...","level":"INFO","msg":"{"event": "agent_request", "question_length": 5, "client_ip": "127.0.0.1"}"}
   ```
   Log aggregator sẽ không parse được dòng này. Muốn mỗi dòng là một JSON object hợp lệ thì phải tạo cả object bằng `json.dumps` trong một `logging.Formatter` riêng.

Ngoài ra, `config.py` không gọi `load_dotenv()` nên file `.env` không được nạp. App chạy đúng chỉ vì giá trị default trùng với `.env.example`. Khi deploy thì biến môi trường do platform cung cấp, nên điều này không ảnh hưởng.

## Part 2: Docker

### Exercise 2.1: Dockerfile questions

File `02-docker/develop/Dockerfile`:

1. **Base image:** `python:3.11`, bản Python đầy đủ chạy trên Debian (đo trong container: `Debian GNU/Linux 13 (trixie)`, `Python 3.11.18`). Bản này có sẵn gcc, header và nhiều công cụ build, nên rất nặng.
2. **Working directory:** `/app`. Mọi lệnh `COPY`, `RUN` và `CMD` sau `WORKDIR /app` đều chạy trong thư mục này. Docker tự tạo thư mục nếu chưa có.
3. **Vì sao `COPY requirements.txt` trước `COPY app.py`:**
   - Docker cache theo từng layer. Một layer chỉ được build lại khi lệnh của nó hoặc file nó COPY thay đổi, và khi đó mọi layer phía sau cũng phải build lại.
   - `requirements.txt` ít khi đổi, còn code đổi liên tục. Nếu COPY requirements trước thì layer `pip install` (53.3 MB, lâu nhất, đo bằng `docker history`) được giữ trong cache. Sửa code chỉ phải build lại layer `COPY app.py` vài KB.
   - Nếu `COPY . .` trước `pip install` thì sửa một dòng code cũng phải cài lại toàn bộ dependencies.
4. **`CMD` khác `ENTRYPOINT`:**
   - `ENTRYPOINT` là chương trình chính, cố định của container.
   - `CMD` là lệnh hoặc tham số mặc định, bị ghi đè khi truyền lệnh khác vào `docker run <image> <cmd>`.
   - Khi có cả hai, `CMD` trở thành tham số nối sau `ENTRYPOINT`.
   - Ở đây chỉ dùng `CMD ["python", "app.py"]`. Vì là exec form (mảng JSON) nên `python` chạy với PID 1 và nhận trực tiếp SIGTERM từ `docker stop`. Mình xác nhận bằng log `Started server process [1]`.

### Exercise 2.2: Build và run

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
$ docker run -d --name day14-develop -p 8000:8000 my-agent:develop

$ curl -i -X POST "http://localhost:8000/ask?question=What%20is%20Docker%3F"
HTTP/1.1 200 OK
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}

$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.8,"container":true}

$ docker logs day14-develop
INFO:     Started server process [1]
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     172.17.0.1:37544 - "POST /ask?question=What%20is%20Docker%3F HTTP/1.1" 200 OK

$ docker exec day14-develop whoami
root
```

Bản develop nhận `question` qua query string (gửi JSON sẽ bị 422), nên lệnh curl khác với `CODE_LAB.md`. Container chạy bằng **root**: nếu app bị khai thác, kẻ tấn công có toàn quyền trong container.

### Exercise 2.3: Image size comparison

Số đo bằng `docker images my-agent` (Docker 29.3.1):

```text
IMAGE               ID             DISK USAGE   CONTENT SIZE
my-agent:advanced   43b4477c02fb        240MB         57.4MB
my-agent:develop    7987e2ce6c40       1.67GB          426MB
```

- Develop: **1.67 GB** (content 426 MB)
- Production: **240 MB** (content 57.4 MB)
- Difference: nhỏ hơn **85.6%** theo disk usage ((1670 − 240) / 1670), và **86.5%** theo content size ((426.3 − 57.4) / 426.3)

Cột `DISK USAGE` là dung lượng image chiếm trên ổ khi đã giải nén. Cột `CONTENT SIZE` là dung lượng nén của các layer, cũng là lượng dữ liệu phải tải khi pull/push.

**Stage 1 (`builder`) làm gì:**
- Dùng `python:3.11-slim`, cài thêm `gcc` và `libpq-dev` để biên dịch được các package có phần C.
- Chạy `pip install --user`, nên mọi package nằm gọn trong `/root/.local`.
- Stage này chỉ dùng để build, không đi vào image cuối.

**Stage 2 (`runtime`) làm gì:**
- Bắt đầu lại từ một `python:3.11-slim` sạch.
- Chỉ COPY `/root/.local` từ builder (40.8 MB, theo `docker history`), cùng `main.py` và `utils/`.
- Tạo user `appuser` và chạy bằng user này (`whoami` → `appuser`).
- Có `HEALTHCHECK` gọi `/health`. `CMD` chạy uvicorn với 2 worker.

**Vì sao image nhỏ hơn:**
- Base `slim` bỏ hết công cụ build và tài liệu mà bản `python:3.11` full mang theo. Đây là phần giảm nhiều nhất.
- `gcc`, `libpq-dev` và apt cache chỉ có ở stage builder. Kiểm trong image cuối: `which gcc` → `no gcc`.
- Image cuối chỉ chứa những layer của stage cuối, nên mọi thứ dùng để build đều bị bỏ lại.

Ngoài dung lượng, image nhỏ còn có lợi về bảo mật: ít package hơn nghĩa là ít CVE hơn, không có compiler, không chạy bằng root.

### Exercise 2.4: Docker Compose stack

**Sơ đồ (`02-docker/production/docker-compose.yml`):**

```text
                   host (Windows)
                        │  :8088 (xem ghi chú bên dưới)
                        ▼
              ┌──────────────────┐
              │  nginx:alpine    │  reverse proxy + rate limit 10r/s/IP (burst 20)
              │  :80             │  + security headers
              └────────┬─────────┘
                       │ proxy_pass http://agent_backend → agent:8000
   network "internal" (bridge)  ─ chỉ nginx publish port ra host
                       ▼
              ┌──────────────────┐
              │  agent :8000     │  build từ Dockerfile multi-stage (target runtime)
              │  uvicorn x2      │  env: REDIS_URL=redis://redis:6379/0
              └───┬──────────┬───┘       QDRANT_URL=http://qdrant:6333
                  │          │
                  ▼          ▼
     ┌──────────────────┐  ┌──────────────────────┐
     │ redis:7-alpine   │  │ qdrant/qdrant:v1.9.0 │
     │ :6379            │  │ :6333                │
     │ vol: redis_data  │  │ vol: qdrant_data     │
     └──────────────────┘  └──────────────────────┘
```

**Các service được start:** 4 service: `redis`, `qdrant`, `agent`, `nginx`.

```text
SERVICE   IMAGE                   STATUS                    PORTS
agent     k5-day14-docker-agent   Up 16 seconds (healthy)   8000/tcp
nginx     nginx:alpine            Up Less than a second     0.0.0.0:8088->80/tcp
qdrant    qdrant/qdrant:v1.9.0    Up 27 seconds (healthy)   6333-6334/tcp
redis     redis:7-alpine          Up 27 seconds (healthy)   6379/tcp
```

**Thứ tự khởi động:**
- `agent` có `depends_on` với `condition: service_healthy`, nên Compose chờ `redis` (`redis-cli ping`) và `qdrant` (mở TCP 6333) báo healthy rồi mới start agent.
- `nginx` start sau `agent`.

**Các service giao tiếp thế nào:**
- Tất cả nằm trong network bridge `internal`. Docker có DNS nội bộ nên các service gọi nhau bằng tên service. Kiểm từ trong container agent: `redis → 172.21.0.3:6379 OK`, `qdrant → 172.21.0.2:6333 OK`, `nginx → 172.21.0.5:80 OK`.
- Chỉ `nginx` publish port ra host. Gọi thẳng `localhost:8000` từ host thì không kết nối được, vì agent chỉ nhận traffic qua Nginx.
- Nginx dùng `upstream agent_backend { server agent:8000; }`. Khi scale agent lên nhiều bản, tên `agent` trỏ tới nhiều IP và Nginx chia tải giữa chúng.

**Test qua Nginx:**

```text
$ curl -i http://localhost:8088/health
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block

{"status":"ok","uptime_seconds":15.2,"version":"2.0.0","timestamp":"2026-11-26T07:56:09.860839"}

$ curl -i -X POST http://localhost:8088/ask -H "Content-Type: application/json" -d '{"question":"Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx
{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}

# 40 request đồng thời → rate limit của Nginx (10r/s, burst 20) hoạt động
     25 200
     15 429
```

**Ghi chú:**
- **Cổng 80 trên Windows bị hệ thống giữ.** Cổng 80 thuộc process `System` (PID 4) và nằm trong dải cổng mà Windows loại trừ (`netsh interface ipv4 show excludedportrange`). Vì vậy `docker compose up` báo `bind: An attempt was made to access a socket in a way forbidden`. Mình không sửa `docker-compose.yml` mà thêm một file override `ports: !override ["8088:80"]` cho service `nginx`, rồi chạy `docker compose -p k5-day14-docker -f docker-compose.yml -f <override> up -d`. Ngoài cổng phía host, mọi thứ khác giữ nguyên.
- **Stack khai báo Redis và Qdrant nhưng `main.py` chưa dùng tới.** Biến `REDIS_URL` và `QDRANT_URL` được truyền vào nhưng code không đọc. Stack này chỉ minh hoạ cách ghép các service. State thật trong Redis được làm ở Part 5 và Part 6.
- **Log của app chưa phải JSON hợp lệ.** Đây là lỗi dấu `"` lồng nhau giống Part 1: `{"time":"...","level":"INFO","msg":"{"event": "request", "q_len": 2}"}`.

## Part 3: Cloud Deployment

Theo CHECKPOINTS CP6, lớp K5 deploy luôn agent Part 6 (`06-lab-complete/`), không deploy app mẫu ở `03-cloud-deployment/`.

### Exercise 3.1: Railway deployment

- **URL:** https://k5-l3b-day14-phamquangdung-2a202603896-deploymen-production.up.railway.app
- **Chi tiết, lệnh test, biến môi trường:** [DEPLOYMENT.md](DEPLOYMENT.md)
- **Screenshot:** [dashboard](screenshots/dashboard.png) · [running](screenshots/running.png) · [test](screenshots/test.png)

4 lệnh tự kiểm của CP6:

```text
GET  /health                  → 200   {"status":"ok","version":"1.0.0","environment":"production",...}
GET  /ready                   → 200   {"ready":true,"redis":"ok"}
POST /ask (không có key)      → 401
POST /ask (X-API-Key đúng)    → 200   {"question":"What is deployment?","answer":"Deployment là quá trình đưa code ..."}
```

Trên cloud, sau 10 request trong một phút, request tiếp theo trả `429` kèm `retry-after: 43`. State rate limit nằm trong Redis của Railway.

**Các lỗi gặp khi deploy và cách xử lý:**

Năm lỗi dưới đây được ghi theo nhóm nguyên nhân, không theo đúng thứ tự xảy ra.

1. **Bị chặn vì "unusual activity".** `railway up` bằng CLI trên tài khoản đầu tiên báo *"We limited this workspace after detecting unusual activity. Upgrade to a paid plan..."*. Mình chuyển sang tài khoản khác và deploy qua giao diện web từ GitHub.
2. **Build nhầm Dockerfile.** Chưa đặt Root Directory nên Railway build `Dockerfile` ở gốc repo. File đó là ví dụ Go, chạy `go build` và fail vì không có `go.mod`. Cách sửa: đặt **Root Directory = `06-lab-complete`**. Không sửa được bằng file `railway.toml` ở gốc repo, vì build context vẫn là gốc repo và các lệnh `COPY app/` sẽ không tìm thấy thư mục.
3. **`REDIS_URL` không hợp lệ.** Deploy Logs báo `ValueError: Redis URL must specify one of the following schemes`: tham chiếu biến bị thay thành chuỗi rỗng, nên cả hai worker chết lúc import, uvicorn khởi động lại chúng liên tục. Dashboard vẫn báo deploy thành công, vì process cha của uvicorn còn sống và health check lúc đó chưa được cấu hình. Đã sửa code (`app/redis_client.py`): `REDIS_URL` sai thì app vẫn chạy, `/ready` và `/ask` trả 503, và log ghi `config_error`. Sau đó dùng log `redis_unavailable` (đã che mật khẩu) để tìm ra lỗi kết nối.
4. **Redis nằm khác project với agent.** `/health` trả 200 nhưng `/ready` trả 503 ngay lập tức (0.4 giây). Lỗi là `redis.railway.internal` không phân giải được (`Name or service not known`): tên miền `*.railway.internal` và tham chiếu `${{Redis.REDIS_URL}}` chỉ dùng được giữa các service **cùng project và cùng environment**. Đây cũng là lý do tham chiếu ở bước 3 bị thay thành chuỗi rỗng. Sau khi chuyển Redis về cùng project với agent: `/ready` → 200.
5. **Gõ nhầm Healthcheck Path** thành `/heath`: mọi lần health check nhận 404, deploy fail. Sửa thành `/health`.

### Exercise 3.2: So sánh `railway.toml` và `render.yaml`

Mình viết cả hai file cho agent Part 6 ([railway.toml](06-lab-complete/railway.toml), [render.yaml](06-lab-complete/render.yaml)). Chỉ deploy thật bằng Railway; `render.yaml` mới qua kiểm tra tĩnh của script chấm, chưa deploy thử.

| Khía cạnh | `railway.toml` | `render.yaml` | Vì sao khác |
|---|---|---|---|
| Phạm vi | Cấu hình **một service**: cách build, cách deploy, health check, restart | **Blueprint của cả hạ tầng**: service web + Redis (Key Value) + biến môi trường + liên kết giữa chúng | Railway tạo service và database trên dashboard hoặc CLI, file chỉ mô tả từng service. Render dựng toàn bộ stack từ một file khi chạy Blueprint |
| Build | `builder = "DOCKERFILE"`, `dockerfilePath` | `runtime: docker`, `rootDir`, `dockerfilePath`, `dockerContext` | Render đọc file từ gốc repo nên phải tự khai báo thư mục con. Railway đặt Root Directory trên giao diện |
| Health check | `healthcheckPath = "/health"`, `healthcheckTimeout` | `healthCheckPath: /health` | Giống nhau về ý nghĩa: nền tảng chỉ chuyển traffic sang bản mới khi `/health` trả 200 |
| Restart | `restartPolicyType = "ON_FAILURE"`, `restartPolicyMaxRetries` | Không khai báo, Render tự restart | Railway cho chỉnh chính sách restart. Render quản lý việc này |
| Biến môi trường và secret | **Không** nằm trong file. Đặt trên tab Variables, tham chiếu bằng `${{Redis.REDIS_URL}}` | Nằm trong file nhưng **không ghi giá trị secret**: `AGENT_API_KEY` dùng `generateValue: true`, `REDIS_URL` dùng `fromService` (lấy `connectionString` của Key Value) | Cả hai đều tránh commit secret. Render để Blueprint tự sinh hoặc liên kết giá trị, Railway để toàn bộ ngoài code |
| Redis | Tạo riêng: `+ Create → Database → Redis` | Khai báo ngay trong file: `type: keyvalue` (Redis-compatible), `ipAllowList: []` (chỉ mạng nội bộ) | Như dòng "Phạm vi" |
| Cổng | Railway cấp `PORT` | Render cấp `PORT` (mặc định 10000) | Dockerfile đọc `${PORT:-8000}` nên chạy được trên cả hai, không sửa gì |

Tóm lại: `railway.toml` mô tả cách build và chạy một service, phần còn lại (database, biến, liên kết) làm trên dashboard. `render.yaml` là infrastructure-as-code cho cả hệ thống, dựng lại được toàn bộ chỉ từ repo, nên phải cẩn thận để không có secret nào nằm trong file.

### Exercise 3.3: Pipeline CI/CD trên Cloud Run

Đọc `03-cloud-deployment/production-cloud-run/cloudbuild.yaml` và `service.yaml` (không deploy):

1. **Trigger:** push lên nhánh `main` kích hoạt Cloud Build chạy 4 bước theo thứ tự `waitFor`: **test → build → push → deploy**.
2. **Test:** chạy trong container `python:3.11-slim`, cài `requirements.txt` cùng `pytest` rồi chạy `pytest tests/`. Test fail thì pipeline dừng, không build image.
3. **Build và push:** build image với hai tag `:$COMMIT_SHA` và `:latest`, dùng `--cache-from :latest` để tái dùng layer, rồi push lên Container Registry (`gcr.io/$PROJECT_ID/ai-agent`).
4. **Deploy:** `gcloud run deploy` dùng image gắn **`$COMMIT_SHA`**, không dùng `latest`. Nhờ vậy mỗi bản deploy gắn đúng với một commit và rollback được. Cấu hình kèm theo: region `asia-southeast1`, 1–10 instance (`min-instances=1` để tránh cold start), 512 MiB, 1 CPU, timeout 60 giây. Secret lấy từ **Secret Manager** (`--set-secrets`), không ghi trong code.
5. **`service.yaml`:** khai báo cùng service đó theo kiểu IaC (Knative). Mỗi instance nhận tối đa 80 request đồng thời. `livenessProbe` gọi `/health`, `startupProbe` gọi `/ready`. `AGENT_API_KEY` và `OPENAI_API_KEY` lấy từ `secretKeyRef`. Áp dụng bằng `gcloud run services replace`.

## Part 4: API Security

### Exercise 4.1: API Key authentication (`04-api-gateway/develop`)

Chạy: `AGENT_API_KEY=secret-key-123 python app.py`.

- **Key được kiểm ở đâu?** Trong hàm `verify_api_key` (dòng 39–54). Hàm này đọc header `X-API-Key` qua `APIKeyHeader(name="X-API-Key", auto_error=False)`, rồi được gắn vào `/ask` bằng `Depends(verify_api_key)` (dòng 70). Endpoint nào cần bảo vệ thì thêm dependency này. `/` và `/health` không gắn nên vẫn public.
- **Sai key thì sao?**
  - Thiếu header: **401** `Missing API key`.
  - Sai key: **403** `Invalid API key.`
  - Request bị chặn trước khi tới LLM nên không tốn tiền.
  - Lưu ý: hợp đồng của Part 6 yêu cầu cả hai trường hợp đều trả **401**. Ngoài ra, phép so sánh `!=` không chạy trong thời gian cố định. Nên dùng `hmac.compare_digest` để tránh bị đoán key bằng cách đo thời gian phản hồi.
- **Rotate key thế nào?**
  - Key đọc từ biến môi trường `AGENT_API_KEY`, nên rotate là đổi biến trên platform rồi restart hoặc redeploy, không phải sửa code.
  - Để không làm gián đoạn client, có thể cho app nhận *nhiều* key trong một khoảng chuyển tiếp (vd `AGENT_API_KEYS=old,new`). Client chuyển hết sang key mới rồi mới gỡ key cũ.
  - Default `"demo-key-change-in-production"` là điểm yếu: quên đặt biến thì app vẫn chạy với key ai cũng biết. Production nên báo lỗi ngay lúc khởi động nếu thiếu key.

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}

$ curl http://localhost:8000/health        # public, không cần key
{"status":"ok"}
```

### Exercise 4.2: JWT authentication (`04-api-gateway/production`)

**Luồng JWT (`auth.py`):**

1. Client gửi `POST /auth/token` với `{"username","password"}`. `authenticate_user` kiểm tra với `DEMO_USERS`, sai thì trả 401.
2. `create_token` tạo payload `{sub, role, iat, exp = now + 60 phút}` rồi ký bằng **HS256** với `JWT_SECRET`, trả về `access_token`.
3. Client gửi `Authorization: Bearer <token>` cho mỗi request.
4. `verify_token` (dependency của `/ask`) gọi `jwt.decode` để kiểm chữ ký và hạn dùng, rồi lấy `username` và `role` từ token. Server không cần tra DB hay session, nên đây là cách xác thực **stateless**. Chạy nhiều instance được, miễn là chúng dùng chung `JWT_SECRET`.

Mình chạy thử (token đã che):

```text
$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"wrong"}'
HTTP/1.1 401 Unauthorized
{"detail":"Invalid credentials"}

$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"demo123"}'
HTTP/1.1 200 OK
x-content-type-options: nosniff
x-frame-options: DENY
{"access_token":"eyJ***MASKED***","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer eyJ***..."}

# Payload giải mã base64 (không cần secret):
{'sub': 'student', 'role': 'user', 'iat': 1795679930, 'exp': 1795683530}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 401 Unauthorized
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer abc.def.ghi" ...
HTTP/1.1 403 Forbidden
{"detail":"Invalid token."}

# Token thật nhưng sửa payload thành "role":"admin", giữ nguyên chữ ký
HTTP/1.1 403 Forbidden
{"detail":"Invalid token."}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'
HTTP/1.1 200 OK
{"question":"Explain JWT","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.","usage":{"requests_remaining":9,"budget_remaining_usd":1.9e-05}}

# Câu hỏi rỗng → Pydantic chặn (min_length=1)
{"detail":[{"type":"string_too_short","loc":["body","question"],"msg":"String should have at least 1 character",...}]} [422]
```

**Nhận xét:**
- **JWT chỉ được *ký*, không được *mã hoá*.** Ai có token cũng đọc được payload, nên không được để secret trong payload. Đổi bất kỳ byte nào thì chữ ký sai và server trả 403. Vì vậy không thể tự nâng quyền lên admin bằng cách sửa token.
- **Token vẫn dùng được sau khi restart server.** Đây là hệ quả của việc JWT stateless. Mặt trái là không thu hồi được token trước khi hết hạn (trừ khi đổi `JWT_SECRET`, làm mọi token cùng mất hiệu lực, hoặc dùng blacklist trong Redis). Vì vậy `exp` nên ngắn.
- `JWT_SECRET` có default `"super-secret-change-in-production-please"`. Ai biết giá trị này thì tự ký được token admin, nên production bắt buộc phải đặt biến này.

### Exercise 4.3: Rate limiting

- **Thuật toán:** **sliding window log**.
  - Mỗi user có một `deque` lưu timestamp các request.
  - Mỗi lần gọi `check`, các timestamp cũ hơn 60 giây bị bỏ đi. Nếu số còn lại ≥ limit thì trả 429, ngược lại thêm timestamp mới vào.
  - Docstring gọi đây là "Sliding Window Counter", nhưng code lưu từng timestamp nên đúng ra là *log*.
  - Ưu điểm so với fixed window: không bị dồn gấp đôi số request ở ranh giới giữa hai phút. Nhược điểm: tốn bộ nhớ O(limit) cho mỗi user.
- **Limit:** user thường (`student`, role `user`) **10 request/60 giây**. Admin (`teacher`, role `admin`) **100 request/60 giây**.
- **Bypass cho admin:**
  - `app.py` dòng 141 chọn `rate_limiter_admin if role == "admin" else rate_limiter_user`. Admin không được miễn hoàn toàn mà dùng một tier có limit cao hơn.
  - `role` lấy từ JWT đã được ký nên client không tự sửa được.
  - Muốn miễn hẳn thì bỏ qua `limiter.check()` khi `role == "admin"`. Nhưng nên giữ một trần nào đó để token admin bị lộ không thể bị dùng để spam.

Vòng lặp 20 request bằng token `student`, chạy ngay sau khi restart server:

```text
Request  1: {"usage":{"requests_remaining":9,"budget_remaining_usd":2.1e-05}}  [200]
Request  2: {"usage":{"requests_remaining":8,"budget_remaining_usd":4.2e-05}}  [200]
...
Request  9: {"usage":{"requests_remaining":1,"budget_remaining_usd":0.000167}}  [200]
Request 10: {"usage":{"requests_remaining":0,"budget_remaining_usd":0.000188}}  [200]
Request 11: {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":56}}  [429]
...
Request 20: {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":54}}  [429]

$ curl -i ... (request thứ 21)
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795680027
retry-after: 53

# teacher (admin), 15 request liên tiếp
     15 200

# /admin/stats: token student → 403 {"detail":"Admin only"}; token teacher → 200
```

**Quan sát:**
- **State của limiter nằm trong RAM.** Lần chạy đầu, mình bị 429 từ request thứ 10 (vì đã gọi `/ask` 1 lần trước đó). Sau khi restart server, bộ đếm về 0 và lại được 10 request. Nếu chạy 3 instance sau load balancer thì mỗi user thực tế được tới 30 request/phút. Vì vậy Part 6 yêu cầu lưu state trong Redis.
- **Lỗi trong `app.py`:** trường `budget_remaining_usd` trả về `usage.total_cost_usd`, tức số tiền *đã tiêu*, nên giá trị tăng dần thay vì giảm. Con số đúng nằm ở `/me/usage`: `"budget_remaining_usd":0.999812`.
- **Lỗi trong middleware:** middleware xoá header `server`, nhưng response vẫn có `server: uvicorn`, vì uvicorn thêm header này *sau* middleware. Muốn ẩn thì chạy uvicorn với `--no-server-header`.

### Exercise 4.4: Cost guard

**Bản có sẵn (`cost_guard.py`) hoạt động thế nào:**
- Đếm token input và output của mỗi request, nhân với giá GPT-4o-mini ($0.15/1M input, $0.60/1M output) để ra chi phí.
- `check_budget` chạy **trước** khi gọi LLM. Vượt $1/ngày/user thì trả **402 Payment Required**. Tổng của mọi user vượt $10/ngày thì trả **503**. Dùng tới 80% thì ghi log cảnh báo.
- `record_usage` chạy **sau** khi gọi LLM để cộng chi phí thật.
- Mọi thứ nằm trong dict trong RAM, nên mất khi restart và không chia sẻ được giữa các instance.

**Cài bằng Redis, budget $10/tháng/user:**

```python
r = redis.Redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
MONTHLY_BUDGET_USD = float(os.getenv("MONTHLY_BUDGET_USD", "10"))

def _key(user_id: str) -> str:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    return f"budget:{user_id}:{month}"          # vd budget:alice:2026-11

def check_budget(user_id: str, estimated_cost: float) -> bool:
    key = _key(user_id)
    new_total = r.incrbyfloat(key, estimated_cost)   # nguyên tử: cộng trước
    if new_total == estimated_cost:                  # lần đầu trong tháng
        r.expire(key, 32 * 24 * 3600)
    if new_total > MONTHLY_BUDGET_USD:               # kiểm sau
        r.incrbyfloat(key, -estimated_cost)          # vượt → hoàn lại
        return False
    return True
```

Các quyết định thiết kế:

- **Tự reset đầu tháng:** tháng nằm trong tên key. Sang tháng mới, key mới bắt đầu từ 0, không cần cron job. TTL 32 ngày để Redis tự xoá key của tháng cũ.
- **Dùng múi giờ UTC:** mọi instance dùng cùng mốc tháng, không phụ thuộc giờ của từng server.
- **Cộng trước, kiểm sau, thay vì GET rồi INCR như lời giải mẫu trong `CODE_LAB.md`:**
  - Bản GET rồi INCR có race condition: nhiều request đọc cùng một giá trị `current`, cùng thấy còn budget và cùng được cho qua.
  - `INCRBYFLOAT` là thao tác nguyên tử trong Redis, nên tổng không bao giờ vượt trần.
  - Request bị từ chối thì được hoàn lại phần vừa cộng.
- **Lưu trong Redis:** mọi instance dùng chung một bộ đếm, và state vẫn còn sau khi restart hoặc deploy.

Mình test với Redis thật (`redis:7-alpine`), mã nguồn test nằm trong scratchpad, không commit:

```text
Budget: $10.0/tháng, key = budget:alice:2026-11

[1] Gọi tuần tự, mỗi lần $3:
  lần 1: allowed=True  spent=$3.00
  lần 2: allowed=True  spent=$6.00
  lần 3: allowed=True  spent=$9.00
  lần 4: allowed=False  spent=$9.00
  lần 5: allowed=False  spent=$9.00
  TTL của key: 2764800 giây (~32.0 ngày)

[2] 50 request đồng thời, mỗi request $0.5 (tối đa 20 request vừa $10):
  bản nguyên tử   : allowed=20, spent=$10.00
  bản GET-rồi-INCR: allowed=41, spent=$20.50
```

Chạy lại lần hai cho ra đúng hai con số này. Bản GET rồi INCR để lọt 41 request và tiêu $20.50, **gấp đôi ngân sách**. Bản nguyên tử chặn đúng ở $10.00.

Còn một cải tiến nên làm khi tích hợp vào app: gọi `check_budget` với chi phí *ước tính* trước khi gọi LLM, rồi điều chỉnh theo token thật sau khi có kết quả (cộng thêm phần chênh lệch). Như vậy request đắt hơn dự tính vẫn được tính đủ.

**Vì sao cần cả rate limit và cost guard:**
- Rate limit giới hạn **tốc độ**: số request trong mỗi phút. Nó chống spam, chống DoS và bảo vệ server.
- Cost guard giới hạn **tổng tiền**. Một user gọi đều 10 request/phút, mỗi request là một prompt dài, vẫn có thể đốt hết ngân sách tháng mà không lần nào bị 429.
- Ngược lại, một chuỗi request rẻ nhưng dồn dập thì không tốn nhiều tiền, nhưng vẫn làm sập server.

## Part 5: Scaling & Reliability

Python trên Windows không gửi được SIGTERM, nên mình chạy bản develop (`05-scaling-reliability/develop`) trong một container Linux:

```bash
docker run -d --name day14-p5dev -p 8000:8000 -v "<develop>:/src:ro" -w /src python:3.11-slim \
  sh -c "pip install -r requirements.txt && exec python app.py"
```

Dùng `exec` để `python app.py` chạy với PID 1 (`/proc/1/cmdline` → `python app.py`), nhờ đó nhận trực tiếp signal từ `docker kill --signal`.

### Exercise 5.1: Health checks

**Cách cài trong `develop/app.py`:**
- `GET /health` (dòng 104–144) là *liveness*: process còn chạy thì luôn trả 200, kèm uptime, version và mức dùng RAM (đọc bằng `psutil`). RAM trên 90% thì `status` là `"degraded"`, nhưng mã HTTP vẫn là 200.
- `GET /ready` (dòng 147–168) là *readiness*: trả **503** khi `_is_ready = False`, tức là lúc chưa khởi động xong hoặc đang tắt. Ngược lại thì trả 200, kèm số request đang xử lý.
- Ở bản `production/app.py`, `/ready` gọi thêm `_redis.ping()` và trả 503 khi Redis không trả lời (dòng 208–215).

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.4,"version":"1.0.0","environment":"development","timestamp":"2026-11-26T08:06:48.799022+00:00","checks":{"memory":{"status":"ok","used_percent":11.0}}}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true,"in_flight_requests":1}
```

`in_flight_requests` bằng 1 vì middleware đếm cả chính request `/ready` đang chạy.

**`/health` khác `/ready` ở đâu:**

|  | `/health` (liveness) | `/ready` (readiness) |
|---|---|---|
| Câu hỏi | Process còn sống không, có bị treo không? | Có nên gửi traffic vào instance này không? |
| Khi fail | Platform **restart** container | Load balancer **ngừng gửi request** vào instance đó, không restart |
| Có kiểm dependency (Redis/DB) không? | **Không** | **Có** |

Nếu `/health` cũng kiểm Redis thì khi Redis chết, mọi instance cùng fail liveness và bị restart liên tục. Restart không làm Redis sống lại, chỉ làm hệ thống sập nặng hơn. Đúng ra, `/ready` trả 503 để load balancer tạm bỏ các instance đó ra, chờ Redis hồi phục. Part 6 yêu cầu đúng hành vi này.

**Lỗi phát hiện khi đo:** `/ask` khai báo `async def` nhưng gọi `ask()`, mà `ask()` dùng `time.sleep(3)` (chặn luồng). Hàm này giữ luôn event loop, nên gọi `/health` trong lúc `/ask` đang chạy phải chờ:

```text
/health trong lúc /ask đang chạy: 200 sau 2.450084s
```

Platform đặt timeout cho health check, thường 5–10 giây. Chỉ cần vài request chậm cùng lúc là health check timeout, và container bị restart oan. Cách sửa: khai báo `def` thường để FastAPI chạy hàm trong threadpool, hoặc `await run_in_threadpool(ask, question)`.

### Exercise 5.2: Graceful shutdown

**Cách cài:**
- uvicorn bắt SIGTERM, ngừng nhận kết nối mới, chờ request đang chạy xong (tối đa `timeout_graceful_shutdown=30`), rồi chạy phần shutdown của `lifespan`.
- Phần shutdown (dòng 54–66) đặt `_is_ready=False`, để `/ready` trả 503 và load balancer ngừng gửi request. Sau đó nó chờ `_in_flight_requests` (do middleware `track_requests` đếm) về 0, rồi mới thoát.

**Test:** gửi `/ask` (mất khoảng 3 giây), tới giây thứ 1 thì gửi SIGTERM:

```text
t=+1s: gửi SIGTERM tới PID 1                 (docker kill --signal SIGTERM day14-p5dev)
t=+1.3s: request mới tới /ready:  [000]      → không kết nối được
{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}
→ request: HTTP 200, 3.019823s               → request đang chạy KHÔNG bị cắt
→ container exit code: 0
```

Log của container:

```text
08:07:07.245877165Z INFO:     Shutting down
08:07:07.245959438Z INFO:     172.17.0.1:34308 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
08:07:07.346641121Z INFO:     Waiting for application shutdown.
08:07:07.346686574Z 2026-11-26 08:07:07,346 INFO 🔄 Graceful shutdown initiated...
08:07:07.346693802Z 2026-11-26 08:07:07,346 INFO ✅ Shutdown complete
08:07:07.346698395Z INFO:     Application shutdown complete.
08:07:07.346807435Z INFO:     Finished server process [1]
08:07:07.346857296Z 2026-11-26 08:07:07,346 INFO Received signal 15 — uvicorn will handle graceful shutdown
```

**Nhận xét:**
- Request đang chạy trả **200** và process thoát với **exit code 0**, đúng yêu cầu graceful shutdown.
- Dòng `Shutting down` xuất hiện cùng lúc request trả về (08:07:07.245), không phải ở giây thứ 1. Lý do: `time.sleep` đang giữ event loop như ở Exercise 5.1, nên uvicorn chỉ xử lý được SIGTERM khi request xong. Kết quả vẫn đúng, nhưng chỉ là may mắn. Nếu có nhiều request chậm, `/ready` không kịp trả 503.
- Log `Received signal 15` của `handle_sigterm` hiện ra **cuối cùng**. Lý do: từ bản 0.29, uvicorn tạm thay các signal handler khác bằng handler của nó, chạy xong quy trình shutdown, rồi mới gọi lại handler cũ của app. Vì vậy logic dọn dẹp nên đặt trong `lifespan`, không đặt trong signal handler.
- Muốn graceful shutdown chạy được trong Docker, uvicorn phải chạy với PID 1. Nghĩa là dùng `CMD [...]` dạng exec form, hoặc `exec` như ở trên. Nếu chạy qua `sh -c` mà không có `exec`, shell nhận SIGTERM nhưng không chuyển tiếp cho uvicorn. Sau 10 giây, Docker gửi SIGKILL và request bị cắt ngang.

### Exercise 5.3: Stateless design

**Đọc `production/app.py`:**
- Session và conversation history được lưu trong Redis bằng `save_session` (`SETEX session:<id>` với TTL 3600 giây) và `load_session` (`GET`). Instance không giữ gì trong RAM.
- Mỗi instance có một `INSTANCE_ID` riêng và trả trong trường `served_by`, để thấy request nào do instance nào xử lý.

**Vì sao state trong RAM hỏng khi chạy nhiều instance:**
- Mỗi instance là một process riêng, với bộ nhớ riêng.
- Load balancer chia request theo vòng tròn, nên turn 1 vào instance A, turn 2 vào instance B. B không có history của A, nên câu trả lời mất ngữ cảnh.
- Instance bị restart, crash hoặc scale down thì state trong RAM của nó mất theo.
- Đưa state ra Redis thì instance nào cũng như nhau. Thêm, bớt, hay tắt một instance đều không ảnh hưởng tới người dùng.

**Vấn đề còn lại trong code mẫu:**
- **Fallback âm thầm về RAM:** nếu lúc khởi động không kết nối được Redis, app tự chuyển sang `_memory_store` (dòng 42–45) và chạy tiếp, hệ thống trở lại stateful mà không báo lỗi. Hơn nữa, kết nối chỉ được thử *một lần* lúc import. Container agent khởi động trước Redis (ví dụ deploy trên cloud không có `depends_on`) sẽ dùng RAM mãi mãi. Production nên để `/ready` trả 503 và thử kết nối lại.
- **Race condition:** `append_to_history` đọc cả session, sửa, rồi ghi đè. Hai request cùng session chạy song song thì message của request ghi trước bị mất. Nên dùng `RPUSH` + `LTRIM` trên một Redis list, như gợi ý `r.lrange(f"history:{user_id}", ...)` trong `CODE_LAB.md`, vì các lệnh này là nguyên tử.
- **`turn` bị lệch 1:** `turn` được tính *sau* khi đã thêm câu hỏi hiện tại vào history, rồi lại `+1`. Câu hỏi thứ 2 trả `"turn": 3` (thấy trong output ở 5.5).

### Exercise 5.4: Load balancing

Chạy trong `05-scaling-reliability/production/`, có tạo `.env.local` rỗng trước:

```bash
docker compose -p k5-day14-scaling up -d --build --scale agent=3
```

```text
NAME                       STATUS                    PORTS
k5-day14-scaling-agent-1   Up 9 seconds (healthy)    8000/tcp
k5-day14-scaling-agent-2   Up 8 seconds (healthy)    8000/tcp
k5-day14-scaling-agent-3   Up 9 seconds (healthy)    8000/tcp
k5-day14-scaling-nginx-1   Up 8 seconds              0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   Up 20 seconds (healthy)   6379/tcp
```

Gửi 10 request `POST /chat` qua Nginx (`localhost:8080`):

```text
Request  1 -> instance-f8e892
Request  2 -> instance-777d9b
Request  3 -> instance-59ca7b
Request  4 -> instance-f8e892
Request  5 -> instance-777d9b
Request  6 -> instance-59ca7b
Request  7 -> instance-f8e892
...
phân bố: 4 instance-f8e892 / 3 instance-777d9b / 3 instance-59ca7b
```

Nginx chia request theo vòng tròn, đều cho 3 instance. Đếm `POST /chat` trong `docker compose -p k5-day14-scaling logs agent` sau toàn bộ bài test: agent-1 = 12, agent-2 = 8, agent-3 = 13. agent-2 ít hơn vì bị tắt hai lần trong thí nghiệm ở 5.5.

**Cách hoạt động:**
- Docker Compose tạo một tên DNS `agent` trỏ tới IP của cả 3 container. Nginx phân giải tên này một lần lúc khởi động, thành 3 server trong `upstream agent_cluster`.
- `proxy_next_upstream error timeout http_503` cùng với `proxy_next_upstream_tries 3`: khi một instance lỗi, timeout hoặc trả 503, Nginx tự gửi lại request sang instance khác.

### Exercise 5.5: Test stateless

```text
$ python test_stateless.py
Session ID: 612bdfa8-06b1-4366-9b35-b03613cdd7a6

Request 1: [instance-f8e892]   Q: What is Docker?
Request 2: [instance-777d9b]   Q: Why do we need containers?
Request 3: [instance-59ca7b]   Q: What is Kubernetes?
Request 4: [instance-f8e892]   Q: How does load balancing work?
Request 5: [instance-777d9b]   Q: What is Redis used for?
------------------------------------------------------------
Total requests: 5
Instances used: {'instance-f8e892', 'instance-777d9b', 'instance-59ca7b'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app để chạy ở mọi nơi. Build once...
  ...
✅ Session history preserved across all instances via Redis!
```

**Giải thích:** 5 câu hỏi cùng một `session_id` được 3 instance khác nhau xử lý xen kẽ. Dù vậy, history đọc lại có đủ 10 message (5 câu hỏi và 5 câu trả lời), đúng thứ tự. Lý do: mỗi instance đọc và ghi session trong cùng một Redis, không instance nào giữ state riêng.

**Thí nghiệm thêm: tắt một instance giữa cuộc hội thoại.** `test_stateless.py` không tự tắt instance nào, nên mình tự làm:
- agent-2 được khởi động lại trước thí nghiệm nên có ID mới là `instance-8aaebd`. Instance ID sinh ngẫu nhiên mỗi lần khởi động.
- Mình dùng `docker kill` (SIGKILL), mô phỏng crash.

```text
turn  3 -> instance-777d9b  HTTP 200  0.133010s
turn  4 -> instance-59ca7b  HTTP 200  0.155220s
turn  5 -> instance-8aaebd  HTTP 200  0.114775s
>>> 15:11:48 docker kill k5-day14-scaling-agent-2 (instance-8aaebd, SIGKILL)
turn  6 -> instance-777d9b  HTTP 200  0.135241s
turn  7 -> instance-59ca7b  HTTP 200  0.117431s
turn  8 -> instance-777d9b  HTTP 200  14.522819s     ← Nginx thử instance đã chết trước
turn  9 -> instance-59ca7b  HTTP 200  0.143203s
turn 10 -> instance-777d9b  HTTP 200  0.125674s
turn 11 -> instance-59ca7b  HTTP 200  0.126346s
history count = 20

nginx | [error] connect() failed (113: Host is unreachable) while connecting to upstream,
        request: "POST /chat HTTP/1.1", upstream: "http://172.21.0.5:8000/chat"
```

(Cột `turn` lệch 1 so với số câu đã gửi, do lỗi đã nêu ở 5.3.)

**Kết quả:**
- Sau khi tắt một instance, cả 6 request còn lại đều trả 200. Cuộc hội thoại vẫn liên tục, history đủ 20 message cho 10 câu hỏi. Dữ liệu không mất vì nằm trong Redis, không nằm trong instance đã chết.
- Có **một request mất 14.5 giây**. Nginx vẫn giữ IP của container đã chết trong danh sách upstream. Lần đầu chọn trúng IP đó, Nginx phải chờ kết nối thất bại rồi mới gửi lại sang instance khác nhờ `proxy_next_upstream`. Sau đó, Nginx đánh dấu server này hỏng (mặc định `max_fails=1`, `fail_timeout=10s`), nên các request tiếp theo đi thẳng tới 2 instance còn sống.
- Muốn giảm độ trễ này, có thể đặt `proxy_connect_timeout 2s`, hoặc dùng một health check chủ động để loại instance chết ra khỏi upstream sớm hơn. Trên cloud, platform làm việc này bằng `/ready`.
- `restart: unless-stopped` không tự bật lại container, vì Docker coi `docker kill` là thao tác dừng có chủ ý của người dùng. Nếu process tự crash, Docker sẽ restart container.

Xong thì dọn stack: `docker compose -p k5-day14-scaling down -v`.

## Part 6: Final Project

`python check_production_ready.py` chạy đầy đủ (có Docker) trong `06-lab-complete/` trên WSL2 (`os.name == "posix"`), Docker 29.3.1:

```text
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

Một số dòng chi tiết trong output: `image < 500 MB — 63 MB`, `429 sau 5 request thành công (RATE_LIMIT_PER_MINUTE=5)`, `restart agent vẫn bị 429`, `402 ở request thứ 1`, `mọi dòng log của app là JSON hợp lệ — 17 dòng JSON`, `docker stop tắt êm — exit code 0, 0.9s`.

**Các quyết định thiết kế chính:**

- **`/health` và `/ready`:**
  - `/health` không kiểm Redis. Nếu Redis chết, restart agent cũng không giúp được gì.
  - `/ready` gọi `PING` tới Redis với timeout 1 giây, không trả lời thì trả 503.
  - Redis client được tạo lúc import nhưng chưa mở kết nối, nên container vẫn khởi động được khi chưa có Redis.
- **`/ask` khai báo `def` thay vì `async def`:** FastAPI chạy hàm trong threadpool. Lời gọi LLM chặn luồng không làm đứng event loop, nên `/health` và SIGTERM vẫn được xử lý ngay. Đây là lỗi đã gặp ở Part 5.
- **Rate limit:**
  - Thuật toán sliding window, dùng Redis sorted set cho mỗi API key. Key trong Redis là hash SHA-256 của API key, không lưu key gốc.
  - Ghi request (`ZADD`) rồi đếm (`ZCARD`) trong cùng một transaction `MULTI/EXEC`, nên nhiều worker không cùng lọt qua được.
  - Request bị từ chối được xoá khỏi cửa sổ. Response 429 có header `Retry-After`.
- **Cost guard:**
  - Tổng chi phí của agent được lưu trong `cost:day:YYYY-MM-DD` và `cost:month:YYYY-MM` (giờ UTC), cộng bằng `INCRBYFLOAT` (thao tác nguyên tử, xem Exercise 4.4).
  - Kiểm `DAILY_BUDGET_USD` và `MONTHLY_BUDGET_USD` *trước* khi gọi LLM, ghi chi phí thật *sau* khi gọi.
  - Ngày hoặc tháng nằm trong tên key nên tự reset, TTL dọn key cũ.
- **Mất Redis giữa chừng:** `/ask` trả **503**, không lộ stack trace, và không cho gọi LLM khi không đếm được rate limit và chi phí (fail closed). Mọi lỗi khác trả 500 với thông báo chung.
- **Log:**
  - `JsonFormatter` tự viết, dùng `json.dumps`, mỗi dòng là một JSON object.
  - Middleware ghi `method`, `path`, `status`, `ms`, `request_id` cho mỗi request. Không ghi API key hay nội dung câu hỏi, chỉ ghi độ dài câu hỏi và chi phí.
- **Graceful shutdown:**
  - Không tự đăng ký signal handler. uvicorn bắt SIGTERM, chờ request đang chạy xong, rồi chạy phần shutdown của `lifespan` (ghi log, đóng connection pool Redis).
  - Trong Docker, lệnh `CMD ["sh","-c","exec uvicorn ... --port ${PORT:-8000} --timeout-graceful-shutdown 8"]`: `exec` làm uvicorn thành PID 1. Timeout 8 giây nhỏ hơn 10 giây, là thời gian `docker stop` chờ trước khi gửi SIGKILL.
- **Dockerfile:** stage builder cài dependencies vào `/opt/venv`. Stage runtime dùng `python:3.11-slim`, chỉ copy venv, `app/`, `utils/`, và chạy bằng user `app`. Kích thước: 63 MB (content), 286 MB (disk usage).
- **Config:** mọi giá trị đọc từ biến môi trường. `AGENT_API_KEY` không có giá trị mặc định. Thiếu key thì `/ask` trả 401 cho mọi request, và app không chịu khởi động khi `ENVIRONMENT=production`. Compose chỉ đọc secret từ `.env` (nằm trong `.gitignore`).

**Tự test thêm với `docker compose -p k5-day14-final up`** (agent + Redis, `RATE_LIMIT_PER_MINUTE=3`):

```text
ask 1: 200
ask 2: 200
ask 3: 200
ask 4: 429
--- restart agent, gọi lại ngay
ask sau restart: HTTP/1.1 429 Too Many Requests
retry-after: 56
--- redis keys
cost:day:2026-11-26
cost:month:2026-11
ratelimit:6c7794735e1ae20ad112c476
--- dừng redis → /health & /ready
/health: {"status":"ok","version":"1.0.0","environment":"development","uptime_seconds":2.9} [200]
/ready: {"ready":false,"redis":"unavailable"} [503]
```

Khi tắt hẳn container Redis, `/ready` mất khoảng 4 giây mới trả 503 (log `"ms": 4003.7`). Có thể do bước tra DNS tên `redis` trong mạng Docker chậm, chưa xác minh. Kết quả vẫn đúng, nhưng nếu platform đặt timeout health check thấp thì nên lưu kết quả ping trong vài giây.

## AI log

| Part | Hỏi AI gì | Nhận được gì | Tự sửa / tự kiểm tra |
|------|-----------|--------------|----------------------|
| Part 1 | Dùng Claude Code giải thích bài lab, tìm anti-pattern trong `develop/app.py` và so sánh với bản production | Danh sách anti-pattern kèm số dòng, bảng so sánh, gợi ý nguyên nhân log INFO bị mất | Tự chạy cả hai bản và gọi bằng `curl` để lấy output ở trên. Chạy lại bản production với `OPENAI_API_KEY=dummy` để xác nhận nguyên nhân mất log |
| Part 2 | Dùng Claude Code trả lời câu hỏi về Dockerfile, giải thích multi-stage, vẽ sơ đồ stack Compose, tìm lý do không mở được cổng 80 | Câu trả lời Exercise 2.1, cách dùng file override để đổi cổng Nginx sang 8088 mà không sửa file của đề | Tự build hai image và đo dung lượng bằng `docker images`. Kiểm layer bằng `docker history`, user bằng `whoami`. Chạy stack và gọi qua Nginx bằng `curl`. Kiểm DNS nội bộ từ trong container agent |
| Part 4 | Dùng Claude Code đọc `auth.py`, `rate_limiter.py`, `cost_guard.py`, viết cost guard lưu trong Redis | Giải thích luồng JWT, thuật toán sliding window, bản `check_budget` dùng `INCRBYFLOAT` nguyên tử | Tự gọi bằng `curl` để thấy 401/403/200/429. Thử sửa payload JWT để kiểm chữ ký. Chạy cost guard với Redis thật, gửi 50 request đồng thời và so với lời giải mẫu (41 request lọt, $20.50 so với 20 request, $10.00) |
| Part 5 | Dùng Claude Code giải thích liveness và readiness, graceful shutdown, stateless; dựng kịch bản gửi SIGTERM và tắt instance | Cách chạy bản develop trong container Linux để gửi SIGTERM; giải thích độ trễ 14.5 giây khi Nginx thử instance đã chết | Tự chạy và đo: `/health` bị chặn 2.45 giây, request trả 200 sau SIGTERM với exit code 0. Chạy `test_stateless.py`. Tự `docker kill` một instance và đếm lại history (20 message) |
| Part 6 | Dùng Claude Code đọc `check_production_ready.py` để hiểu từng tiêu chí, rồi viết code cho các TODO trong `06-lab-complete/` (`app/*.py`, Dockerfile, compose, `.dockerignore`, `.env.example`, `railway.toml`, `render.yaml`) | Code hoàn chỉnh cho 12 tiêu chí và giải thích các quyết định thiết kế ở trên | Chạy `--static-only`, rồi `--skip-docker`, rồi bản đầy đủ trong WSL: PASS 12/12. Tự test stack compose: 429 vẫn còn sau restart, tắt Redis thì `/ready` trả 503. Chạy container không có Redis để xem `/health` và `/ask` phản hồi thế nào. Không sửa `check_production_ready.py` |
| Part 3 | Dùng Claude Code đọc lỗi deploy Railway (build nhầm Dockerfile Go, `REDIS_URL` rỗng làm worker liên tục chết và khởi động lại, gõ nhầm healthcheck path), sửa `redis_client.py` để báo lỗi cấu hình thay vì crash, so sánh `railway.toml` với `render.yaml`, tóm tắt pipeline Cloud Run | Hướng dẫn đặt Root Directory và tham chiếu biến, bản sửa code, nội dung `DEPLOYMENT.md` | Tự deploy trên giao diện Railway và tự sửa cấu hình. Chạy 4 lệnh tự kiểm trên URL public (200/200/401/200), thử rate limit trên cloud (429 sau 10 request), chạy `check_production_ready.py --public-url`. Sau mỗi lần sửa code đều chạy lại script chấm: PASS 12/12 |
| Part 7 (CP7) | Dùng Claude Code chạy load test và eval, tìm nguyên nhân p95 tăng vọt ở c=100, chọn từ khoá cho `KNOWLEDGE_BASE` để không gây regression, viết job CI và điền `BENCHMARK_REPORT.md` mục 1–3 | Bản thêm metric `agent_question_length_chars`, 2 tài liệu KB, `.github/workflows/eval.yml`, phần phân tích | Mọi số trong báo cáo được trích bằng script từ file JSON, không chép tay. Kiểm tra giả thuyết "log qua pipe gây chậm" bằng một bản v2 ghi log vào `/dev/null`: giả thuyết sai. Kiểm từ khoá KB trên cả 23 câu hỏi trước khi thêm. Chạy đúng lệnh verification của Lab Coach: 23/23, exit code 0. Chạy thử các lệnh của job CI với bản tốt (exit 0) và bản hỏng (exit 1) |
| Part 7 (CP8) | Dùng Claude Code dựng stack observability, tìm lỗi container agent crash (`PYTHONUSERBASE`) và image Grafana bị cắt cụt, chạy benchmark scaling, làm 5 thí nghiệm alert, chụp Grafana/Jaeger/Prometheus bằng trình duyệt Playwright, điền `BENCHMARK_REPORT.md` mục 4–6 | Bản sửa 1 dòng `production/Dockerfile`, file override Grafana 13.2.3 ngoài repo, phân tích kết quả | Kiểm chứng lỗi Dockerfile bằng `site.USER_SITE` trong container. Phát hiện lệnh `compose run` của đề âm thầm đưa số instance về 2 (xem lịch sử `up` trong Prometheus), chạy lại với `--no-deps` và theo dõi số instance suốt lần đo. Đối chiếu 499 trace với log để loại 29 trace bị đội thời gian do đồng hồ nhảy. Mọi số trong báo cáo lấy từ CSV, PromQL và log. `promtool test rules` báo SUCCESS |
