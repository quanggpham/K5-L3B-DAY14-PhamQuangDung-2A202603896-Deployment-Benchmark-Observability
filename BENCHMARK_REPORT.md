# Benchmark & Observability Report — Day 14

> Mọi con số trong báo cáo lấy từ report JSON trong [reports/](reports/) (bản gốc ở `06-lab-complete/benchmark-observability/develop/reports/`), CSV trong [benchmark-results/](benchmark-results/) và screenshot trong [screenshots/](screenshots/).

**Họ tên:** Phạm Quang Dũng — 2A202603896
**Ngày chạy:** 2026-11-26
**Môi trường:** Windows 11 + WSL2 Ubuntu 24.04 (16 vCPU, 7 GB RAM cấp cho WSL), Python 3.12.3. Agent (`develop/app.py`, 1 process uvicorn) và `load_test.py` chạy chung một máy, giao tiếp qua `localhost`. Docker Desktop 29.3.1 (WSL2 backend, ổ đĩa ảo đặt trên ổ F:) cho phần production. Stack production giới hạn mỗi agent `cpus: 0.5`, `memory: 256M` theo `docker-compose.yml` của đề.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | Request bình thường của v2 mất khoảng 190–230 ms (p95 = 219.5 ms khi không có request chậm, `load_v2_slow_0.json`). 500 ms cho dư hơn 2 lần, để chịu được jitter và tải tăng. Một request "chậm bất thường" mất khoảng 1.7 s, nên khi nhóm này vượt 5% số request thì p95 vượt ngưỡng ngay và SLO báo động |
| Error rate `/ask` | < 2 % | Mock LLM có khoảng 1% lỗi upstream (`MOCK_ERROR_RATE=0.01`). Đo thực tế được 0–2% (bảng 2.1). Ngưỡng 2% chấp nhận mức lỗi nền của provider nhưng bắt được khi lỗi tăng gấp đôi. Error budget là 2% số request |
| Accuracy trên golden dataset | ≥ 85 %, không có câu regression | Cùng ngưỡng với job CI (`.github/workflows/eval.yml`). v2 đạt 91.3% trước khi sửa và 100% sau khi sửa. Ngoài ngưỡng tổng, gate còn chặn mọi câu từ đúng thành sai so với baseline, vì accuracy tổng có thể giữ nguyên trong khi một câu quan trọng hỏng |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

Lệnh: `python load_test.py --url http://localhost:8002 --requests 300 --concurrency <c> --out reports/load_v2_c<c>.json` (agent v2, cấu hình mặc định).

### 2.1 Concurrency vs latency (agent v2)

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.3 | 194.2 | 220.3 | 1697.3 | 0.00% | ✅ PASS |
| 10 | 35.99 | 194.1 | 223.5 | 1704.1 | 1.33% | ✅ PASS |
| 50 | 107.01 | 197.7 | 293.9 | 1684.3 | 2.00% | ✅ PASS |
| 100 | 66.07 | 295.7 | 2605.1 | 2792.4 | 1.00% | ❌ FAIL (p95) |

Nguồn: `reports/load_v2_c1.json`, `load_v2_c10.json`, `load_v2_c50.json`, `load_v2_c100.json`. Error rate đều do 503 của mock LLM (`status_codes`), không có lỗi kết nối.

**Nhận xét:** RPS chỉ tăng gần tuyến tính ở mức concurrency thấp, sau đó chững lại rồi **giảm**.

- **Mức thấp, đúng định luật Little:** RPS ≈ số request song song ÷ latency. c=1: 1 ÷ 0.2325 s = 4.3 req/s, khớp số đo. c=10: tăng 8.4 lần (lý thuyết 10 lần), do latency trung bình nhích lên một chút (243.6 ms) và overhead của client.
- **c=50 bắt đầu bão hoà:** lý thuyết là 50 ÷ 0.256 s ≈ 195 req/s, nhưng chỉ đo được 107 req/s, và p95 tăng từ 223 lên 294 ms. Agent chỉ có **1 process uvicorn, một event loop**. Mỗi request tốn CPU cho parse JSON, Pydantic, ghi metric và log. Khi số request đến nhanh hơn tốc độ event loop xử lý, request phải xếp hàng.
- **c=100 vượt khỏi vùng bão hoà:** RPS giảm xuống 66, p95 lên 2.6 s, SLO fail. Phân bố latency tách làm hai nhóm: p50 = 296 ms nhưng p90 = 2361 ms, tức khoảng 10% request phải chờ thêm khoảng 2 s.
  - Mình đã kiểm tra giả thuyết "log ghi qua pipe chặn event loop" bằng cách chạy thêm một bản v2 với log đưa vào `/dev/null`. Kết quả tương tự: p95 1.9–2.8 s, và RPS dao động 62–120 giữa các lần chạy. Vậy không phải do ghi log.
  - Giả thuyết phù hợp với số liệu nhất: 100 kết nối TCP mới mở cùng lúc dồn vào một event loop đang bận xử lý request, nên một phần kết nối phải đợi lâu. Mình chưa chứng minh được điểm này.
  - Ở mức này, client cũng chạy trên cùng máy nên cạnh tranh CPU với server.
