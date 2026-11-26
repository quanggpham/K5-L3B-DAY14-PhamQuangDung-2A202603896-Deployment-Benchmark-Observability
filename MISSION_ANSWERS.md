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

## AI log

| Part | Hỏi AI gì | Nhận được gì | Tự sửa / tự kiểm tra |
|------|-----------|--------------|----------------------|
| Part 1 | Dùng Claude Code giải thích bài lab, tìm anti-pattern trong `develop/app.py` và so sánh với bản production | Danh sách anti-pattern kèm số dòng, bảng so sánh, gợi ý nguyên nhân log INFO bị mất | Tự chạy cả hai bản và gọi bằng `curl` để lấy output ở trên. Chạy lại bản production với `OPENAI_API_KEY=dummy` để xác nhận nguyên nhân mất log |
| Part 2 | Dùng Claude Code trả lời câu hỏi về Dockerfile, giải thích multi-stage, vẽ sơ đồ stack Compose, tìm lý do không mở được cổng 80 | Câu trả lời Exercise 2.1, cách dùng file override để đổi cổng Nginx sang 8088 mà không sửa file của đề | Tự build hai image và đo dung lượng bằng `docker images`. Kiểm layer bằng `docker history`, user bằng `whoami`. Chạy stack và gọi qua Nginx bằng `curl`. Kiểm DNS nội bộ từ trong container agent |
