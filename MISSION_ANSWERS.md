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

## AI log

| Part | Hỏi AI gì | Nhận được gì | Tự sửa / tự kiểm tra |
|------|-----------|--------------|----------------------|
| Part 1 | Dùng Claude Code giải thích bài lab, tìm anti-pattern trong `develop/app.py` và so sánh với bản production | Danh sách anti-pattern kèm số dòng, bảng so sánh, gợi ý nguyên nhân log INFO bị mất | Tự chạy cả hai bản và gọi bằng `curl` để lấy output ở trên. Chạy lại bản production với `OPENAI_API_KEY=dummy` để xác nhận nguyên nhân mất log |