- **Kết luận:** muốn tăng throughput phải chạy **nhiều worker hoặc nhiều instance** (scale ngang, xem mục 5), không phải mở thêm kết nối. Mỗi lần chạy chỉ 300 request: ở c=50 chỉ mất 2.8 s, nên p99 tương ứng với khoảng 3 request và dao động mạnh giữa các lần chạy.
- **p99 khoảng 1.7 s ở mọi mức concurrency** là do mock cố tình cho khoảng 3% request chậm thêm 1500 ms (mục 2.2), không phải do tải.

### 2.2 Tail latency

Mỗi mức: bật lại v2 với `MOCK_SLOW_RATE=<giá trị>`, chạy `--requests 300 --concurrency 20`.

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 192.3 | 219.5 | 234.6 |
| 0.03 (mặc định) | 242.4 | 230.7 | 1682.3 |
| 0.1 | 279.2 | 1661.0 | 1711.6 |

Nguồn: `reports/load_v2_slow_0.json`, `load_v2_slow_0.03.json`, `load_v2_slow_0.1.json`.

**Nhận xét:**

- **Chỉ số nhạy nhất là percentile nằm trong vùng của nhóm request chậm**, còn avg gần như không đổi:
  - Từ 0 lên 0.03: avg chỉ tăng **50 ms (+26%)**, p95 gần như không đổi (+11 ms), nhưng **p99 tăng 7.2 lần** (234.6 lên 1682.3 ms). Lý do: 3% request chậm nằm ngoài top 5% (nên p95 không thấy) nhưng nằm trong top 1% (nên p99 thấy).
  - Từ 0 lên 0.1: nhóm chậm vượt 5% số request, nên **p95 cũng tăng 7.6 lần** (219.5 lên 1661.0 ms), trong khi avg mới tăng **87 ms (+45%)**.
- **Vì sao không đặt SLO theo average:** average gộp mọi request thành một con số, nên một nhóm nhỏ người dùng bị chậm rất nặng (gần 1.7 s, chậm gấp 8 lần) bị nhóm đông request nhanh che đi. Ở mức 0.1, avg = 279 ms vẫn trông "đạt" ngưỡng 500 ms, trong khi cứ 10 người thì 1 người chờ gần 2 giây. Percentile trả lời đúng câu "x% người dùng chờ tối đa bao lâu", nên SLO đặt theo p95 hoặc p99. Ở đây SLO p95 < 500 ms đã bắt được trường hợp 0.1 (❌ FAIL).
- **Tác dụng phụ trên throughput:** RPS giảm từ 100.3 (0) xuống 63.1 (0.03) và 55.4 (0.1), vì mỗi request chậm giữ một slot concurrency khoảng 1.7 s.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3% (1/23) | 91.3% (21/23) | 100.0% (23/23) |
| Avg tokens / câu | 57.6 | 108.8 | 114.7 |
| Tổng cost (USD) | 0.000411 | 0.000758 | 0.000799 |
| Cost / câu đúng (USD) | 0.00041115 | 0.00003607 | 0.00003475 |
| Quality gate | FAIL (accuracy < 80%) | PASS (≥ 80%, 0 regression so với v1) | PASS (≥ 100%, 0 regression so với v2) |

Nguồn: `reports/eval_v1.json`, `eval_v2.json` (`--baseline reports/eval_v1.json`), `eval_v2_fixed.json` (`--min-accuracy 1.0 --baseline reports/eval_v2.json`).

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `scaling-03` "Kubernetes HPA scale dựa trên những chỉ số nào?" | `cpu`, `memory`, `custom metrics` | Thêm tài liệu với từ khoá `["hpa", "horizontal pod autoscaler", "autoscal"]` vào `KNOWLEDGE_BASE`: HPA scale số pod theo CPU, memory hoặc custom metrics (RPS, độ dài hàng đợi) |
| `security-02` "JWT khác gì API key trong xác thực?" | `token`, `hết hạn`, `chữ ký` | Thêm tài liệu với từ khoá `["jwt", "json web token", "api key"]`: API key là chuỗi bí mật cố định; JWT là token có chữ ký số, chứa thời điểm hết hạn, server chỉ cần kiểm tra chữ ký |

