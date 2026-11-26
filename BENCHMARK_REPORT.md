# Benchmark & Observability Report — Day 14

> Mọi con số trong báo cáo lấy từ report JSON trong [reports/](reports/) (bản gốc ở `06-lab-complete/benchmark-observability/develop/reports/`), CSV trong [benchmark-results/](benchmark-results/) và screenshot trong [screenshots/](screenshots/).

**Họ tên:** Phạm Quang Dũng — 2A202603896
**Ngày chạy:** 2026-11-26
**Môi trường:** Windows 11 + WSL2 Ubuntu 24.04 (16 vCPU, 7 GB RAM cấp cho WSL), Python 3.12.3. Agent (`develop/app.py`, 1 process uvicorn) và `load_test.py` chạy chung một máy, giao tiếp qua `localhost`. Docker Desktop 29.3.1 cho phần production.

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

> Làm ở CP8 (Part 7 Bước 2).

### 4.1 Screenshot

- [ ] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals)
- [ ] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian)
- [ ] Một trace lỗi trong Jaeger (có event `exception`)
- [ ] Trang Alerts của Prometheus với ít nhất 1 alert đang **firing**

### 4.2 Thí nghiệm "phá hệ thống"

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | | | |
| B | `MOCK_SLOW_RATE=0.3` | | | |
| C | `LLM_MODEL=mock-llm-large` | | | |
| D | `docker pause` 1 instance | | | |
| E | `AGENT_VERSION=v1` | | | |

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

...

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất bạn tìm được:

- `trace_id`: ...
- Tổng thời gian: ... ms
- Span chiếm nhiều thời gian nhất: ... (... ms, ...%)
- Dòng log tương ứng (grep theo `trace_id`):

```json

```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

> Làm ở CP8.

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | | | | | |
| 3 | | | | | |

**Giải thích kết quả:** Scale có cải thiện p95 không? Nút thắt nằm ở đâu?

...

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

> Hoàn thiện sau CP8.

- 3 điều bạn học được về benchmark & observability:
  1. ...
  2. ...
  3. ...
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?

...