**Nguyên nhân sai:** không có tài liệu nào trong `KNOWLEDGE_BASE` khớp câu hỏi, nên retrieval trả rỗng và v2 rơi về câu trả lời chung chung ("Câu hỏi hay! ...", score = 0). Đây là lỗi **thiếu dữ liệu** (coverage), không phải lỗi của model.

**Tránh regression:** retrieval chỉ lấy 1 tài liệu khớp nhiều từ khoá nhất (`top_k=1`). Trước khi thêm, mình kiểm tra từng từ khoá trên cả 23 câu hỏi: `hpa`, `kubernetes`, `jwt`, `api key` chỉ xuất hiện ở đúng câu cần sửa. Riêng `scale` có xuất hiện cả ở `scaling-01`, nên **không dùng** từ này, để tài liệu HPA không "cướp" retrieval của câu đang đúng. Kết quả: 2 câu được cải thiện, 0 regression. Đã chép cùng phần sửa sang `production/utils/mock_llm.py` để stack Docker cũng đạt 100%.

**Quality gate trong CI:** [.github/workflows/eval.yml](.github/workflows/eval.yml) chạy khi có pull request đụng tới `develop/`:
1. Bật agent v2 với `MOCK_ERROR_RATE=0`, để gate không fail ngẫu nhiên vì 503.
2. Chạy `run_eval.py --min-accuracy 0.85 --baseline reports/eval_v2_fixed.json`.
3. `run_eval.py` trả **exit code 1** khi accuracy dưới ngưỡng hoặc có câu regression. Khi đó step fail, job fail, và nếu bật branch protection "require status checks" thì PR không merge được.

Đã chạy thử đúng các lệnh của job ở local: bản v2 hiện tại cho exit code **0** (23/23, 0 regression). Bản giả lập PR làm hỏng chất lượng (chạy v1 trên cùng cổng) cho exit code **1** (4.3%, Quality gate ❌ FAIL). Lệnh của Bài tập 4 (`run_eval.py --url http://localhost:8001 --baseline reports/eval_v2.json`) cũng cho exit code **1**, báo 20 câu regression.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**

- **Chất lượng:** v2 có retrieval (RAG) nên accuracy tăng từ 4.3% lên 91.3%, rồi 100% sau khi bổ sung tài liệu.
- **Chi phí mỗi câu:** prompt của v2 chứa thêm tài liệu nên **token mỗi câu tăng 1.9 lần** (57.6 lên 108.8) và **tổng cost tăng 1.84 lần**.
- **Chi phí mỗi câu đúng:** đây là chỉ số nên dùng để so sánh. v1 gần như không trả lời đúng câu nào, nên mỗi câu đúng tốn 0.00041 USD. Với v2 con số này là 0.000036 USD, **rẻ hơn khoảng 11.4 lần**. Sau khi sửa KB, token tăng thêm 5.4% nhưng cost mỗi câu đúng còn giảm tiếp (0.00003475 USD). "Cost / câu" chỉ cho thấy v2 đắt hơn; "cost / câu đúng" cho thấy v2 hiệu quả hơn nhiều.
- **Latency:** v2 thêm bước retrieval (khoảng 20 ms) và prompt dài hơn (+0.2 ms mỗi token input), nên p50 tăng khoảng 30 ms (163.6 lên 196.6 ms). Mức tăng này nhỏ so với SLO 500 ms. p95 của eval v2 (1530.9 ms) cao chủ yếu vì 2 trong 23 câu rơi đúng vào nhóm request chậm của mock. Với mẫu 23 câu, một request chậm là đủ làm p95 nhảy vọt, nên **latency phải đo bằng load test, không đo bằng eval**.
- **Kết luận:** v2 đáng triển khai. Đổi lại khoảng 2 lần token và +30 ms, accuracy tăng hơn 20 lần và chi phí trên mỗi câu đúng giảm hơn 10 lần.

---

## 4. Observability stack (production)

Stack chạy bằng `docker compose -p k5-day14-observability ...` trong `production/`: agent (1 hoặc 3 instance), Nginx, Prometheus, Grafana, Jaeger, Locust. Locust chạy liên tục 30 user để dashboard luôn có tải.

**Hai chỗ phải sửa để stack chạy được trên máy này:**

1. **Lỗi trong `production/Dockerfile` của đề.** `useradd -d /app agent` đặt HOME của user là `/app`, nên Python tìm package ở `/app/.local`, trong khi package được copy vào `/home/agent/.local`. Container agent liên tục crash với `ModuleNotFoundError: No module named 'uvicorn'`. Đã sửa bằng một dòng `ENV PYTHONUSERBASE=/home/agent/.local` (Part 5 cũng dùng cách này). Kiểm chứng: `site.USER_SITE` đổi từ `/app/.local/...` sang đúng thư mục, và agent chuyển sang trạng thái healthy.
2. **Image `grafana/grafana:13.2.4` bị hỏng trên máy.** Ổ C: đầy giữa lúc pull, nên file chạy của Grafana bị cắt cụt còn đúng 48 MiB (50.331.648 byte). Grafana thoát với mã 139 (segfault). Pull lại không sửa được vì Docker tái dùng layer hỏng trong kho nội bộ. Đã chạy Grafana bằng bản vá liền trước `13.2.3` (file chạy đủ 522 MB) qua một file compose override cục bộ, không sửa `docker-compose.yml`. Ổ đĩa ảo của Docker được chuyển sang ổ F:.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải: [screenshots/grafana-dashboard.png](screenshots/grafana-dashboard.png)
- [x] Một trace chậm trong Jaeger: [screenshots/jaeger-slow-trace.png](screenshots/jaeger-slow-trace.png)
- [x] Một trace lỗi trong Jaeger (có event `exception`): [screenshots/jaeger-error-trace.png](screenshots/jaeger-error-trace.png)
- [x] Trang Alerts của Prometheus, **2 alert cùng firing**: [screenshots/prometheus-alert-firing.png](screenshots/prometheus-alert-firing.png)

**Trả lời Exercise 7.4**, số đo bằng PromQL lúc 20:52 (30 user Locust, 3 instance):

1. **p95 và avg:** p95 = **294 ms**, avg = **233 ms**, p50 = 178 ms. Avg thấp hơn p95 không nhiều vì ở tải này chỉ khoảng 3% request rơi vào nhóm chậm 1.7 s của mock LLM (`MOCK_SLOW_RATE=0.03`). Nhóm này nằm ngoài top 5% nên p95 không thấy, nhưng p99 trên dashboard luôn ở khoảng 1.7–1.8 s. Khoảng cách giữa avg (hoặc p50) và p99 chính là tail latency: phần lớn người dùng được trả lời trong khoảng 200 ms, nhưng cứ 100 người thì vài người chờ gần 2 giây. Average và p95 đều không phản ánh được nhóm này.
2. **Nginx có chia đều không:** có. `sum by (instance) (rate(http_requests_total{route="/ask"}[1m]))` cho ra **5.62 / 5.84 / 5.78 req/s** trên 3 instance (chênh lệch dưới 4%). Panel "Requests/s theo instance" trong ảnh Grafana cho thấy lần benchmark 1 instance chỉ có một đường, còn lần 3 instance có 3 đường chồng lên nhau. Nginx chia theo round-robin, kết hợp `server agent:8000 resolve` (hỏi lại DNS mỗi 10 giây nên tự thấy instance mới).
3. **Input tokens/s lớn hơn output tokens/s:** đo được **input 1333 tokens/s, output 705 tokens/s**. Prompt của v2 gồm system prompt, **tài liệu retrieve được (context)** và câu hỏi, trong khi output chỉ là câu trả lời ngắn. Đây là đặc trưng của RAG: context làm prompt dài ra. Sang thí nghiệm E (v1, không có context), input giảm còn khoảng 650 tokens/s.
4. **`curl http://localhost:8080/metrics`:** trả **`404 Not Found`** từ Nginx (`Server: nginx/1.28.3`). Trong `nginx/nginx.conf` có `location = /metrics { return 404; }`. `/metrics` để lộ thông tin nội bộ: route, lưu lượng, chi phí, phiên bản, số instance. Prometheus không cần đi qua Nginx: nó scrape **trực tiếp** từng container agent trong mạng nội bộ của Docker, tìm agent bằng `dns_sd_configs`.

### 4.2 Thí nghiệm "phá hệ thống"

Mỗi thí nghiệm áp dụng lúc Locust đang chạy 30 user, ghi thời gian bằng cách hỏi `GET /api/v1/alerts` mỗi 5 giây. Sau mỗi thí nghiệm đều khôi phục cấu hình mặc định.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (giá trị 21.2%) | pending +31 s, **firing +94 s** | Error rate (Golden Signals), "Requests/s theo status code" (đường 503 tăng), "LLM calls theo kết quả" (error) |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` | pending +20 s, **firing +142 s** | "Latency /ask — p50/p95/p99": p95 vượt đường 1 s, trong khi avg và p50 tăng ít hơn nhiều |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` | pending +30 s, **firing +152 s** | "Cost burn rate (USD/giờ)": lên **$32.4/giờ** (rate 1m), so với khoảng $2/giờ lúc bình thường. "LLM latency p95": 198 lên **497 ms** (khoảng 2.5 lần) |
| D | `docker pause k5-day14-observability-agent-1` | `AgentInstanceDown` (instance `172.21.0.9:8000`) | pending +7 s, **firing +39 s** | "Instances up": 3 xuống 2 |
| E | `AGENT_VERSION=v1` | **Không có** (theo dõi 3 phút) | — | "Tokens/s": input 1350 xuống khoảng 650. "Phiên bản đang chạy": `v1=3`. Error rate và p95 vẫn xanh, p95 còn **giảm** từ 294 xuống 199 ms |

**Ghi chú:**

- **Thời gian tới lúc firing = thời gian điều kiện thành đúng + `for`.** A: `rate[1m]` cần khoảng 30 giây gom đủ lỗi sau khi container được tạo lại, cộng `for: 1m`. B và C: `for: 2m`. D: `for: 30s` cộng chu kỳ scrape 5 giây. Vì vậy D báo nhanh nhất, B và C chậm nhất. `for` là đánh đổi giữa tốc độ phát hiện và báo động giả.
- **Về ảnh `prometheus-alert-firing.png`:** hai alert đang firing là `AgentHighErrorRate` (do thí nghiệm A) và `LLMCostBurnRateHigh`. Alert chi phí **firing từ trước khi làm thí nghiệm** (từ 20:40:57, lúc benchmark 100 user đẩy chi phí lên khoảng $4.5/giờ, vượt ngưỡng $2/giờ). Lúc chụp ảnh nó vẫn firing với giá trị $2.08/giờ do tải 30 user. Ngưỡng $2/giờ thấp so với tải benchmark, nên production thật cần đặt ngưỡng theo ngân sách thực tế. Thí nghiệm C tạo ra lại alert này một cách có chủ đích.
- **D và lỗi ở tầng Nginx:** trong 2 thí nghiệm có tạo lại container (A và lúc khôi phục cấu hình), log Nginx có **234 và 241 lỗi 502** (agent đang khởi động lại, kết nối bị từ chối). Nhưng metric phía agent chỉ thấy mã 200 và 503, **không thấy một lỗi 502 nào**. Metric chỉ đo những gì chính service đó nhận và xử lý. Request bị lỗi ở Nginx (502, hoặc 504 khi agent bị pause quá `proxy_read_timeout 10s`) không bao giờ tới được agent. Muốn thấy loại lỗi này, cần giám sát ở tầng load balancer (metric hoặc log của Nginx) hoặc synthetic check từ bên ngoài.
- **Dùng `docker pause` thay vì `docker stop`:** container bị stop sẽ biến mất khỏi Docker DNS, nên Prometheus coi target đó không còn tồn tại (không phải "down") và không có alert. Container bị pause vẫn có trong DNS nhưng không trả lời, nên `up == 0`.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Đổi sang v1 là **sự cố chất lượng**, không phải sự cố hạ tầng. Request vẫn trả 200, còn nhanh hơn và rẻ hơn, nên mọi Golden Signal đều xanh. Accuracy thì tụt từ 100% xuống 4.3% (mục 3). Cách phát hiện:

1. **Chặn trước khi deploy:** chạy eval trên golden dataset làm quality gate trong CI (`.github/workflows/eval.yml`). Một PR làm agent tụt về v1 sẽ fail ngay (đã thử: exit code 1).
2. **Giám sát chất lượng khi đang chạy:**
   - Chạy eval định kỳ (synthetic eval) lên production.
   - Lấy mẫu câu trả lời thật để chấm bằng LLM-as-judge hoặc đối chiếu keyword.
   - Thu thập phản hồi người dùng (👍/👎, tỉ lệ hỏi lại).
   - Đặt alert trên các metric chất lượng này.
3. **Metric gián tiếp:**
   - Alert khi retrieval hit ratio giảm đột ngột, hoặc khi `agent_info{version}` khác với phiên bản mong đợi.
   - Alert khi số token mỗi request thay đổi bất thường, ví dụ ở đây input tokens/s giảm một nửa dù traffic không đổi.

### 4.3 Debug bằng trace

Chọn trace chậm nhất **khớp với log**: lấy 500 trace `POST /ask` dài hơn 1.5 s qua Jaeger API, so thời lượng span gốc với trường `ms` trong log JSON có cùng `trace_id`.

- `trace_id`: `b374ce6cde7551955b4919b89aa92b79` (ảnh: [jaeger-slow-trace.png](screenshots/jaeger-slow-trace.png))
- Tổng thời gian: **2071.6 ms**. Log ghi `"ms": 2070.0`, khớp.
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small` (1666.3 ms, 80.4%)**, model `mock-llm-small`, 76 token input và 39 token output. Đây là một request "chậm bất thường" của mock LLM (cộng thêm 1500 ms), nghĩa là chậm do LLM chứ không do code của agent.
- Chi tiết các span:

  | Span | ms | % |
  |---|---:|---:|
  | `POST /ask` | 2071.6 | 100% |
  | `agent.run` | 1766.5 | 85.3% |
  | `agent.retrieve_context` | 100.0 | 4.8% |
  | `chat mock-llm-small` | 1666.3 | 80.4% |

  Còn khoảng **305 ms giữa lúc `POST /ask` bắt đầu và lúc `agent.run` bắt đầu**, và `retrieve_context` mất 100 ms, trong khi bình thường chỉ khoảng 20 ms. Trace này thuộc lần benchmark **1 instance, 100 user** (20:47:50). Request đã phải **xếp hàng trong event loop** của instance duy nhất trước khi được xử lý. Đây là bằng chứng trực tiếp cho kết luận ở mục 5.

- Dòng log tương ứng (`docker compose -p k5-day14-observability logs agent | grep b374ce6cde7551955b4919b89aa92b79`):

```json
{"ts": "2026-11-26T13:47:50.586529+00:00", "level": "INFO", "service": "observable-agent", "instance": "71b6a2c9580f", "event": "llm_call", "trace_id": "b374ce6cde7551955b4919b89aa92b79", "span_id": "c979debc388ee113", "request_id": "dc8080110146", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 76, "output_tokens": 39, "cost_usd": 3.5e-05, "llm_ms": 1666.3}
{"ts": "2026-11-26T13:47:50.588013+00:00", "level": "INFO", "service": "observable-agent", "instance": "71b6a2c9580f", "event": "http_request", "trace_id": "b374ce6cde7551955b4919b89aa92b79", "span_id": "c979debc388ee113", "request_id": "dc8080110146", "method": "POST", "route": "/ask", "status": 200, "ms": 2070.0}
```

**Trace lỗi** ([jaeger-error-trace.png](screenshots/jaeger-error-trace.png)):
- `trace_id`: `df8a604f7453579fd7c5e466e0ef672c`, tổng 99.5 ms, `POST /ask` trả **503**.
- Span `chat mock-llm-small` có `error.type = upstream_unavailable` và **event `exception`**: `utils.mock_llm.LLMError: Upstream LLM error: 503 Service Unavailable (mock)`, kèm stack trace.
- Request lỗi kết thúc nhanh (75.5 ms ở bước LLM), vì mock trả lỗi sau khoảng nửa thời gian bình thường.

Log tương ứng:

```json
{"ts": "2026-11-26T13:54:15.859650+00:00", "level": "INFO", "service": "observable-agent", "instance": "b300926a750d", "event": "llm_error", "trace_id": "df8a604f7453579fd7c5e466e0ef672c", "span_id": "48c711124fb84494", "request_id": "8731492be204", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

**Một phát hiện về độ tin cậy của số đo:** trace chậm nhất theo Jaeger (`a28058c3…`) có thời lượng **3575.7 ms**, nhưng log của chính request đó ghi `ms: 1702.4` và `llm_ms: 1673.9`. So sánh cả 499 trace chậm có log: 470 trace khớp (lệch dưới 20 ms), **29 trace đều bị đội thêm khoảng 1.6–1.9 s**. Span của trace tính theo đồng hồ hệ thống (wall clock), còn `ms` trong log đo bằng `perf_counter` (đồng hồ đơn điệu). Giả thuyết hợp lý nhất là đồng hồ hệ thống của VM Docker/WSL bị **chỉnh nhảy** khi máy thiếu tài nguyên; mình chưa xác minh được chắc chắn. Vì vậy báo cáo dùng trace khớp với log, và rút ra bài học: luôn đối chiếu trace với log hoặc metric trước khi kết luận từ một con số duy nhất.

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

Nguồn: [benchmark-results/scale_1_stats.csv](benchmark-results/scale_1_stats.csv), [benchmark-results/scale_3_stats.csv](benchmark-results/scale_3_stats.csv), dòng `/ask`. Cột RPS ghi số của `/ask`, kèm tổng (Aggregated, gồm cả `/health`) trong ngoặc.

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 56.30 (62.03) | 200 | 610 | 1700 | 61 / 7006 (0.87%), đều là 503 của mock |
| 3 | 56.93 (62.61) | 200 | 240 | 1700 | 75 / 7156 (1.05%), đều là 503 của mock |

**Chạy lại vì lệnh trong CHECKPOINTS bị lỗi:** lần chạy đầu dùng đúng lệnh `docker compose ... run --rm locust ...`. Lịch sử `count(up{job="agent"}==1)` trong Prometheus cho thấy số instance bị đưa về **2** ngay sau khi Locust khởi động, ở cả lần "scale 1" (từ 20:39:45) lẫn "scale 3" (từ 20:43:30). Lý do: `compose run` khởi động lại các service mà locust phụ thuộc (nginx, rồi agent) theo `deploy.replicas: 2` trong file, ghi đè lên `--scale`. Đã chạy lại với `run --rm --no-deps`, và theo dõi số target mỗi 10 giây: giữ đúng **1** và đúng **3** suốt 2 phút. Hai file CSV nộp kèm là của lần chạy lại.

**Giải thích kết quả:**

- **RPS gần như không đổi (khoảng 62 req/s)** vì đây là **tải dạng vòng kín** (closed-loop). Mỗi user Locust nghỉ 0.5–2 s giữa các request (`wait_time = between(0.5, 2)`, trung bình 1.25 s), nên 100 user chỉ tạo tối đa khoảng 100 ÷ (1.25 + 0.25) ≈ 66 req/s, bất kể server nhanh tới đâu. Hệ thống không bị giới hạn ở throughput, nên thêm instance không làm tăng RPS.
- **p95 cải thiện rõ: 610 xuống 240 ms (−61%).** Với 1 instance, request phải **xếp hàng** trong một event loop duy nhất (container bị giới hạn `cpus: 0.5`). Bằng chứng từ Prometheus ở giữa mỗi lần chạy:

  | | 1 instance (20:47) | 3 instance (20:50) |
  |---|---|---|
  | HTTP p95 `/ask` | 663 ms | 292 ms |
  | LLM p95 | 198 ms | 198 ms |
  | In-flight tối đa | 19 | 7 |
  | CPU mỗi instance | 0.165 core | khoảng 0.077 core |

  Latency của LLM không đổi. Phần tăng thêm (khoảng 465 ms ở p95) nằm **bên trong agent, trước và quanh lời gọi LLM**. Trace ở mục 4.3 cho thấy đúng khoảng chờ 305 ms đó. CPU trung bình 0.165 core vẫn dưới mức giới hạn, nên nguyên nhân là các đợt tải dồn vào một event loop, và có thể thêm việc container bị giới hạn CPU trong những khoảnh khắc ngắn (giả thuyết, chưa đo trực tiếp). Ba instance chia tải nên mỗi event loop chỉ còn khoảng 1/3 số request, và hết xếp hàng.
- **p99 không đổi (1700 ms)**. p99 do khoảng 3% request "chậm bất thường" của **LLM** (cộng thêm 1500 ms, panel "LLM latency p95 theo model" có các đỉnh khoảng 1.7 s) quyết định. Scale ngang agent không làm LLM nhanh hơn, nên **nút thắt của tail latency nằm ở LLM provider**. Muốn giảm p99 cần xử lý ở phía LLM: timeout cùng retry hoặc hedged request, cache, model nhanh hơn, streaming để người dùng thấy token đầu sớm hơn.
- **Failures không đổi (khoảng 1%)**: đều là 503 do mock LLM (`MOCK_ERROR_RATE=0.01`), không liên quan tới số instance.

---

## Phụ lục — Exercise 7.1: Đọc metrics của agent

Sau một request `POST /ask` vào v2, `curl http://localhost:8002/metrics | grep -v "^#"` cho ra:

| Loại | Ví dụ thật trong output | Ý nghĩa |
|---|---|---|
| **Counter** (chỉ tăng) | `http_requests_total{method="POST",route="/ask",status="200"} 1.0`, `llm_tokens_total{model="mock-llm-small",type="input"} 91.0`, `llm_cost_usd_total{model="mock-llm-small"} 4.665e-05` | Đếm dồn từ lúc process khởi động. Dùng `rate()` để ra tốc độ |
| **Gauge** (lên xuống) | `agent_in_flight_requests 0.0`, `agent_info{model="mock-llm-small",version="v2"} 1.0` | Giá trị hiện tại: số request đang xử lý (saturation), và phiên bản đang chạy |
| **Histogram** | `http_request_duration_seconds_bucket{le="0.2",method="POST",route="/ask"} 1.0`, cùng với `_count` và `_sum` | Đếm số lần đo rơi vào từng bucket `le`. `histogram_quantile()` dùng các bucket này để ước lượng p95/p99 |

**Nếu label `route` dùng URL thật (`request.url.path`):** mỗi URL lạ mà bot quét gọi tới (`/wp-admin`, `/.env`, `/a1b2c3`...) sẽ tạo ra **một tổ hợp label mới**, tức thêm time series mới cho `http_requests_total` và **13 series** cho histogram latency (11 bucket, `+Inf`, `_count`, `_sum`). Bot gọi hàng nghìn URL ngẫu nhiên là thành hàng chục nghìn series. Prometheus phải giữ tất cả trong RAM và index, nên tốn bộ nhớ, query chậm, có thể bị OOM. Đây là lỗi **cardinality bùng nổ**, mà kẻ tấn công còn chủ động gây ra được. Dùng route template thì mọi URL không khớp route nào đều gom vào `route="unmatched"`. Đã kiểm chứng: gọi 3 URL lạ cho ra đúng **một** series `http_requests_total{method="GET",route="unmatched",status="404"} 3.0`.

**Nhiệm vụ: thêm `agent_question_length_chars`.** Thêm vào [develop/app.py](06-lab-complete/benchmark-observability/develop/app.py) một `Histogram` với buckets `(20, 50, 100, 200, 500, 1000, 2000)`, gọi `QUESTION_LENGTH.observe(len(body.question))` ở đầu `/ask`. Dùng Histogram chứ không dùng label `length=...`, vì độ dài có vô số giá trị, mỗi giá trị thành một series. Output sau 1 câu hỏi dài 40 ký tự:

```text
agent_question_length_chars_bucket{le="20.0"} 0.0
agent_question_length_chars_bucket{le="50.0"} 1.0
...
agent_question_length_chars_count 1.0
agent_question_length_chars_sum 40.0
```

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Không bao giờ đánh giá latency bằng average.**
     - Ở mục 2.2, khi tăng `MOCK_SLOW_RATE` từ 0 lên 0.1, avg chỉ tăng 87 ms nhưng p95 tăng 7.6 lần.
     - Ở mục 5, scale lên 3 instance làm p95 giảm 61% nhưng p99 không đổi. Mỗi percentile kể một câu chuyện khác: p95 phản ánh hàng đợi trong agent, p99 phản ánh nhóm request chậm của LLM.
     - SLO phải đặt theo percentile, và phải đọc nhiều percentile cùng lúc mới tìm đúng nút thắt.
  2. **Hạ tầng xanh không có nghĩa là sản phẩm tốt.** Thí nghiệm E làm accuracy tụt từ 100% xuống 4.3% mà dashboard còn "đẹp" hơn. Observability hạ tầng (metric, trace, log) trả lời câu hỏi "chạy có ổn không", còn eval trên golden dataset trả lời câu hỏi "trả lời có đúng không". Cần cả hai, và eval phải là quality gate trong CI.
  3. **Kiểm tra lại công cụ đo trước khi tin số liệu.** Trong lab này, mình gặp ba lần số đo bị sai mà không có cảnh báo gì:
     - `compose run` âm thầm đưa số instance về 2 trong lúc benchmark;
     - 29 trace bị đội thêm khoảng 1.7 s, có lẽ do đồng hồ hệ thống nhảy;
     - `load_test.py` ở c=100 cho kết quả dao động 62–120 RPS giữa các lần chạy.

     Mỗi lần đều phải đối chiếu chéo (Prometheus với CSV, trace với log, chạy lại nhiều lần) mới phát hiện được. Metric, log và trace có giá trị nhất khi **liên kết được với nhau** qua `trace_id`.
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?
  - **Giám sát chất lượng:**
    - Chạy eval định kỳ lên production và đặt alert khi accuracy tụt dưới SLO.
    - Metric phản hồi người dùng (👍/👎).
    - Alert khi `agent_info{version}` khác với phiên bản mong đợi, hoặc khi retrieval hit ratio giảm.
  - **Giám sát ở tầng load balancer và từ bên ngoài:**
    - Metric hoặc log của Nginx (tỉ lệ 502/504, upstream response time).
    - Synthetic check gọi `/ask` từ bên ngoài, để bắt được loại lỗi mà metric của agent không thấy (thí nghiệm D).
  - **Alert theo error budget burn rate** (nhiều cửa sổ thời gian, ví dụ 5 phút và 1 giờ) thay cho ngưỡng cố định, để vừa bắt sự cố nhanh vừa ít báo động giả.
  - **Alert về chi phí theo ngân sách thật** (theo ngày hoặc tháng, như cost guard ở Part 6), kèm chi phí trên mỗi câu trả lời đúng. Ngưỡng $2/giờ hiện tại đã firing ngay ở tải benchmark bình thường.
  - **Metric bão hoà chi tiết hơn:** độ trễ của event loop, số lần container bị giới hạn CPU, độ dài hàng đợi. Mục 5 cho thấy nút thắt của p95 nằm ở đây mà dashboard hiện không có panel nào thể hiện trực tiếp.
  - **Alert khi p99 hoặc timeout của LLM tăng**, tách riêng khỏi latency HTTP, vì p99 do LLM provider quyết định.
